import datetime
import json

import pandas as pd
import streamlit as st

from core.validator import SCMDataValidator
from core.tender_registry import (
    apply_correction, archive_tender, correct_tender_metadata, get_correction_history,
    get_effective_escalation_price, get_effective_tender_field, get_tender,
    has_tender_activity, list_tenders, log_check, record_escalation,
    resolve_effective_tender, save_tender, tender_exists, update_tender_metadata_direct,
    METADATA_CORRECTABLE_FIELDS,
)
from utils.document_gen import build_audit_pdf

# Municipal SCM Audit Chatbot -- standalone feature (see CHATBOT_README.md).
# Imported here only to power the "SCM Audit Chatbot" sidebar mode below;
# nothing in the Tender Registry flow above depends on it. The dashboard mode
# shows only the deterministic scm_parser.py verification result (no Ollama
# LLM report step) -- see run_demo.py / ollama_bot.py for that, in the terminal.
import scm_parser

# Municipal SCM Authentication Module -- provides login/signup and role-based access control
from auth_module import render_auth_interface

# Tender History Export Module -- generates combined audit trail PDFs and JSONs
from utils.tender_history_export import download_tender_history

ARCHIVE_PATH = "data/stats_sa_cpi_archive.csv"

# Set up clean, institutional dark theme dashboard configuration
st.set_page_config(page_title="Lekwankwa SCM Engine", layout="wide")

# ============================================================================
# AUTHENTICATION GATE
# ============================================================================
# Check authentication state first. If user is not logged in, show auth interface
# and exit (don't render the main app). If authenticated, continue to main app.

from auth_module import init_session_state
init_session_state()

if not st.session_state.authenticated:
    # User is not authenticated - render login/signup interface
    render_auth_interface()
    st.stop()  # Stop execution; don't render main app

# ============================================================================
# MAIN APP (Only reached if user is authenticated)
# ============================================================================

st.title("Municipal SCM Governance Engine")
st.subheader("Data-Driven Procurement Protection and Internal Audit Control Gateway")
st.markdown("---")


def _current_approver_display() -> str:
    """'First Last (Role)' for the currently authenticated user (guaranteed
    set -- the AUTHENTICATION GATE above already st.stop()s otherwise).
    Used to auto-fill the Tender Registry's approval gates below instead of
    asking someone who already logged in to retype their own name and role.
    """
    user = st.session_state.current_user
    return f"{user['first_name']} {user['surname']} ({user['role']})"


def _months_since_anchor(anchor_month: str, month: str) -> int:
    anchor_dt = datetime.datetime.strptime(anchor_month, "%Y-%m")
    m_dt = datetime.datetime.strptime(month, "%Y-%m")
    return (m_dt.year - anchor_dt.year) * 12 + (m_dt.month - anchor_dt.month)


def _anniversary_months(anchor_month: str, available_months: list) -> list:
    """Months that are strict 12-month multiples AFTER anchor_month (not the
    anchor month itself -- it isn't an anniversary of itself, and Task 3's
    escalation-eligibility check reuses this same function, where checking
    the anchor month against itself must not count as an eligible target).

    Used to both label these dates in the check-month picker and to gate
    whether "Run Annual Escalation" is offered for the selected month.
    """
    result = []
    for m in available_months:
        months_diff = _months_since_anchor(anchor_month, m)
        if months_diff > 0 and months_diff % 12 == 0:
            result.append(m)
    return result


def _formula_reference_point(tender: dict) -> tuple:
    """(anchor_month, anchor_cpi, base_price) to use as the drift reference
    for a tender, per its CPA formula type -- the single source of truth
    used identically by "Run Monthly Check" AND "Calculate/Confirm Annual
    Escalation", so a monthly preview is never inconsistent with what the
    next escalation would actually compute for the same tender:

      CUMULATIVE_FROM_ORIGINAL -- always the permanent original anchor/base,
          regardless of how many escalations have happened since. A monthly
          check is a live preview of "what would the next escalation compute
          right now" -- it was a real bug for this to instead measure drift
          from the CURRENT (rolled) anchor after an escalation, which would
          silently double-count the prior escalation's effect.
      COMPOUND_FROM_PRIOR_YEAR -- the current (rolled) anchor/price, i.e.
          whatever the last approved escalation left in effect. This one
          was already correct.

    Before any escalation has happened, original_* and current_* are
    identical, so this makes no difference either way.

    Resolves to the EFFECTIVE original_base_value / cpa_formula_type first
    (resolve_effective_tender()) rather than the raw stored fields -- so a
    metadata correction to either one (see core/tender_registry.py's
    correct_tender_metadata()) automatically applies to every future
    calculation through this one function, without needing to be threaded
    through each of this function's three callers individually. This is the
    same class of fix as the original bug this function was written to
    solve: one source of truth instead of scattered call sites.
    """
    tender = resolve_effective_tender(tender)
    if tender.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL") == "CUMULATIVE_FROM_ORIGINAL":
        return tender["original_anchor_month"], tender["original_anchor_cpi"], tender["original_base_value"]
    return tender["current_anchor_month"], tender["current_anchor_cpi"], tender["current_adjusted_price"]


def _format_metadata_value(field: str, value) -> str:
    """Currency formatting for original_base_value, plain string for every
    other correctable metadata field -- a single f"{v:,.2f}" applied
    uniformly would crash/misrender for tender_name/start_date/
    end_date/cpa_formula_type."""
    if field == "original_base_value":
        try:
            return f"R{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _month_label(month: str, anchor_month: str, anniversary_months: set) -> str:
    if month in anniversary_months:
        year_n = _months_since_anchor(anchor_month, month) // 12
        return f"Year {year_n} Anniversary - {month}"
    return month


def render_result_block(tender_id, tender_name, baseline_type, base_value,
                          start_date_str, end_date_str, timeline_results=None,
                          stage_results=None, extras=None, output_hash="N/A",
                          document_title="Audit-Ready Compliance Record",
                          escalation_info=None, correction_detail=None,
                          correction_history=None, metadata_correction_detail=None,
                          archive_detail=None, full_escalation_history=None):
    """Shared output rendering for the anchor event and every later
    monthly/annual check/escalation/correction -- metrics, the real
    10-stage panel, the results table, and the PDF/JSON downloads. Used
    identically everywhere so no action needs its own separate rendering
    path.

    document_title / escalation_info / correction_detail: different
    purpose, different legal weight, different downstream effect on the
    contract -- see utils/document_gen.py's build_audit_pdf() docstring
    for the full explanation.

    timeline_results/stage_results are None for a pure correction record
    (a correction is a business/audit action, not a new CPI calculation --
    nothing runs the 10-stage gate for one) -- the metrics/stage-panel/
    lineage-table sections are skipped gracefully in that case.

    correction_history, when non-empty, is shown on EVERY result for a
    tender that has ever had a correction applied -- not just the
    correction's own record -- per the "never just the final number with
    no trace of what changed" requirement.

    metadata_correction_detail: same "just applied" notice pattern as
    correction_detail, but for a tender-metadata correction (see
    core/tender_registry.py's correct_tender_metadata()).

    archive_detail: {reason, archived_by, archived_at, just_archived}. When
    just_archived is True this is "you just archived this tender" (a
    formal, prominent notice); when False it's "you are viewing an already-
    archived tender" (a calmer informational one), used by the Archived /
    Closed Contracts browse mode.

    full_escalation_history: the tender's COMPLETE escalation_history (every
    year, not just the latest) -- only passed by the Archived / Closed
    Contracts browse view, so routine check/escalation renders are visually
    unchanged.
    """
    extras = extras or {}
    correction_history = correction_history or []

    if escalation_info:
        approval_line = ""
        if escalation_info.get("approved_by"):
            approval_line = (f" Approved by {escalation_info['approved_by']} "
                              f"on {escalation_info.get('approved_at', 'N/A')}.")
        st.warning(
            "This is a formal annual price adjustment. Effective "
            f"{escalation_info['effective_date']}, the contract's baseline value "
            f"is revised from R{escalation_info['old_base_zar']:,.2f} to "
            f"R{escalation_info['new_base_zar']:,.2f}.{approval_line}"
        )

        # Full derivation chain, per the CPA formula requirement: the
        # original tender baseline stays visible and unchanged, the prior
        # year's adjusted price shows for a compounding tender, and the
        # formula used is explicit -- so the whole chain from original
        # tender to current price is auditable in one document.
        is_compound = escalation_info.get("formula_type") == "COMPOUND_FROM_PRIOR_YEAR"
        derivation_lines = [
            f"Formula Type Used: {escalation_info.get('formula_type', 'N/A')}",
            f"Original Baseline (fixed against escalation rollups, "
            f"{escalation_info.get('original_anchor_month', 'N/A')}): "
            f"CPI {escalation_info.get('original_anchor_cpi', 'N/A')}, "
            f"Base R{escalation_info.get('original_base_zar', 0):,.2f}",
        ]
        if is_compound:
            derivation_lines.append(
                f"Prior Year Adjusted Price ({escalation_info.get('prior_anchor_month', 'N/A')}): "
                f"R{escalation_info.get('prior_adjusted_price', 0):,.2f}"
            )
        derivation_lines.append(
            f"This Year's New Adjusted Price ({escalation_info['effective_date']}): "
            f"R{escalation_info['new_base_zar']:,.2f}"
        )
        st.info("\n\n".join(derivation_lines))

    if correction_detail:
        cd = correction_detail
        cascade_line = f" Cascade mode: {cd['cascade_mode']}." if cd.get("cascade_mode") else ""
        st.error(
            f"This is a formal correction to the approved {cd['year_month']} annual escalation "
            f"record. The figure is revised from R{cd['original_figure']:,.2f} to "
            f"R{cd['corrected_figure']:,.2f}. Reason: {cd['reason']}. Approved by "
            f"{cd['corrected_by']} on {cd.get('corrected_at', 'N/A')}.{cascade_line}"
        )

    if metadata_correction_detail:
        mcd = metadata_correction_detail
        notice = (
            f"This is a formal correction to tender metadata. Field '{mcd['field']}' revised "
            f"from '{mcd['original_value']}' to '{mcd['corrected_value']}'. Reason: {mcd['reason']}. "
            f"Approved by {mcd['corrected_by']} on {mcd.get('corrected_at', 'N/A')}."
        )
        # Amber/warning-toned when this correction touches an already-relied-on
        # calculation input (see correct_tender_metadata()'s retroactive_impact_flag) --
        # "flag for human review" tone, distinct from a routine correction's tone.
        if mcd.get("retroactive_impact_flag"):
            st.warning(notice + f" ⚠ {mcd.get('retroactive_impact_note', '')}")
        else:
            st.error(notice)

    if archive_detail:
        ad = archive_detail
        notice = (
            f"This tender is ARCHIVED. Reason: {ad['reason']}. Archived by "
            f"{ad['archived_by']} on {ad['archived_at']}. It is hidden from the active "
            "'Open Existing Tender' working list; its full history remains fully "
            "retrievable via 'Archived / Closed Contracts'."
        )
        if ad.get("just_archived"):
            st.error(notice)
        else:
            st.info(notice)

    # METRIC DISPLAY MATRIX (Top Layer Visualization) -- only for a
    # CPI-driven record (anchor / monthly check / escalation); a pure
    # correction record has no CPI calculation to show metrics for.
    if timeline_results:
        inception_data = timeline_results[0]
        latest_data = timeline_results[-1]
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Anchor CPI", f"{inception_data['anchor_cpi']} ({inception_data.get('anchor_month', inception_data['month'])})")
        with col2:
            st.metric("Current Vintage CPI", f"{latest_data['current_cpi']} ({latest_data['month']})")
        with col3:
            final_drift = latest_data["drift_percentage"]
            st.metric("Cumulative Drift %", f"{final_drift:+.4f}%")
        st.markdown("---")

    # 10-STAGE AUTOMATED PIPELINE VISUALIZATION
    # Renders the REAL stage-by-stage results returned by the validator
    # (same 10-stage pattern used across the Lekwankwa data platform:
    # 1a / 1b / 1c / 2 / 3 / 4 / 5 / 6 / 7 / 8), not a static checklist.
    # Skipped for a pure correction record -- nothing runs the gate for one.
    if stage_results:
        overall_ok = all(s["status"] != "FAIL" for s in stage_results)
        if overall_ok:
            st.success("10-Stage Data Integrity Validation Pipeline Status: [PASSED]")
        else:
            st.error("10-Stage Data Integrity Validation Pipeline Status: [FAILED]")

        # Same bracketed [PASS]/[WARN]/[FAIL] convention used in the PDF export,
        # so the on-screen and PDF status language match.
        cols = st.columns(5)
        for i, s in enumerate(stage_results):
            cols[i % 5].markdown(f"**[{s['status']}] Stage {s['stage']} - {s['name']}**")
            with cols[i % 5].expander("detail", expanded=False):
                st.caption(s["detail"])
        st.markdown("---")

    if timeline_results:
        st.subheader("Audit Lineage")
        st.dataframe(pd.DataFrame(timeline_results), use_container_width=True)
        st.markdown("---")

    # Only rendered by the Archived / Closed Contracts browse view -- the
    # COMPLETE escalation_history (every year), not just the latest, so
    # nothing about a closed contract's history is ever hidden.
    if full_escalation_history:
        st.subheader("Full Escalation History")
        display_rows = [
            {
                "Year": e["new_anchor_month"], "Formula": e["formula_type"],
                "Prior CPI": e["prior_anchor_cpi"], "New CPI": e["new_anchor_cpi"],
                "Prior Price (R)": e["prior_adjusted_price"],
                "Approved Price (R)": e["new_adjusted_price"],
                "Effective Price (R)": get_effective_escalation_price(e),
                "Approved By": e["approved_by"], "Approved At": e["escalated_at"],
            }
            for e in full_escalation_history
        ]
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True)
        st.markdown("---")

    if correction_history:
        st.subheader("Correction History")
        st.caption("Shown on every document for this tender, per the requirement that a correction "
                    "is never a silent edit -- the full trail stays visible going forward.")
        for c in correction_history:
            if c["type"] == "CORRECTION":
                cascade_tag = f" ({c['cascade_mode']})" if c.get("cascade_mode") else ""
                st.markdown(f"**{c['year_month']} — Correction{cascade_tag}**")
                st.caption(f"Original: R{c['original_figure']:,.2f} -> Corrected: R{c['corrected_figure']:,.2f} | "
                            f"Reason: {c['reason']} | Approved by: {c['corrected_by']} on {c['corrected_at']}")
            elif c["type"] == "METADATA_CORRECTION":
                flag_tag = " ⚠ FLAGGED FOR REVIEW" if c.get("retroactive_impact_flag") else ""
                st.markdown(f"**Metadata — {c['field']}{flag_tag}**")
                st.caption(f"Original: {_format_metadata_value(c['field'], c['original_value'])} -> "
                            f"Corrected: {_format_metadata_value(c['field'], c['corrected_value'])} | "
                            f"Reason: {c['reason']} | Approved by: {c['corrected_by']} on {c['corrected_at']}")
                if c.get("retroactive_impact_note"):
                    st.caption(c["retroactive_impact_note"])
            else:  # STALE_INPUT_FLAG
                st.markdown(f"**{c['year_month']} — Stale Input Flag**")
                st.caption(c["note"])
        st.markdown("---")

    # OUTPUT GENERATION INTERFACE
    st.subheader("Automated Output Package Registry")
    st.write("Download certified compliance payloads directly below:")

    # Shared tender metadata — both the PDF and the JSON export are built
    # from this same dict + output_hash, so the two files are
    # cryptographically tied to the same run (see document_gen.py).
    tender_metadata = {
        "id": tender_id,
        "name": tender_name,
        "type": baseline_type,
        "base_zar": base_value,
        "start": start_date_str,
        "end": end_date_str,
    }

    gold_standard_json = {
        "record_type": document_title,
        "tender_metadata": tender_metadata,
        "validation_pipeline": stage_results or [],
        "lineage": extras.get("lineage", {}),
        "outliers": extras.get("outliers", []),
        "output_hash": output_hash,
        "audit_lineage": timeline_results or [],
        "escalation": escalation_info,
        "correction": correction_detail,
        "metadata_correction": metadata_correction_detail,
        "archive": archive_detail,
        "full_escalation_history": full_escalation_history,
        "correction_history": correction_history,
    }
    json_bytes = json.dumps(gold_standard_json, indent=4, default=str).encode("utf-8")

    pdf_bytes = build_audit_pdf(
        tender_metadata=tender_metadata,
        stage_results=stage_results,
        timeline_results=timeline_results,
        lineage=extras.get("lineage", {}),
        outliers=extras.get("outliers", []),
        document_title=document_title,
        escalation_info=escalation_info,
        correction_detail=correction_detail,
        correction_history=correction_history,
        metadata_correction_detail=metadata_correction_detail,
        archive_detail=archive_detail,
        full_escalation_history=full_escalation_history,
        output_hash=output_hash,
    )

    # A pure correction/metadata-correction/archive record has no CPI
    # timeline to pull a month tag from for the file name -- fall back
    # through each record type's own natural date, then a plain tag.
    file_month_tag = (
        timeline_results[-1]["month"] if timeline_results
        else correction_detail["year_month"] if correction_detail
        else metadata_correction_detail["corrected_at"][:10] if metadata_correction_detail
        else "archived" if archive_detail
        else "record"
    )
    out_col1, out_col2 = st.columns(2)
    with out_col1:
        st.download_button(
            label="Download Audit-Ready PDF Record",
            data=pdf_bytes,
            file_name=f"Audit_Record_{tender_id}_{file_month_tag}.pdf",
            mime="application/pdf",
        )
    with out_col2:
        st.download_button(
            label="Download Gold Standard Data Export (.json)",
            data=json_bytes,
            file_name=f"DataExport_{tender_id}_{file_month_tag}.json",
            mime="application/json",
        )


def render_metadata_edit_or_correct_block(tender: dict, key_prefix: str) -> dict:
    """Renders EITHER a direct-edit form (zero check/escalation/correction
    activity yet) OR a gated correct-metadata form (has activity), for
    `tender`. Called from both "Open Existing Tender" (active tenders) and
    "Archived / Closed Contracts" (archived ones) -- an archived tender is
    just as correctable as an active one, and a zero-history archived
    tender (archived immediately with no activity) still gets the
    direct-edit form, since has_tender_activity() is independent of status.

    Only renders widgets and returns what the clerk requested this run --
    never mutates the registry itself, matching this file's existing
    convention (sidebar collects, the MAIN FRAME PROCESSING section below
    acts). key_prefix keeps widget keys distinct between the two call
    sites even though only one ever renders per script run (registry_mode
    is a single radio -- only one mode's branch executes at a time).
    """
    result = {
        "trigger_direct_edit": False, "direct_edit_data": None,
        "trigger_preview_correction": False, "preview_data": None,
    }
    effective = resolve_effective_tender(tender)
    # cpa_formula_type is independent of baseline_type -- always editable/
    # correctable here, regardless of Monthly vs Annual (see Fix 2's
    # rationale at the anchor form above).
    formula_labels = {
        "CUMULATIVE_FROM_ORIGINAL": "Cumulative from Original Anchor",
        "COMPOUND_FROM_PRIOR_YEAR": "Compound from Prior Year",
    }

    with st.expander("Edit / Correct Tender Metadata"):
        if not has_tender_activity(tender):
            st.caption("No checks, approved escalations, or prior corrections yet -- "
                        "this tender's metadata can still be edited directly.")
            new_name = st.text_input("Tender / Project Name", value=effective["tender_name"],
                                       key=f"{key_prefix}_meta_name")
            new_start = st.date_input("Contract Start Date (Anchor Month)",
                                        value=datetime.date.fromisoformat(effective["start_date"]),
                                        key=f"{key_prefix}_meta_start")
            new_end = st.date_input("Contract End Date",
                                      value=datetime.date.fromisoformat(effective["end_date"]),
                                      key=f"{key_prefix}_meta_end")
            new_base = st.number_input("Base Contract Valuation (ZAR)", min_value=1.0,
                                         value=float(effective["original_base_value"]), step=1000.0,
                                         key=f"{key_prefix}_meta_base")
            current_formula = effective.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL")
            new_formula = st.radio(
                "CPA Formula Type", ["CUMULATIVE_FROM_ORIGINAL", "COMPOUND_FROM_PRIOR_YEAR"],
                index=["CUMULATIVE_FROM_ORIGINAL", "COMPOUND_FROM_PRIOR_YEAR"].index(current_formula),
                format_func=lambda x: formula_labels[x], key=f"{key_prefix}_meta_formula",
            )

            if new_start >= new_end:
                st.error("Contract End Date must be strictly after the Start Date.")
            elif st.button("Save Changes", key=f"{key_prefix}_meta_save"):
                result["trigger_direct_edit"] = True
                result["direct_edit_data"] = {
                    "tender_id": tender["tender_id"], "tender_name": new_name,
                    "start_date": new_start, "end_date": new_end,
                    "original_base_value": new_base, "cpa_formula_type": new_formula,
                }
        else:
            st.caption("This tender has check/escalation/correction history -- metadata can no "
                        "longer be edited directly. This layers a new, separately dated "
                        "correction on top, same pattern already used for CPI figures.")
            correctable_fields = list(METADATA_CORRECTABLE_FIELDS)
            field_labels = {
                "tender_name": "Tender / Project Name", "start_date": "Contract Start Date",
                "end_date": "Contract End Date", "original_base_value": "Base Contract Valuation (ZAR)",
                "cpa_formula_type": "CPA Formula Type",
            }
            field = st.selectbox("Field to Correct", correctable_fields,
                                   format_func=lambda f: field_labels[f], key=f"{key_prefix}_meta_field")
            current_val = get_effective_tender_field(tender, field)
            st.caption(f"Current effective value: {_format_metadata_value(field, current_val)}")

            if field == "original_base_value":
                new_val = st.number_input("Corrected Value (ZAR)", min_value=0.01,
                                            value=float(current_val), step=1000.0,
                                            key=f"{key_prefix}_meta_newval")
            elif field in ("start_date", "end_date"):
                new_val = str(st.date_input("Corrected Value", value=datetime.date.fromisoformat(current_val),
                                              key=f"{key_prefix}_meta_newval"))
            elif field == "cpa_formula_type":
                new_val = st.radio("Corrected Value", ["CUMULATIVE_FROM_ORIGINAL", "COMPOUND_FROM_PRIOR_YEAR"],
                                     format_func=lambda x: formula_labels[x], key=f"{key_prefix}_meta_newval")
            else:
                new_val = st.text_input("Corrected Value", value=str(current_val), key=f"{key_prefix}_meta_newval")

            reason = st.text_area("Reason for Correction (required)", key=f"{key_prefix}_meta_reason",
                                    placeholder="e.g. data entry error, dispute resolution, audit finding")

            if field in ("original_base_value", "cpa_formula_type") and tender.get("escalation_history"):
                st.warning(f"This tender has {len(tender['escalation_history'])} already-approved "
                            "escalation(s) calculated using the current value. Correcting it will NOT "
                            "retroactively recalculate those records -- flagged for manual review.")

            if st.button("Preview Metadata Correction", key=f"{key_prefix}_meta_preview"):
                result["trigger_preview_correction"] = True
                result["preview_data"] = {
                    "tender_id": tender["tender_id"], "field": field,
                    "corrected_value": new_val, "reason": reason,
                }
    return result


# TENDER REGISTRY (Sidebar UI)
with st.sidebar:
    st.header("Navigation")
    registry_mode = st.radio("Mode", [
        "Anchor New Tender", "Open Existing Tender",
        "Correct Prior Escalation", "Archived / Closed Contracts",
        "SCM Audit Chatbot (Beta)",
    ])
    st.markdown("---")

    trigger_anchor = False
    trigger_check = False
    trigger_calculate_escalation = False
    trigger_preview_correction = False
    trigger_archive = False
    trigger_direct_metadata_edit = False
    trigger_preview_metadata_correction = False
    trigger_view_archived = False
    trigger_chatbot_analysis = False
    chatbot_uploaded_file = None
    selected_tender = None
    check_month = None
    check_month_is_anniversary = False
    correction_tender = None
    correction_year_month = None
    correction_figure = None
    correction_reason = None
    correction_cascade_mode = "ISOLATED"
    archive_target_tender = None
    archive_reason = None
    direct_edit_data = None
    metadata_correction_preview_data = None
    archived_tender = None

    if registry_mode == "Anchor New Tender":
        st.header("Tender Configuration Panel")
        st.write("Input official contract parameters below. This locks the anchor CPI once — every later check reads it back from the registry.")

        tender_id = st.text_input("Tender reference ID / Number", value="TENDER-LP-2025")
        tender_name = st.text_input("Tender / Project Name", value="Limpopo Catering Project")

        baseline_type = st.radio(
            "Contract Structure Baseline Type",
            ["Monthly Base Recurring Invoice Value", "Total Annual Contract Allocation Value"],
        )

        # baseline_type (how the Rand figure is structured) and
        # cpa_formula_type (how escalation compounds over multiple years)
        # are independent fields -- ANY tender, regardless of baseline_type,
        # can run multiple years and reach an annual anniversary, so this is
        # always required, never conditional on which baseline_type was
        # chosen.
        cpa_formula_type = st.radio(
            "CPA Formula Type (set once, permanent for this tender)",
            ["CUMULATIVE_FROM_ORIGINAL", "COMPOUND_FROM_PRIOR_YEAR"],
            format_func=lambda x: {
                "CUMULATIVE_FROM_ORIGINAL": "Cumulative from Original Anchor",
                "COMPOUND_FROM_PRIOR_YEAR": "Compound from Prior Year",
            }[x],
        )

        base_value = st.number_input("Base Contract Valuation (ZAR)", min_value=1.0, value=1000000.0, step=1000.0)

        start_date = st.date_input("Contract Official Execution / Start Date (Anchor Month)", value=datetime.date(2025, 1, 1))
        end_date = st.date_input("Contract Official Expiration / End Date", value=datetime.date(2026, 6, 1))

        st.markdown("---")
        trigger_anchor = st.button("Anchor Tender")

    elif registry_mode == "Open Existing Tender":
        st.header("Open Existing Tender")
        # ARCHIVED tenders are hidden from this day-to-day working list --
        # the one status filter in the whole app. They stay fully visible
        # and correctable via "Archived / Closed Contracts" instead.
        tenders = [t for t in list_tenders() if t.get("status", "ACTIVE") != "ARCHIVED"]
        if not tenders:
            st.info("No active tenders anchored yet. Switch to 'Anchor New Tender' first, "
                      "or check 'Archived / Closed Contracts' if you expected one here.")
        else:
            # Dropdown labels use the EFFECTIVE (post-correction) tender name so a
            # corrected name shows up going forward.
            options = {f"{t['tender_id']} - {resolve_effective_tender(t)['tender_name']}": t["tender_id"]
                       for t in tenders}
            picked_label = st.selectbox("Select Tender", list(options.keys()))
            selected_tender = get_tender(options[picked_label])
            effective_tender = resolve_effective_tender(selected_tender)

            st.caption(f"Baseline: {selected_tender['baseline_type']}")
            st.caption(f"CPA Formula: {effective_tender.get('cpa_formula_type', 'CUMULATIVE_FROM_ORIGINAL')}")
            st.caption(f"Original Anchor (fixed against escalation rollups): {selected_tender['original_anchor_month']} "
                        f"@ CPI {selected_tender['original_anchor_cpi']} "
                        f"(base R{effective_tender['original_base_value']:,.2f})")
            st.caption(f"Current: {selected_tender['current_anchor_month']} "
                        f"@ CPI {selected_tender['current_anchor_cpi']} "
                        f"(adjusted price R{selected_tender['current_adjusted_price']:,.2f})")

            # Every month on/after the CURRENT anchor is selectable, for BOTH
            # baseline types identically -- baseline_type only describes how
            # the Rand figure is structured, not whether a contract can
            # reach an annual anniversary. Any tender is eligible for
            # "Calculate Annual Escalation" on a true anniversary month (see
            # below), regardless of Monthly vs Annual.
            archive_dates = pd.read_csv(ARCHIVE_PATH)["Date"].astype(str).tolist()
            eligible_months = [d for d in archive_dates if d >= selected_tender["current_anchor_month"]]
            anniversary_months = set(_anniversary_months(selected_tender["current_anchor_month"], eligible_months))

            if not eligible_months:
                st.info("No eligible check month yet for this tender.")
            else:
                default_index = len(eligible_months) - 1
                if anniversary_months:
                    latest_anniversary = max(anniversary_months)
                    default_index = eligible_months.index(latest_anniversary)

                check_month = st.selectbox(
                    "Select Check Month", eligible_months, index=default_index,
                    format_func=lambda m: _month_label(m, selected_tender["current_anchor_month"], anniversary_months),
                )
                check_month_is_anniversary = check_month in anniversary_months
                st.markdown("---")

                if check_month_is_anniversary:
                    trigger_check = st.button("Run Monthly Check (preview, no registry change)")
                    trigger_calculate_escalation = st.button("Calculate Annual Escalation (proposal only)")
                else:
                    trigger_check = st.button("Run Monthly Check")

            st.markdown("---")
            meta_result = render_metadata_edit_or_correct_block(selected_tender, key_prefix="active")
            if meta_result["trigger_direct_edit"]:
                trigger_direct_metadata_edit = True
                direct_edit_data = meta_result["direct_edit_data"]
            if meta_result["trigger_preview_correction"]:
                trigger_preview_metadata_correction = True
                metadata_correction_preview_data = meta_result["preview_data"]

            with st.expander("Archive This Tender"):
                st.warning("Archiving is ONE-DIRECTIONAL -- there is no 'Reactivate' action. "
                            "Nothing is deleted; the tender's full history stays fully retrievable "
                            "via 'Archived / Closed Contracts', it just leaves this working list.")
                archive_reason_input = st.text_area(
                    "Reason for archiving (required)", key="archive_reason_input",
                    placeholder="e.g. contract ended, entered in error, superseded",
                )
                if st.button("Archive This Tender", key="trigger_archive_btn"):
                    trigger_archive = True
                    archive_target_tender = selected_tender
                    archive_reason = archive_reason_input

            st.markdown("---")
            st.subheader("📥 Export Complete Tender History")
            st.write("Download the complete audit trail for this tender in one document, "
                     "containing all events (anchor, checks, escalations, corrections) "
                     "in strict chronological order.")
            download_tender_history(selected_tender["tender_id"], selected_tender)

    elif registry_mode == "Correct Prior Escalation":
        st.header("Correct Prior Escalation")
        st.write("An approved annual price is never edited in place. This layers a new, "
                  "separately dated correction on top -- the original approved figure is kept forever.")
        correctable = [t for t in list_tenders() if t.get("escalation_history")]
        if not correctable:
            st.info("No tenders have any approved Annual Escalation yet to correct.")
        else:
            options = {f"{t['tender_id']} - {resolve_effective_tender(t)['tender_name']}": t["tender_id"]
                       for t in correctable}
            picked_label = st.selectbox("Select Tender", list(options.keys()), key="correction_tender_select")
            correction_tender = get_tender(options[picked_label])
            history = correction_tender["escalation_history"]
            year_options = [e["new_anchor_month"] for e in history]

            def _correction_year_label(ym, _history=history):
                entry = next(e for e in _history if e["new_anchor_month"] == ym)
                eff = get_effective_escalation_price(entry)
                tag = " [already corrected]" if entry.get("corrections") else ""
                return f"{ym} -- current effective price R{eff:,.2f}{tag}"

            correction_year_month = st.selectbox(
                "Select Year to Correct", year_options, index=len(year_options) - 1,
                format_func=_correction_year_label,
            )
            entry = next(e for e in history if e["new_anchor_month"] == correction_year_month)
            current_effective = get_effective_escalation_price(entry)
            st.caption(f"Currently effective figure for {correction_year_month}: R{current_effective:,.2f} "
                        f"(originally approved: R{entry['new_adjusted_price']:,.2f})")

            correction_figure = st.number_input(
                "Corrected Figure (ZAR)", min_value=0.01, value=float(current_effective), step=1000.0
            )
            correction_reason = st.text_area(
                "Reason for Correction (required)",
                placeholder="e.g. data entry error, dispute resolution, audit finding",
            )

            idx = year_options.index(correction_year_month)
            has_later_years = idx < len(year_options) - 1
            is_compound_tender = resolve_effective_tender(correction_tender)["cpa_formula_type"] == "COMPOUND_FROM_PRIOR_YEAR"
            if has_later_years and is_compound_tender:
                correction_cascade_mode = st.radio(
                    "How should subsequent already-approved years be handled?",
                    ["ISOLATED", "CASCADE_FORWARD"],
                    format_func=lambda x: {
                        "ISOLATED": "Remain ISOLATED (default) -- only this year changes; later years "
                                     "stay as approved, flagged as calculated from a since-corrected figure",
                        "CASCADE_FORWARD": "Cascade Forward -- recalculate every subsequent approved year "
                                            "from this correction",
                    }[x],
                )
            else:
                correction_cascade_mode = "ISOLATED"  # not applicable: CUMULATIVE, or no later years exist

            st.markdown("---")
            trigger_preview_correction = st.button("Preview Correction")

    elif registry_mode == "Archived / Closed Contracts":
        st.header("Archived / Closed Contracts")
        st.write("Nothing about an archived tender is ever hidden or deleted -- its full anchor, "
                  "escalation, and correction history remains fully retrievable here.")
        archived_list = [t for t in list_tenders() if t.get("status") == "ARCHIVED"]
        if not archived_list:
            st.info("No archived tenders yet.")
        else:
            options = {f"{t['tender_id']} - {resolve_effective_tender(t)['tender_name']}": t["tender_id"]
                       for t in archived_list}
            picked_label = st.selectbox("Select Archived Tender", list(options.keys()), key="archived_tender_select")
            archived_tender = get_tender(options[picked_label])
            ai = archived_tender.get("archive_info", {})
            st.caption(f"Archived by {ai.get('archived_by', 'N/A')} on {ai.get('archived_at', 'N/A')} "
                        f"-- Reason: {ai.get('reason', 'N/A')}")

            st.markdown("---")
            meta_result = render_metadata_edit_or_correct_block(archived_tender, key_prefix="archived")
            if meta_result["trigger_direct_edit"]:
                trigger_direct_metadata_edit = True
                direct_edit_data = meta_result["direct_edit_data"]
            if meta_result["trigger_preview_correction"]:
                trigger_preview_metadata_correction = True
                metadata_correction_preview_data = meta_result["preview_data"]

            st.markdown("---")
            # Consistent with every other action in this app (mutating or not,
            # e.g. "Run Monthly Check") requiring an explicit click before
            # anything renders into the main frame -- no auto-render on select.
            view_col, export_col = st.columns(2)
            with view_col:
                trigger_view_archived = st.button("View Full History")
            with export_col:
                if st.button("📥 Export Full History", key="archived_export_btn"):
                    st.markdown("---")
                    download_tender_history(archived_tender["tender_id"], archived_tender)

    else:  # registry_mode == "SCM Audit Chatbot (Beta)"
        # Standalone feature -- independent of the Tender Registry above.
        # Reuses scm_parser.py's PDF parser + fallback verification engine
        # and ollama_bot.py's local-LLM client, the same modules
        # run_demo.py uses from the terminal. See CHATBOT_README.md.
        st.header("Municipal SCM Audit Chatbot")
        st.write(
            "Upload a contractor invoice PDF to verify its claimed price escalation against the "
            "Month M-1 Stats SA CPI/PPI rule. This is a standalone mock verification engine -- it "
            "does not read or write the Tender Registry above."
        )

        chatbot_uploaded_file = st.file_uploader("Contractor Invoice (PDF)", type=["pdf"], key="chatbot_uploader")

        st.markdown("---")
        trigger_chatbot_analysis = st.button("Analyze Invoice")

# MAIN FRAME PROCESSING GRAPHICS
if trigger_anchor:
    if start_date >= end_date:
        st.error("Operational Error: Expiration/End Date must be strictly after the Start Date.")
    elif tender_exists(tender_id):
        st.error(f"Tender ID '{tender_id}' already exists in the registry. "
                  "Use 'Open Existing Tender' to check it, or choose a different ID to anchor a new one.")
    else:
        try:
            validator = SCMDataValidator(ARCHIVE_PATH)
            # Anchor-only run: start == end, so this both validates the anchor
            # month through the full 10-stage gate and returns exactly one row.
            timeline_results, stage_results, extras, output_hash = validator.process_timeline_loop(
                str(start_date), str(start_date), tender_id=tender_id, baseline_type=None
            )
            anchor_record = timeline_results[0]

            # original_* is permanent from this point on -- never written
            # again by anything in this app. current_* starts identical and
            # is the only pair record_escalation() is ever allowed to move.
            save_tender({
                "tender_id": tender_id,
                "tender_name": tender_name,
                "baseline_type": baseline_type,
                "cpa_formula_type": cpa_formula_type,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "original_anchor_month": anchor_record["month"],
                "original_anchor_cpi": anchor_record["anchor_cpi"],
                "original_base_value": base_value,
                "current_anchor_month": anchor_record["month"],
                "current_anchor_cpi": anchor_record["anchor_cpi"],
                "current_adjusted_price": base_value,
                "created_at": datetime.datetime.now().isoformat(),
                "status": "ACTIVE",
            })

            # Stashed in session_state rather than rendered directly: st.download_button
            # clicks trigger a Streamlit rerun in which trigger_anchor/trigger_check are
            # False again (the button wasn't clicked on THAT rerun), so anything rendered
            # only inside this `if` block would vanish the moment a user clicked one
            # download button, taking the other download button down with it.
            st.session_state["last_result"] = {
                "banner": f"Tender '{tender_id}' anchored at {anchor_record['month']} "
                          f"(CPI {anchor_record['anchor_cpi']}). Saved to registry.",
                "tender_id": tender_id, "tender_name": tender_name, "baseline_type": baseline_type,
                "base_value": base_value, "start_date_str": str(start_date), "end_date_str": str(end_date),
                "timeline_results": timeline_results, "stage_results": stage_results,
                "extras": extras, "output_hash": output_hash,
                "document_title": "Tender Anchor Record", "escalation_info": None,
                "correction_detail": None, "correction_history": [],  # brand new tender, none possible yet
            }
        except ValueError as e:
            st.error(f"Validation Pipeline Halted: {e}")
        except Exception as e:
            st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

elif trigger_check and selected_tender and check_month:
    try:
        validator = SCMDataValidator(ARCHIVE_PATH)
        # A non-mutating preview against whichever reference point this
        # tender's formula calls for -- same helper Calculate/Confirm Annual
        # Escalation use, so a monthly check is always consistent with what
        # the next escalation would actually compute for this tender.
        check_anchor_month, check_anchor_cpi, check_base_price = _formula_reference_point(selected_tender)
        timeline_results, stage_results, extras, output_hash = validator.run_monthly_check(
            anchor_month=check_anchor_month,
            anchor_cpi_value=check_anchor_cpi,
            check_month=check_month,
            tender_id=selected_tender["tender_id"],
        )
        # Audit-log-only: no CPI/price/anchor figure is touched by this call
        # (see log_check()'s docstring) -- it just makes "has this tender ever
        # been checked" gate-able for metadata correction.
        updated_tender = log_check(selected_tender["tender_id"], check_month)
        effective_tender = resolve_effective_tender(updated_tender)
        st.session_state["last_result"] = {
            "banner": None,
            "tender_id": effective_tender["tender_id"], "tender_name": effective_tender["tender_name"],
            "baseline_type": effective_tender["baseline_type"], "base_value": check_base_price,
            "start_date_str": effective_tender["start_date"], "end_date_str": effective_tender["end_date"],
            "timeline_results": timeline_results, "stage_results": stage_results,
            "extras": extras, "output_hash": output_hash,
            "document_title": "Monthly Invoice Verification Record", "escalation_info": None,
            "correction_detail": None,
            # Every document for a tender that has ever been corrected shows
            # the full trail, not just the correction's own record.
            "correction_history": get_correction_history(updated_tender),
        }
    except ValueError as e:
        st.error(f"Validation Pipeline Halted: {e}")
    except Exception as e:
        st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

elif trigger_calculate_escalation and selected_tender and check_month:
    # CALCULATE ONLY -- does not touch the registry. Neither the permanent
    # original_* fields NOR the rolling current_* fields are written here;
    # this only stashes a proposal in session_state until a separate,
    # explicit approval step (below) applies it.
    try:
        # Re-validated server-side, not just gated by which buttons the UI
        # showed: an Annual Escalation is only legal on a true anniversary of
        # the tender's CURRENT anchor, and can't be run against that anchor
        # month itself. Anniversaries are always counted from the CURRENT
        # anchor (which, under normal one-escalation-per-year operation,
        # stays 12 months apart from the original anchor anyway).
        current_anchor_month = selected_tender["current_anchor_month"]
        if check_month == current_anchor_month:
            raise ValueError(f"Cannot escalate a tender against its own current anchor month ({current_anchor_month}).")
        months_diff = _months_since_anchor(current_anchor_month, check_month)
        if months_diff <= 0 or months_diff % 12 != 0:
            raise ValueError(f"{check_month} is not a 12-month anniversary of the current anchor "
                              f"({current_anchor_month}) -- Annual Escalation is only valid on anniversary months.")

        # This is the whole formula distinction: CUMULATIVE always derives
        # from the untouched original tender submission; COMPOUND derives
        # from whatever the last approved escalation left as current. Same
        # helper "Run Monthly Check" uses, so the two are never inconsistent.
        # Reads the EFFECTIVE formula type/base value (post metadata-correction,
        # if any) via resolve_effective_tender() -- see _formula_reference_point().
        effective_selected_tender = resolve_effective_tender(selected_tender)
        formula_type = effective_selected_tender.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL")
        calc_anchor_month, calc_anchor_cpi, calc_base_price = _formula_reference_point(selected_tender)

        validator = SCMDataValidator(ARCHIVE_PATH)
        # This year's validated drift against whichever anchor the formula
        # calls for -- computed BEFORE any rollover, nothing written to the
        # registry from this call.
        timeline_results, stage_results, extras, output_hash = validator.run_monthly_check(
            anchor_month=calc_anchor_month,
            anchor_cpi_value=calc_anchor_cpi,
            check_month=check_month,
            tender_id=selected_tender["tender_id"],
        )
        drift_pct = timeline_results[-1]["drift_percentage"]
        new_cpi = timeline_results[-1]["current_cpi"]
        new_adjusted_price = calc_base_price * (1 + drift_pct / 100)

        st.session_state["pending_escalation"] = {
            "tender_id": selected_tender["tender_id"],
            "tender_name": selected_tender["tender_name"],
            "baseline_type": selected_tender["baseline_type"],
            "start_date": selected_tender["start_date"],
            "end_date": selected_tender["end_date"],
            "formula_type": formula_type,
            "original_anchor_month": selected_tender["original_anchor_month"],
            "original_anchor_cpi": selected_tender["original_anchor_cpi"],
            "original_base_value": effective_selected_tender["original_base_value"],
            "prior_anchor_month": selected_tender["current_anchor_month"],
            "prior_anchor_cpi": selected_tender["current_anchor_cpi"],
            "prior_adjusted_price": selected_tender["current_adjusted_price"],
            "calc_anchor_month": calc_anchor_month,
            "calc_anchor_cpi": calc_anchor_cpi,
            "calc_base_price": calc_base_price,
            "new_anchor_month": check_month,
            "new_anchor_cpi": new_cpi,
            "new_adjusted_price": new_adjusted_price,
            "drift_pct": drift_pct,
            "pipeline_ok": all(s["status"] != "FAIL" for s in stage_results),
            "calculated_at": datetime.datetime.now().isoformat(),
        }
    except ValueError as e:
        st.error(f"Validation Pipeline Halted: {e}")
    except Exception as e:
        st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

# PENDING ANNUAL ESCALATION -- APPROVAL GATE
# Shown regardless of current sidebar selection (not tied to selected_tender
# / check_month) so a pending item survives the approver navigating around
# to double check something before confirming. Nothing here has been
# applied to the registry yet -- that only happens if "Confirm & Apply" is
# clicked below, and only after a non-empty approver name/role is given.
if "pending_escalation" in st.session_state:
    pend = st.session_state["pending_escalation"]
    is_compound = pend["formula_type"] == "COMPOUND_FROM_PRIOR_YEAR"

    lines = [
        f"PENDING ANNUAL ESCALATION for '{pend['tender_id']}' -- PROPOSED, NOT YET APPLIED.",
        "",
        f"Formula Type: {pend['formula_type']}",
        "",
        f"Original Baseline (fixed against escalation rollups, {pend['original_anchor_month']}): "
        f"CPI {pend['original_anchor_cpi']}, Base R{pend['original_base_value']:,.2f}",
    ]
    if is_compound:
        lines += [
            "",
            f"Prior Year Adjusted Price ({pend['prior_anchor_month']}): "
            f"CPI {pend['prior_anchor_cpi']}, Price R{pend['prior_adjusted_price']:,.2f}",
        ]
    lines += [
        "",
        f"This Year's New Adjusted Price ({pend['new_anchor_month']}): "
        f"CPI {pend['new_anchor_cpi']}, Price R{pend['new_adjusted_price']:,.2f} "
        f"(drift {pend['drift_pct']:+.4f}% from the "
        f"{'original' if not is_compound else 'prior-year'} anchor)",
        "",
        f"10-Stage validation: {'PASSED' if pend['pipeline_ok'] else 'FAILED'}",
    ]
    st.markdown("---")
    st.warning("\n\n".join(lines))

    approver_name = st.text_input(
        "Approver Name / Role (required to apply)", key="approver_input",
        placeholder="e.g. J. Naidoo, SCM Manager",
    )
    confirm_col, discard_col = st.columns(2)
    with confirm_col:
        confirm_clicked = st.button("Confirm & Apply Annual Escalation")
    with discard_col:
        discard_clicked = st.button("Discard Pending Escalation")

    if discard_clicked:
        del st.session_state["pending_escalation"]
        st.rerun()

    if confirm_clicked:
        if not approver_name or not approver_name.strip():
            st.error("Approver Name / Role is required before an Annual Escalation can be applied.")
        else:
            try:
                # Re-fetch and re-validate fresh at the moment of approval --
                # don't just trust the numbers calculated earlier, in case
                # the registry or archive changed in between. Only the
                # CURRENT anchor is checked for drift -- original_* is
                # permanent and can never have "changed" underneath us.
                current = get_tender(pend["tender_id"])
                if current is None:
                    raise ValueError(f"Tender '{pend['tender_id']}' no longer exists in the registry.")
                if current["current_anchor_month"] != pend["prior_anchor_month"]:
                    raise ValueError(
                        f"Tender '{pend['tender_id']}'s current anchor has changed since this was "
                        f"calculated (now {current['current_anchor_month']}, "
                        f"was {pend['prior_anchor_month']}) -- discard this proposal and recalculate."
                    )

                # Re-derive from scratch using the SAME formula-dependent
                # base the calculate step used -- never trust the stashed
                # pending numbers as the thing actually written to the
                # registry.
                calc_anchor_month, calc_anchor_cpi, calc_base_price = _formula_reference_point(current)

                validator = SCMDataValidator(ARCHIVE_PATH)
                timeline_results, stage_results, extras, output_hash = validator.run_monthly_check(
                    anchor_month=calc_anchor_month,
                    anchor_cpi_value=calc_anchor_cpi,
                    check_month=pend["new_anchor_month"],
                    tender_id=pend["tender_id"],
                )
                drift_pct = timeline_results[-1]["drift_percentage"]
                new_cpi = timeline_results[-1]["current_cpi"]
                new_adjusted_price = calc_base_price * (1 + drift_pct / 100)
                approved_at = datetime.datetime.now().isoformat()

                updated = record_escalation(
                    pend["tender_id"], pend["new_anchor_month"], new_cpi, new_adjusted_price,
                    approved_by=approver_name.strip(),
                )

                st.session_state["last_result"] = {
                    "banner": f"Annual escalation APPLIED for '{updated['tender_id']}'. "
                              f"New current anchor: {pend['new_anchor_month']} @ CPI {new_cpi}. "
                              f"Original baseline unchanged at {updated['original_anchor_month']} "
                              f"@ CPI {updated['original_anchor_cpi']}. "
                              f"Approved by {approver_name.strip()}.",
                    "tender_id": updated["tender_id"], "tender_name": updated["tender_name"],
                    "baseline_type": updated["baseline_type"],
                    "base_value": new_adjusted_price,  # the document reflects the NEW price going forward
                    "start_date_str": updated["start_date"], "end_date_str": updated["end_date"],
                    "timeline_results": timeline_results, "stage_results": stage_results,
                    "extras": extras, "output_hash": output_hash,
                    "document_title": "Annual Contract Price Adjustment Record",
                    "escalation_info": {
                        "effective_date": pend["new_anchor_month"],
                        "old_base_zar": calc_base_price, "new_base_zar": new_adjusted_price,
                        "approved_by": approver_name.strip(), "approved_at": approved_at,
                        "formula_type": pend["formula_type"],
                        "original_anchor_month": updated["original_anchor_month"],
                        "original_anchor_cpi": updated["original_anchor_cpi"],
                        "original_base_zar": resolve_effective_tender(updated)["original_base_value"],
                        "prior_anchor_month": pend["prior_anchor_month"],
                        "prior_adjusted_price": pend["prior_adjusted_price"],
                    },
                    "correction_detail": None,
                    "correction_history": get_correction_history(updated),
                }
                del st.session_state["pending_escalation"]
                st.rerun()
            except ValueError as e:
                st.error(f"Validation Pipeline Halted: {e}")
            except Exception as e:
                st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

elif trigger_preview_correction and correction_tender and correction_year_month:
    # PREVIEW ONLY -- a dry_run, so nothing is written to the registry.
    # An approved figure is never edited in place; this just computes what
    # WOULD change (including any cascade impact) for review before an
    # approver commits to it below.
    try:
        preview = apply_correction(
            correction_tender["tender_id"], correction_year_month, correction_figure,
            correction_reason or "", corrected_by=None, cascade_mode=correction_cascade_mode,
            dry_run=True,
        )
        st.session_state["pending_correction"] = {
            "tender_id": correction_tender["tender_id"],
            "tender_name": correction_tender["tender_name"],
            "baseline_type": correction_tender["baseline_type"],
            "start_date": correction_tender["start_date"],
            "end_date": correction_tender["end_date"],
            "cpa_formula_type": resolve_effective_tender(correction_tender).get("cpa_formula_type"),
            "year_month": correction_year_month,
            "corrected_figure": correction_figure,
            "reason": correction_reason,
            "cascade_mode": correction_cascade_mode,
            "preview_escalation_history": preview["escalation_history"],
            "preview_current_adjusted_price": preview["current_adjusted_price"],
            "calculated_at": datetime.datetime.now().isoformat(),
        }
    except ValueError as e:
        st.error(f"Correction Rejected: {e}")

elif trigger_direct_metadata_edit and direct_edit_data:
    # No approval gate -- legal ONLY while the tender has zero activity
    # (server-side re-checked inside update_tender_metadata_direct() itself,
    # not just gated by which UI branch rendered the button).
    d = direct_edit_data
    try:
        if d["start_date"] >= d["end_date"]:
            st.error("Operational Error: Contract End Date must be strictly after the Start Date.")
        else:
            updated = update_tender_metadata_direct(
                d["tender_id"], d["tender_name"], str(d["start_date"]), str(d["end_date"]),
                d["original_base_value"], d["cpa_formula_type"],
            )
            st.session_state["last_result"] = {
                "banner": f"Tender '{updated['tender_id']}' metadata updated (no history existed yet -- "
                          "no approval gate required).",
                "tender_id": updated["tender_id"], "tender_name": updated["tender_name"],
                "baseline_type": updated["baseline_type"], "base_value": updated["current_adjusted_price"],
                "start_date_str": updated["start_date"], "end_date_str": updated["end_date"],
                "timeline_results": None, "stage_results": None,
                "extras": {}, "output_hash": "N/A",
                "document_title": "Tender Anchor Record", "escalation_info": None,
                "correction_detail": None, "correction_history": [],
            }
    except ValueError as e:
        st.error(f"Metadata Edit Rejected: {e}")
    except Exception as e:
        st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

elif trigger_preview_metadata_correction and metadata_correction_preview_data:
    # PREVIEW ONLY -- a dry_run, nothing written to the registry. Mirrors
    # trigger_preview_correction's exact shape above.
    d = metadata_correction_preview_data
    try:
        preview = correct_tender_metadata(
            d["tender_id"], d["field"], d["corrected_value"], d["reason"] or "",
            corrected_by=None, dry_run=True,
        )
        latest = preview["metadata_corrections"][-1]
        st.session_state["pending_metadata_correction"] = {
            "tender_id": preview["tender_id"], "tender_name": resolve_effective_tender(preview)["tender_name"],
            "field": latest["field"], "original_value": latest["original_value"],
            "corrected_value": latest["corrected_value"], "reason": latest["reason"],
            "retroactive_impact_flag": latest["retroactive_impact_flag"],
            "retroactive_impact_note": latest["retroactive_impact_note"],
            "calculated_at": datetime.datetime.now().isoformat(),
        }
    except ValueError as e:
        st.error(f"Correction Rejected: {e}")

elif trigger_archive and archive_target_tender:
    # CALCULATE-free: archiving has nothing to derive/preview, so this
    # stashes a pending confirmation directly (matching Anchor Tender's own
    # single-step-but-validated shape) rather than the calculate-then-approve
    # gate used for escalations/corrections, which exists specifically to
    # preview a DERIVED number before committing to it.
    if not archive_reason or not archive_reason.strip():
        st.error("A reason is required to archive a tender.")
    else:
        st.session_state["pending_archive"] = {
            "tender_id": archive_target_tender["tender_id"],
            "tender_name": resolve_effective_tender(archive_target_tender)["tender_name"],
            "reason": archive_reason.strip(),
            "requested_at": datetime.datetime.now().isoformat(),
        }

elif trigger_view_archived and archived_tender:
    ai = archived_tender.get("archive_info", {})
    effective_tender = resolve_effective_tender(archived_tender)
    st.session_state["last_result"] = {
        "banner": None,
        "tender_id": effective_tender["tender_id"], "tender_name": effective_tender["tender_name"],
        "baseline_type": effective_tender["baseline_type"],
        "base_value": archived_tender["current_adjusted_price"],
        "start_date_str": effective_tender["start_date"], "end_date_str": effective_tender["end_date"],
        "timeline_results": None, "stage_results": None,
        "extras": {}, "output_hash": "N/A",
        "document_title": "Archived Tender -- Full Audit Record",
        "escalation_info": None, "correction_detail": None,
        "correction_history": get_correction_history(archived_tender),
        "metadata_correction_detail": None,
        "archive_detail": {**ai, "just_archived": False},
        "full_escalation_history": archived_tender.get("escalation_history", []),
    }

# PENDING CORRECTION -- APPROVAL GATE
# Same pattern as the escalation gate above: nothing is applied to the
# registry until an approver is named and Confirm & Apply is clicked.
if "pending_correction" in st.session_state:
    pc = st.session_state["pending_correction"]
    year_idx = next(i for i, e in enumerate(pc["preview_escalation_history"]) if e["new_anchor_month"] == pc["year_month"])
    corrected_entry = pc["preview_escalation_history"][year_idx]
    # apply_correction() already computed this (the effective price right
    # before the new correction it appended in the dry-run preview) -- read
    # it straight from there instead of re-deriving it a second way.
    original_figure = corrected_entry["corrections"][-1]["original_figure"]
    is_compound = pc["cpa_formula_type"] == "COMPOUND_FROM_PRIOR_YEAR"
    later_entries = pc["preview_escalation_history"][year_idx + 1:]

    lines = [
        f"PENDING CORRECTION for '{pc['tender_id']}', Year {pc['year_month']} -- PROPOSED, NOT YET APPLIED.",
        "",
        f"Original Approved Figure: R{original_figure:,.2f}",
        f"Proposed Corrected Figure: R{pc['corrected_figure']:,.2f}",
        f"Reason: {pc['reason']}",
    ]
    if later_entries and is_compound:
        lines.append(f"Cascade Mode: {pc['cascade_mode']}")
    if later_entries:
        lines.append("")
        lines.append("Impact on subsequent already-approved years:")
        for later in later_entries:
            eff = get_effective_escalation_price(later)
            if pc["cascade_mode"] == "CASCADE_FORWARD" and is_compound:
                lines.append(f"  {later['new_anchor_month']}: would be RECALCULATED to R{eff:,.2f}")
            else:
                lines.append(f"  {later['new_anchor_month']}: stays as approved at "
                              f"R{later['new_adjusted_price']:,.2f} (will be flagged as calculated "
                              "from a since-corrected input)")
    lines += [
        "",
        f"New current effective price after this correction: R{pc['preview_current_adjusted_price']:,.2f}",
    ]
    st.markdown("---")
    st.warning("\n\n".join(lines))

    corrector_name = _current_approver_display()
    st.text_input(
        "Approver (auto-filled from your logged-in account)",
        value=corrector_name, disabled=True, key="corrector_input_display",
    )
    confirm_corr_col, discard_corr_col = st.columns(2)
    with confirm_corr_col:
        confirm_correction_clicked = st.button("Confirm & Apply Correction")
    with discard_corr_col:
        discard_correction_clicked = st.button("Discard Pending Correction")

    if discard_correction_clicked:
        del st.session_state["pending_correction"]
        st.rerun()

    if confirm_correction_clicked:
        if not corrector_name or not corrector_name.strip():
            st.error("Approver Name / Role is required before a correction can be applied.")
        else:
            try:
                updated = apply_correction(
                    pc["tender_id"], pc["year_month"], pc["corrected_figure"], pc["reason"],
                    corrected_by=corrector_name.strip(), cascade_mode=pc["cascade_mode"],
                )
                # Re-find the entry by year_month rather than reusing the
                # preview's index -- cheap, and avoids ever trusting an
                # index computed against a possibly-stale snapshot.
                updated_entry = next(e for e in updated["escalation_history"] if e["new_anchor_month"] == pc["year_month"])
                corrected_at = updated_entry["corrections"][-1]["corrected_at"]

                st.session_state["last_result"] = {
                    "banner": f"Correction APPLIED for '{updated['tender_id']}', {pc['year_month']}. "
                              f"Approved by {corrector_name.strip()}.",
                    "tender_id": updated["tender_id"], "tender_name": updated["tender_name"],
                    "baseline_type": updated["baseline_type"],
                    "base_value": updated["current_adjusted_price"],
                    "start_date_str": updated["start_date"], "end_date_str": updated["end_date"],
                    "timeline_results": None, "stage_results": None,  # a correction is not a CPI calculation
                    "extras": {}, "output_hash": "N/A",
                    "document_title": "Annual Price Correction Record",
                    "escalation_info": None,
                    "correction_detail": {
                        "year_month": pc["year_month"],
                        "original_figure": original_figure,
                        "corrected_figure": pc["corrected_figure"],
                        "reason": pc["reason"],
                        "corrected_by": corrector_name.strip(),
                        "corrected_at": corrected_at,
                        "cascade_mode": pc["cascade_mode"] if is_compound and later_entries else None,
                    },
                    "correction_history": get_correction_history(updated),
                }
                del st.session_state["pending_correction"]
                st.rerun()
            except ValueError as e:
                st.error(f"Validation Pipeline Halted: {e}")
            except Exception as e:
                st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

# PENDING ARCHIVE -- APPROVAL GATE
# Same shape as the escalation/correction gates: nothing is applied to the
# registry until a named archived_by is given and Confirm is clicked.
if "pending_archive" in st.session_state:
    pa = st.session_state["pending_archive"]
    st.markdown("---")
    st.warning(
        f"PENDING ARCHIVE for '{pa['tender_id']}' ({pa['tender_name']}) -- NOT YET APPLIED.\n\n"
        f"Reason: {pa['reason']}\n\n"
        "This is ONE-DIRECTIONAL -- there is no 'Reactivate' action. The tender's full history "
        "is never deleted or altered; it only leaves the active 'Open Existing Tender' list."
    )
    archived_by_name = _current_approver_display()
    st.text_input(
        "Archived By (auto-filled from your logged-in account)",
        value=archived_by_name, disabled=True, key="archived_by_input_display",
    )
    confirm_arc_col, discard_arc_col = st.columns(2)
    with confirm_arc_col:
        confirm_archive_clicked = st.button("Confirm & Apply Archive")
    with discard_arc_col:
        discard_archive_clicked = st.button("Discard Pending Archive")

    if discard_archive_clicked:
        del st.session_state["pending_archive"]
        st.rerun()

    if confirm_archive_clicked:
        if not archived_by_name or not archived_by_name.strip():
            st.error("Archived By is required before an archive can be applied.")
        else:
            try:
                updated = archive_tender(pa["tender_id"], pa["reason"], archived_by_name.strip())
                effective_tender = resolve_effective_tender(updated)
                st.session_state["last_result"] = {
                    "banner": f"Tender '{updated['tender_id']}' ARCHIVED. Archived by {archived_by_name.strip()}.",
                    "tender_id": updated["tender_id"], "tender_name": effective_tender["tender_name"],
                    "baseline_type": updated["baseline_type"],
                    "base_value": updated["current_adjusted_price"],
                    "start_date_str": effective_tender["start_date"], "end_date_str": effective_tender["end_date"],
                    "timeline_results": None, "stage_results": None,
                    "extras": {}, "output_hash": "N/A",
                    "document_title": "Tender Archived Record",
                    "escalation_info": None, "correction_detail": None,
                    "correction_history": get_correction_history(updated),
                    "metadata_correction_detail": None,
                    "archive_detail": {**updated["archive_info"], "just_archived": True},
                    "full_escalation_history": updated.get("escalation_history", []),
                }
                del st.session_state["pending_archive"]
                st.rerun()
            except (ValueError, KeyError) as e:
                st.error(f"Archive Rejected: {e}")

# PENDING METADATA CORRECTION -- APPROVAL GATE
# Same shape as the CPI correction gate: nothing is applied to the registry
# until a named corrected_by is given and Confirm is clicked.
if "pending_metadata_correction" in st.session_state:
    pmc = st.session_state["pending_metadata_correction"]
    st.markdown("---")
    lines = [
        f"PENDING METADATA CORRECTION for '{pmc['tender_id']}' -- PROPOSED, NOT YET APPLIED.",
        "",
        f"Field: {pmc['field']}",
        f"Original Value: {_format_metadata_value(pmc['field'], pmc['original_value'])}",
        f"Proposed Corrected Value: {_format_metadata_value(pmc['field'], pmc['corrected_value'])}",
        f"Reason: {pmc['reason']}",
    ]
    st.markdown("---")
    if pmc["retroactive_impact_flag"]:
        st.warning("\n\n".join(lines))
        st.error(f"⚠ FLAGGED FOR HUMAN REVIEW: {pmc['retroactive_impact_note']}")
    else:
        st.warning("\n\n".join(lines))

    metadata_corrector_name = _current_approver_display()
    st.text_input(
        "Approver (auto-filled from your logged-in account)",
        value=metadata_corrector_name, disabled=True, key="metadata_corrector_input_display",
    )
    confirm_meta_col, discard_meta_col = st.columns(2)
    with confirm_meta_col:
        confirm_metadata_correction_clicked = st.button("Confirm & Apply Metadata Correction")
    with discard_meta_col:
        discard_metadata_correction_clicked = st.button("Discard Pending Metadata Correction")

    if discard_metadata_correction_clicked:
        del st.session_state["pending_metadata_correction"]
        st.rerun()

    if confirm_metadata_correction_clicked:
        if not metadata_corrector_name or not metadata_corrector_name.strip():
            st.error("Approver Name / Role is required before a metadata correction can be applied.")
        else:
            try:
                updated = correct_tender_metadata(
                    pmc["tender_id"], pmc["field"], pmc["corrected_value"], pmc["reason"],
                    corrected_by=metadata_corrector_name.strip(),
                )
                latest = updated["metadata_corrections"][-1]
                effective_tender = resolve_effective_tender(updated)
                st.session_state["last_result"] = {
                    "banner": f"Metadata correction APPLIED for '{updated['tender_id']}', field "
                              f"'{pmc['field']}'. Approved by {metadata_corrector_name.strip()}.",
                    "tender_id": updated["tender_id"], "tender_name": effective_tender["tender_name"],
                    "baseline_type": updated["baseline_type"],
                    "base_value": effective_tender["original_base_value"],
                    "start_date_str": effective_tender["start_date"], "end_date_str": effective_tender["end_date"],
                    "timeline_results": None, "stage_results": None,
                    "extras": {}, "output_hash": "N/A",
                    "document_title": "Tender Metadata Correction Record",
                    "escalation_info": None, "correction_detail": None,
                    "correction_history": get_correction_history(updated),
                    "metadata_correction_detail": latest,
                    "archive_detail": None,
                    "full_escalation_history": None,
                }
                del st.session_state["pending_metadata_correction"]
                st.rerun()
            except ValueError as e:
                st.error(f"Validation Pipeline Halted: {e}")
            except Exception as e:
                st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

# Renders whatever the most recent successful anchor/check/escalation/
# correction produced. Lives outside the trigger blocks above so it
# survives the reruns that clicking either download button inside it
# causes (see comment above).
if "last_result" in st.session_state:
    res = st.session_state["last_result"]
    if res["banner"]:
        st.success(res["banner"])
    render_result_block(
        res["tender_id"], res["tender_name"], res["baseline_type"], res["base_value"],
        res["start_date_str"], res["end_date_str"], res["timeline_results"],
        res["stage_results"], res["extras"], res["output_hash"],
        document_title=res.get("document_title", "Audit-Ready Compliance Record"),
        escalation_info=res.get("escalation_info"),
        correction_detail=res.get("correction_detail"),
        correction_history=res.get("correction_history"),
        metadata_correction_detail=res.get("metadata_correction_detail"),
        archive_detail=res.get("archive_detail"),
        full_escalation_history=res.get("full_escalation_history"),
    )


def _crosscheck_against_registry(fields: dict, outcome: dict) -> dict:
    """The chatbot's own verification (scm_parser.verify_invoice()) never
    reads data/tenders.json -- it trusts whatever baseline date / value the
    invoice PDF itself claims, with no way to notice if that disagrees with
    the tender's actual audited anchor. This is a SEPARATE, additive check:
    if the invoice's Contract Reference Number happens to match an existing
    tender_id in the registry, compare the invoice's claimed baseline month
    and (if present) original contract value against that tender's
    registry-anchored figures, and surface any mismatch explicitly rather
    than silently trusting the invoice. Read-only -- never writes anything.
    """
    contract_id = outcome.get("contract_id")
    if not contract_id:
        return {"checked": False, "reason": "No Contract Reference Number was extracted from the invoice."}

    tender = get_tender(contract_id)
    if tender is None:
        return {
            "checked": True, "found": False,
            "detail": f"No tender with ID '{contract_id}' exists in the registry -- this invoice's "
                      "figures could not be cross-checked against an anchored record.",
        }

    effective = resolve_effective_tender(tender)
    mismatches = []

    invoice_baseline_month = (fields.get("baseline_date") or "")[:7]
    registry_anchor_month = effective.get("original_anchor_month")
    if invoice_baseline_month and registry_anchor_month and invoice_baseline_month != registry_anchor_month:
        mismatches.append(
            f"Invoice's Baseline Contract Date ({invoice_baseline_month}) does not match this "
            f"tender's audited anchor month in the registry ({registry_anchor_month})."
        )

    invoice_value = fields.get("original_value")
    registry_value = effective.get("original_base_value")
    if invoice_value is not None and registry_value is not None and abs(float(invoice_value) - float(registry_value)) > 0.01:
        mismatches.append(
            f"Invoice's Original Contract Value (R{invoice_value:,.2f}) does not match this "
            f"tender's registered original base value (R{registry_value:,.2f})."
        )

    return {
        "checked": True, "found": True,
        "tender_id": tender["tender_id"], "tender_name": effective.get("tender_name"),
        "status": tender.get("status", "ACTIVE"),
        "registry_anchor_month": registry_anchor_month, "registry_original_base_value": registry_value,
        "mismatches": mismatches,
    }


# MUNICIPAL SCM AUDIT CHATBOT -- category reference browser. Shown whenever
# this mode is selected, not gated behind "Analyze Invoice" -- lets you see
# (not pick from -- verification always reads the category from the
# uploaded invoice's own text) the full set of categories scm_parser.py
# recognizes, sourced from the tenderbulletins.co.za taxonomy.
if registry_mode == "SCM Audit Chatbot (Beta)":
    st.markdown("---")
    with st.expander(
        f"Browse recognized categories ({len(scm_parser.CATEGORY_INDEX_MAP)} priced, "
        f"{len(scm_parser.PENDING_CATEGORIES)} pending, "
        f"{len(scm_parser.EXCLUDED_CATEGORIES)} excluded)"
    ):
        st.caption(
            "Reference only -- verification always reads the category from the uploaded "
            "invoice's own text; there's nothing to select here. Scope: Lekwankwa "
            "Corporation Municipal Procurement Category Mapping (tenderbulletins.co.za)."
        )
        priced_rows = [
            {"Category": name, "Index Type": info["index_type"], "Series": info["label"]}
            for name, info in scm_parser.CATEGORY_INDEX_MAP.items()
        ]
        pending_rows = [
            {"Category": name, "Index Type": "PENDING", "Series": "Not yet enabled -- see message on analysis"}
            for name in scm_parser.PENDING_CATEGORIES
        ]
        excluded_rows = [
            {"Category": name, "Index Type": "EXCLUDED", "Series": "Manual review required (JBCC/GCC/FIDIC/NEC)"}
            for name in scm_parser.EXCLUDED_CATEGORIES
        ]
        category_df = (
            pd.DataFrame(priced_rows + pending_rows + excluded_rows)
            .sort_values("Category")
            .reset_index(drop=True)
        )
        st.dataframe(category_df, use_container_width=True, height=400)

# MUNICIPAL SCM AUDIT CHATBOT -- Result rendering
# Deliberately separate from render_result_block()/"last_result" above: this
# feature is standalone (see scm_parser.py / CHATBOT_README.md) and its
# result shape (a scm_parser.verify_invoice() outcome dict) has nothing to
# do with the Tender Registry's timeline/escalation/correction schema. The
# cross-check below is the one deliberate, read-only exception: it queries
# (never writes) the registry to catch an invoice whose claimed baseline/
# value disagrees with what was actually anchored.
if trigger_chatbot_analysis:
    st.markdown("---")
    st.header("Municipal SCM Audit Chatbot -- Result")

    invoice_text = None
    try:
        if chatbot_uploaded_file is not None:
            invoice_text = scm_parser.extract_text_from_pdf(chatbot_uploaded_file)
        else:
            st.error("Upload a PDF invoice before analyzing.")
    except (FileNotFoundError, RuntimeError) as exc:
        st.error(f"Could not read the invoice PDF: {exc}")

    if invoice_text is not None:
        fields = scm_parser.parse_invoice_fields(invoice_text)
        outcome = scm_parser.verify_invoice(fields)

        if fields["parse_warnings"]:
            st.warning("Parse warnings:\n\n" + "\n".join(f"- {w}" for w in fields["parse_warnings"]))

        status = outcome["status"]
        if status == "PASSED":
            st.success("VERIFICATION STATUS: [PASSED - COMPLIANT]")
        elif status == "FAILED":
            st.error("VERIFICATION STATUS: [FAILED - HARD-LOCK APPLIED]")
        elif status == "MANUAL_REVIEW_REQUIRED":
            st.warning(outcome["message"])
        elif status == "CATEGORY_PENDING":
            st.info(outcome["message"])
        else:  # PARSE_ERROR / UNMAPPED_CATEGORY / INSUFFICIENT_DATA
            st.warning(f"{status}: {outcome['detail']}")

        if status in ("PASSED", "FAILED"):
            # Category names can be long (e.g. "Security Services and
            # Equipment") -- st.metric() truncates with an ellipsis in a
            # narrow column, so it gets its own full-width line instead of
            # sharing the 2-column metric row below.
            col1, col2 = st.columns(2)
            col1.metric("Contract Ref", outcome["contract_id"])
            col2.metric("Index Type", outcome["index_type"] + (" [MOCK]" if outcome["index_type"] == "PPI" else ""))

            category_line = f"**Category:** {outcome['category']}"
            if outcome.get("category_raw") and outcome["category_raw"] != outcome["category"]:
                category_line += f"  \n*(matched from invoice text: '{outcome['category_raw']}')*"
            st.write(category_line)

            st.write(
                f"**Baseline Period:** {outcome['baseline_period']}  |  "
                f"**Evaluated Period (M-1):** {outcome['evaluated_period']}  |  "
                f"**Index Series:** {outcome['index_series_label']}"
            )

            breakdown_rows = [{
                "Attribute": "Escalation Rate",
                "Contractor Claimed": f"{outcome['claimed_escalation_pct']}%",
                "System Limit (Stats SA)": f"{outcome['allowable_escalation_pct']}%",
                "Status": outcome["escalation_rate_status"],
            }]
            if outcome["financial_impact_status"] == "N/A - NO BASE VALUE PROVIDED":
                breakdown_rows.append({
                    "Attribute": "Financial Impact", "Contractor Claimed": "N/A",
                    "System Limit (Stats SA)": "N/A", "Status": outcome["financial_impact_status"],
                })
            else:
                breakdown_rows.append({
                    "Attribute": "Financial Impact",
                    "Contractor Claimed": f"R{outcome['claimed_amount']:,.2f}",
                    "System Limit (Stats SA)": f"R{outcome['allowable_amount']:,.2f}",
                    "Status": outcome["financial_impact_status"],
                })
            st.table(pd.DataFrame(breakdown_rows).set_index("Attribute"))

            if outcome["audit_hash"]:
                st.caption(f"Cryptographic Audit Hash: SHA256:{outcome['audit_hash']}")

        with st.expander("Raw backend verification result (JSON)"):
            st.json(outcome)

        st.markdown("---")
        st.subheader("Registry Cross-Check")
        st.caption(
            "The verification above never reads data/tenders.json -- it trusts whatever this "
            "invoice itself claims. This separate, read-only check compares those claims against "
            "the audited Tender Registry, if a matching tender exists."
        )
        crosscheck = _crosscheck_against_registry(fields, outcome)
        if not crosscheck["checked"]:
            st.info(crosscheck["reason"])
        elif not crosscheck["found"]:
            st.warning(crosscheck["detail"])
        elif crosscheck["mismatches"]:
            st.error(
                f"MISMATCH vs. registry record for '{crosscheck['tender_id']}' "
                f"({crosscheck['tender_name']}, status: {crosscheck['status']}):\n\n"
                + "\n".join(f"- {m}" for m in crosscheck["mismatches"])
            )
        else:
            st.success(
                f"Matches the audited registry record for '{crosscheck['tender_id']}' "
                f"({crosscheck['tender_name']}, status: {crosscheck['status']}) -- anchor month "
                f"{crosscheck['registry_anchor_month']}, original base value "
                f"R{crosscheck['registry_original_base_value']:,.2f}."
            )

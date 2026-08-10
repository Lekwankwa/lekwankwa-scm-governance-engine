import datetime
import json

import pandas as pd
import streamlit as st

from core.validator import SCMDataValidator
from core.tender_registry import (
    apply_correction, get_correction_history, get_effective_escalation_price,
    get_tender, list_tenders, record_escalation, save_tender, tender_exists,
)
from utils.document_gen import build_audit_pdf

ARCHIVE_PATH = "data/stats_sa_cpi_archive.csv"

# Set up clean, institutional dark theme dashboard configuration
st.set_page_config(page_title="Lekwankwa SCM Engine", layout="wide")

st.title("Municipal SCM Governance Engine")
st.subheader("Data-Driven Procurement Protection and Internal Audit Control Gateway")
st.markdown("---")


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
    """
    if tender.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL") == "CUMULATIVE_FROM_ORIGINAL":
        return tender["original_anchor_month"], tender["original_anchor_cpi"], tender["original_base_value"]
    return tender["current_anchor_month"], tender["current_anchor_cpi"], tender["current_adjusted_price"]


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
                          correction_history=None):
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
            f"Original Baseline (permanent, {escalation_info.get('original_anchor_month', 'N/A')}): "
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
            else:
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
        output_hash=output_hash,
    )

    # A pure correction record has no CPI timeline to pull a month tag from
    # for the file name -- fall back to the corrected year, then a plain tag.
    file_month_tag = (
        timeline_results[-1]["month"] if timeline_results
        else (correction_detail["year_month"] if correction_detail else "record")
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
            label="Export Gold Standard ERP Payload (.json)",
            data=json_bytes,
            file_name=f"ERP_Payload_{tender_id}_{file_month_tag}.json",
            mime="application/json",
        )


# TENDER REGISTRY (Sidebar UI)
with st.sidebar:
    st.header("Tender Registry")
    registry_mode = st.radio("Mode", ["Anchor New Tender", "Open Existing Tender", "Correct Prior Escalation"])
    st.markdown("---")

    trigger_anchor = False
    trigger_check = False
    trigger_calculate_escalation = False
    trigger_preview_correction = False
    selected_tender = None
    check_month = None
    check_month_is_anniversary = False
    correction_tender = None
    correction_year_month = None
    correction_figure = None
    correction_reason = None
    correction_cascade_mode = "ISOLATED"

    if registry_mode == "Anchor New Tender":
        st.header("Tender Configuration Panel")
        st.write("Input official contract parameters below. This locks the anchor CPI once — every later check reads it back from the registry.")

        tender_id = st.text_input("Tender reference ID / Number", value="TENDER-LP-2025")
        tender_name = st.text_input("Tender / Project Name", value="Limpopo Catering Project")

        baseline_type = st.radio(
            "Contract Structure Baseline Type",
            ["Monthly Base Recurring Invoice Value", "Total Annual Contract Allocation Value"],
        )

        # Only meaningful for Annual-baseline tenders (it governs how Annual
        # Escalation derives each year's adjusted price -- see the Calculate
        # Annual Escalation branch below); fixed for Monthly tenders since
        # they never go through that flow.
        if baseline_type == "Total Annual Contract Allocation Value":
            cpa_formula_type = st.radio(
                "Annual CPA Formula Type (set once, permanent for this tender)",
                ["CUMULATIVE_FROM_ORIGINAL", "COMPOUND_FROM_PRIOR_YEAR"],
                format_func=lambda x: {
                    "CUMULATIVE_FROM_ORIGINAL": "Cumulative from Original Anchor",
                    "COMPOUND_FROM_PRIOR_YEAR": "Compound from Prior Year",
                }[x],
            )
        else:
            cpa_formula_type = "CUMULATIVE_FROM_ORIGINAL"

        base_value = st.number_input("Base Contract Valuation (ZAR)", min_value=1.0, value=1000000.0, step=1000.0)

        start_date = st.date_input("Contract Official Execution / Start Date (Anchor Month)", value=datetime.date(2025, 1, 1))
        end_date = st.date_input("Contract Official Expiration / End Date", value=datetime.date(2026, 6, 1))

        st.markdown("---")
        trigger_anchor = st.button("Anchor Tender")

    elif registry_mode == "Open Existing Tender":
        st.header("Open Existing Tender")
        tenders = list_tenders()
        if not tenders:
            st.info("No tenders anchored yet. Switch to 'Anchor New Tender' first.")
        else:
            options = {f"{t['tender_id']} - {t['tender_name']}": t["tender_id"] for t in tenders}
            picked_label = st.selectbox("Select Tender", list(options.keys()))
            selected_tender = get_tender(options[picked_label])

            st.caption(f"Baseline: {selected_tender['baseline_type']}")
            if selected_tender["baseline_type"] == "Total Annual Contract Allocation Value":
                st.caption(f"CPA Formula: {selected_tender.get('cpa_formula_type', 'CUMULATIVE_FROM_ORIGINAL')}")
            st.caption(f"Original Anchor (permanent): {selected_tender['original_anchor_month']} "
                        f"@ CPI {selected_tender['original_anchor_cpi']} "
                        f"(base R{selected_tender['original_base_value']:,.2f})")
            st.caption(f"Current: {selected_tender['current_anchor_month']} "
                        f"@ CPI {selected_tender['current_anchor_cpi']} "
                        f"(adjusted price R{selected_tender['current_adjusted_price']:,.2f})")

            # Every month on/after the CURRENT anchor is selectable for BOTH
            # baseline types -- an annual tender isn't limited to its
            # anniversary date, that's just the one that gets
            # highlighted/defaulted to, and the only one "Calculate Annual
            # Escalation" is offered for (see below).
            archive_dates = pd.read_csv(ARCHIVE_PATH)["Date"].astype(str).tolist()
            eligible_months = [d for d in archive_dates if d >= selected_tender["current_anchor_month"]]
            anniversary_months = set(_anniversary_months(selected_tender["current_anchor_month"], eligible_months))
            is_annual = selected_tender["baseline_type"] == "Total Annual Contract Allocation Value"

            if not eligible_months:
                st.info("No eligible check month yet for this tender.")
            else:
                default_index = len(eligible_months) - 1
                if is_annual and anniversary_months:
                    latest_anniversary = max(anniversary_months)
                    default_index = eligible_months.index(latest_anniversary)

                check_month = st.selectbox(
                    "Select Check Month", eligible_months, index=default_index,
                    format_func=lambda m: _month_label(m, selected_tender["current_anchor_month"], anniversary_months),
                )
                check_month_is_anniversary = check_month in anniversary_months
                st.markdown("---")

                if is_annual and check_month_is_anniversary:
                    trigger_check = st.button("Run Monthly Check (preview, no registry change)")
                    trigger_calculate_escalation = st.button("Calculate Annual Escalation (proposal only)")
                else:
                    trigger_check = st.button("Run Monthly Check")

    else:  # registry_mode == "Correct Prior Escalation"
        st.header("Correct Prior Escalation")
        st.write("An approved annual price is never edited in place. This layers a new, "
                  "separately dated correction on top -- the original approved figure is kept forever.")
        correctable = [t for t in list_tenders() if t.get("escalation_history")]
        if not correctable:
            st.info("No tenders have any approved Annual Escalation yet to correct.")
        else:
            options = {f"{t['tender_id']} - {t['tender_name']}": t["tender_id"] for t in correctable}
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
            is_compound_tender = correction_tender.get("cpa_formula_type") == "COMPOUND_FROM_PRIOR_YEAR"
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
        st.session_state["last_result"] = {
            "banner": None,
            "tender_id": selected_tender["tender_id"], "tender_name": selected_tender["tender_name"],
            "baseline_type": selected_tender["baseline_type"], "base_value": check_base_price,
            "start_date_str": selected_tender["start_date"], "end_date_str": selected_tender["end_date"],
            "timeline_results": timeline_results, "stage_results": stage_results,
            "extras": extras, "output_hash": output_hash,
            "document_title": "Monthly Invoice Verification Record", "escalation_info": None,
            "correction_detail": None,
            # Every document for a tender that has ever been corrected shows
            # the full trail, not just the correction's own record.
            "correction_history": get_correction_history(selected_tender),
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
        formula_type = selected_tender.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL")
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
            "original_base_value": selected_tender["original_base_value"],
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
        f"Original Baseline (permanent, {pend['original_anchor_month']}): "
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
                        "original_base_zar": updated["original_base_value"],
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
            "cpa_formula_type": correction_tender.get("cpa_formula_type"),
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

    corrector_name = st.text_input(
        "Approver Name / Role (required to apply)", key="corrector_input",
        placeholder="e.g. J. Naidoo, SCM Manager",
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
    )

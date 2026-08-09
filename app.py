import datetime
import json

import pandas as pd
import streamlit as st

from core.validator import SCMDataValidator
from core.tender_registry import get_tender, list_tenders, record_escalation, save_tender, tender_exists
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


def _month_label(month: str, anchor_month: str, anniversary_months: set) -> str:
    if month in anniversary_months:
        year_n = _months_since_anchor(anchor_month, month) // 12
        return f"Year {year_n} Anniversary - {month}"
    return month


def render_result_block(tender_id, tender_name, baseline_type, base_value,
                          start_date_str, end_date_str, timeline_results,
                          stage_results, extras, output_hash,
                          document_title="Audit-Ready Compliance Record",
                          escalation_info=None):
    """Shared output rendering for the anchor event and every later
    monthly/annual check -- metrics, the real 10-stage panel, the results
    table, and the PDF/JSON downloads. Used identically by "Anchor New
    Tender" and "Open Existing Tender" so a check never needs its own
    separate rendering path.

    document_title / escalation_info: different purpose, different legal
    weight, different downstream effect on the contract -- see
    utils/document_gen.py's build_audit_pdf() docstring for the full
    explanation. escalation_info, when set, is a dict with effective_date,
    old_base_zar, new_base_zar for a formal Annual Escalation.
    """
    inception_data = timeline_results[0]
    latest_data = timeline_results[-1]

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

    # METRIC DISPLAY MATRIX (Top Layer Visualization)
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

    st.subheader("Audit Lineage")
    st.dataframe(pd.DataFrame(timeline_results), use_container_width=True)

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
        "validation_pipeline": stage_results,
        "lineage": extras.get("lineage", {}),
        "outliers": extras.get("outliers", []),
        "output_hash": output_hash,
        "audit_lineage": timeline_results,
        "escalation": escalation_info,
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
        output_hash=output_hash,
    )

    out_col1, out_col2 = st.columns(2)
    with out_col1:
        st.download_button(
            label="Download Audit-Ready PDF Record",
            data=pdf_bytes,
            file_name=f"Audit_Record_{tender_id}_{latest_data['month']}.pdf",
            mime="application/pdf",
        )
    with out_col2:
        st.download_button(
            label="Export Gold Standard ERP Payload (.json)",
            data=json_bytes,
            file_name=f"ERP_Payload_{tender_id}_{latest_data['month']}.json",
            mime="application/json",
        )


# TENDER REGISTRY (Sidebar UI)
with st.sidebar:
    st.header("Tender Registry")
    registry_mode = st.radio("Mode", ["Anchor New Tender", "Open Existing Tender"])
    st.markdown("---")

    trigger_anchor = False
    trigger_check = False
    trigger_calculate_escalation = False
    selected_tender = None
    check_month = None
    check_month_is_anniversary = False

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

    else:
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
            }
        except ValueError as e:
            st.error(f"Validation Pipeline Halted: {e}")
        except Exception as e:
            st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

elif trigger_check and selected_tender and check_month:
    try:
        validator = SCMDataValidator(ARCHIVE_PATH)
        # A non-mutating preview against whatever is CURRENTLY in effect --
        # the original tender submission's anchor only matters for how
        # Annual Escalation derives a new figure (see below), not for this.
        timeline_results, stage_results, extras, output_hash = validator.run_monthly_check(
            anchor_month=selected_tender["current_anchor_month"],
            anchor_cpi_value=selected_tender["current_anchor_cpi"],
            check_month=check_month,
            tender_id=selected_tender["tender_id"],
        )
        st.session_state["last_result"] = {
            "banner": None,
            "tender_id": selected_tender["tender_id"], "tender_name": selected_tender["tender_name"],
            "baseline_type": selected_tender["baseline_type"], "base_value": selected_tender["current_adjusted_price"],
            "start_date_str": selected_tender["start_date"], "end_date_str": selected_tender["end_date"],
            "timeline_results": timeline_results, "stage_results": stage_results,
            "extras": extras, "output_hash": output_hash,
            "document_title": "Monthly Invoice Verification Record", "escalation_info": None,
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
        # from whatever the last approved escalation left as current.
        formula_type = selected_tender.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL")
        if formula_type == "CUMULATIVE_FROM_ORIGINAL":
            calc_anchor_month = selected_tender["original_anchor_month"]
            calc_anchor_cpi = selected_tender["original_anchor_cpi"]
            calc_base_price = selected_tender["original_base_value"]
        else:  # COMPOUND_FROM_PRIOR_YEAR
            calc_anchor_month = selected_tender["current_anchor_month"]
            calc_anchor_cpi = selected_tender["current_anchor_cpi"]
            calc_base_price = selected_tender["current_adjusted_price"]

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
                if pend["formula_type"] == "CUMULATIVE_FROM_ORIGINAL":
                    calc_anchor_month = current["original_anchor_month"]
                    calc_anchor_cpi = current["original_anchor_cpi"]
                    calc_base_price = current["original_base_value"]
                else:
                    calc_anchor_month = current["current_anchor_month"]
                    calc_anchor_cpi = current["current_anchor_cpi"]
                    calc_base_price = current["current_adjusted_price"]

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
                }
                del st.session_state["pending_escalation"]
                st.rerun()
            except ValueError as e:
                st.error(f"Validation Pipeline Halted: {e}")
            except Exception as e:
                st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

# Renders whatever the most recent successful anchor/check/APPLIED escalation
# produced. Lives outside the trigger blocks above so it survives the
# reruns that clicking either download button inside it causes (see
# comment above).
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
    )

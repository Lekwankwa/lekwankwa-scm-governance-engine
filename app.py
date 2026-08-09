import datetime
import json

import pandas as pd
import streamlit as st

from core.validator import SCMDataValidator
from core.tender_registry import get_tender, list_tenders, save_tender, tender_exists
from utils.document_gen import build_audit_pdf

ARCHIVE_PATH = "data/stats_sa_cpi_archive.csv"

# Set up clean, institutional dark theme dashboard configuration
st.set_page_config(page_title="Lekwankwa SCM Engine", layout="wide")

st.title("Municipal SCM Governance Engine")
st.subheader("Data-Driven Procurement Protection and Internal Audit Control Gateway")
st.markdown("---")


def _anniversary_months(anchor_month: str, available_months: list) -> list:
    """Months that are exact 12-month multiples on/after anchor_month.

    Used to restrict the check-month picker for annually-billed tenders to
    only the dates that are legally meaningful escalation points, instead of
    letting a Product 3 (Annual Anniversary Engine) tender be checked against
    an arbitrary mid-year month.
    """
    anchor_dt = datetime.datetime.strptime(anchor_month, "%Y-%m")
    result = []
    for m in available_months:
        m_dt = datetime.datetime.strptime(m, "%Y-%m")
        months_diff = (m_dt.year - anchor_dt.year) * 12 + (m_dt.month - anchor_dt.month)
        if months_diff >= 0 and months_diff % 12 == 0:
            result.append(m)
    return result


def render_result_block(tender_id, tender_name, baseline_type, base_value,
                          start_date_str, end_date_str, timeline_results,
                          stage_results, extras, output_hash):
    """Shared output rendering for both the anchor event and every later
    monthly/annual check -- metrics, the real 10-stage panel, the results
    table, and the PDF/JSON downloads. Used identically by "Anchor New
    Tender" and "Open Existing Tender" so a check never needs its own
    separate rendering path.
    """
    inception_data = timeline_results[0]
    latest_data = timeline_results[-1]

    # METRIC DISPLAY MATRIX (Top Layer Visualization)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Anchor CPI", f"{inception_data['anchor_cpi']}")
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
        "tender_metadata": tender_metadata,
        "validation_pipeline": stage_results,
        "lineage": extras.get("lineage", {}),
        "outliers": extras.get("outliers", []),
        "output_hash": output_hash,
        "audit_lineage": timeline_results,
    }
    json_bytes = json.dumps(gold_standard_json, indent=4, default=str).encode("utf-8")

    pdf_bytes = build_audit_pdf(
        tender_metadata=tender_metadata,
        stage_results=stage_results,
        timeline_results=timeline_results,
        lineage=extras.get("lineage", {}),
        outliers=extras.get("outliers", []),
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
    selected_tender = None
    check_month = None

    if registry_mode == "Anchor New Tender":
        st.header("Tender Configuration Panel")
        st.write("Input official contract parameters below. This locks the anchor CPI once — every later check reads it back from the registry.")

        tender_id = st.text_input("Tender reference ID / Number", value="TENDER-LP-2025")
        tender_name = st.text_input("Tender / Project Name", value="Limpopo Catering Project")

        baseline_type = st.radio(
            "Contract Structure Baseline Type",
            ["Monthly Base Recurring Invoice Value", "Total Annual Contract Allocation Value"],
        )

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
            st.caption(f"Anchor: {selected_tender['anchor_month']} @ CPI {selected_tender['anchor_cpi_value']}")
            st.caption(f"Base Value (ZAR): {selected_tender['base_value']:,.2f}")

            archive_dates = pd.read_csv(ARCHIVE_PATH)["Date"].astype(str).tolist()
            eligible_months = [d for d in archive_dates if d >= selected_tender["anchor_month"]]
            if selected_tender["baseline_type"] == "Total Annual Contract Allocation Value":
                eligible_months = _anniversary_months(selected_tender["anchor_month"], eligible_months)

            if not eligible_months:
                st.info("No eligible check month yet for this tender's baseline type.")
            else:
                check_month = st.selectbox("Select Check Month", eligible_months, index=len(eligible_months) - 1)
                st.markdown("---")
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

            save_tender({
                "tender_id": tender_id,
                "tender_name": tender_name,
                "base_value": base_value,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "baseline_type": baseline_type,
                "anchor_month": anchor_record["month"],
                "anchor_cpi_value": anchor_record["anchor_cpi"],
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
            }
        except ValueError as e:
            st.error(f"Validation Pipeline Halted: {e}")
        except Exception as e:
            st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

elif trigger_check and selected_tender and check_month:
    try:
        validator = SCMDataValidator(ARCHIVE_PATH)
        timeline_results, stage_results, extras, output_hash = validator.run_monthly_check(
            anchor_month=selected_tender["anchor_month"],
            anchor_cpi_value=selected_tender["anchor_cpi_value"],
            check_month=check_month,
            tender_id=selected_tender["tender_id"],
        )
        st.session_state["last_result"] = {
            "banner": None,
            "tender_id": selected_tender["tender_id"], "tender_name": selected_tender["tender_name"],
            "baseline_type": selected_tender["baseline_type"], "base_value": selected_tender["base_value"],
            "start_date_str": selected_tender["start_date"], "end_date_str": selected_tender["end_date"],
            "timeline_results": timeline_results, "stage_results": stage_results,
            "extras": extras, "output_hash": output_hash,
        }
    except ValueError as e:
        st.error(f"Validation Pipeline Halted: {e}")
    except Exception as e:
        st.error(f"Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

# Renders whatever the most recent successful anchor/check produced. Lives
# outside the trigger blocks above so it survives the reruns that clicking
# either download button inside it causes (see comment above).
if "last_result" in st.session_state:
    res = st.session_state["last_result"]
    if res["banner"]:
        st.success(res["banner"])
    render_result_block(
        res["tender_id"], res["tender_name"], res["baseline_type"], res["base_value"],
        res["start_date_str"], res["end_date_str"], res["timeline_results"],
        res["stage_results"], res["extras"], res["output_hash"],
    )

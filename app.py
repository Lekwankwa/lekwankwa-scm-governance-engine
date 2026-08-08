import datetime
import json

import streamlit as st

from core.validator import SCMDataValidator

# Set up clean, institutional dark theme dashboard configuration
st.set_page_config(page_title="Lekwankwa SCM Engine", layout="wide")

st.title("🇿🇦 Municipal SCM Governance Engine")
st.subheader("Data-Driven Procurement Protection & Internal Audit Control Gateway")
st.markdown("---")

# 📋 THE TENDER CONFIGURATION PANEL (Sidebar UI)
with st.sidebar:
    st.header("📋 Tender Configuration Panel")
    st.write("Input official contract parameters below:")

    tender_id = st.text_input("Tender reference ID / Number", value="TENDER-LP-2025")
    tender_name = st.text_input("Tender / Project Name", value="Limpopo Catering Project")

    baseline_type = st.radio(
        "Contract Structure Baseline Type",
        ["Monthly Base Recurring Invoice Value", "Total Annual Contract Allocation Value"],
    )

    base_value = st.number_input("Base Contract Valuation (ZAR)", min_value=1.0, value=1000000.0, step=1000.0)

    # Date Pickers mapping your chronological parameters
    start_date = st.date_input("Contract Official Execution / Start Date", value=datetime.date(2025, 1, 1))
    end_date = st.date_input("Contract Official Expiration / End Date", value=datetime.date(2026, 6, 1))

    st.markdown("---")
    trigger_calculation = st.button("🚀 Run SCM Verification")

# MAIN FRAME PROCESSING GRAPHICS
if trigger_calculation:
    if start_date >= end_date:
        st.error("❌ Operational Error: Expiration/End Date must be strictly after the Start Date.")
    else:
        try:
            # Initialize engine and run the gated timeline loop
            # In production this targets: data/stats_sa_cpi_archive.csv
            validator = SCMDataValidator("data/stats_sa_cpi_archive.csv")
            timeline_results, stage_results, extras, output_hash = validator.process_timeline_loop(
                str(start_date), str(end_date), tender_id=tender_id
            )

            if not timeline_results:
                st.error("⚠️ No processable records were found in the requested date range.")
            else:
                # Fetch terminal calculation row strings (the last processed row)
                inception_data = timeline_results[0]
                anniversary_data = timeline_results[-1]

                # 📊 METRIC DISPLAY MATRIX (Top Layer Visualization)
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Month 1 Anchor CPI", f"{inception_data['anchor_cpi']}")
                with col2:
                    st.metric("Current Vintage CPI", f"{anniversary_data['current_cpi']}")
                with col3:
                    final_drift = anniversary_data["drift_percentage"]
                    escalated_total = base_value * (1 + (final_drift / 100))
                    st.metric("Total Cumulative Drift %", f"{final_drift:+.2f}%")

                st.markdown("---")

                # ⚙️ 10-STAGE AUTOMATED PIPELINE VISUALIZATION
                # Renders the REAL stage-by-stage results returned by the validator
                # (same 10-stage pattern used across the Lekwankwa data platform:
                # 1a / 1b / 1c / 2 / 3 / 4 / 5 / 6 / 7 / 8), not a static checklist.
                overall_ok = all(s["status"] != "FAIL" for s in stage_results)
                if overall_ok:
                    st.success("🟢 10-Stage Data Integrity Validation Pipeline Status: [PASSED]")
                else:
                    st.error("🔴 10-Stage Data Integrity Validation Pipeline Status: [FAILED]")

                status_icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
                cols = st.columns(5)
                for i, s in enumerate(stage_results):
                    icon = status_icon.get(s["status"], "•")
                    cols[i % 5].markdown(f"{icon} **Stage {s['stage']} — {s['name']}**")
                    with cols[i % 5].expander("detail", expanded=False):
                        st.caption(s["detail"])

                st.markdown("---")

                # 📦 OUTPUT GENERATION INTERFACE
                st.subheader("📦 Automated Output Package Registry")
                st.write("Download certified compliance payloads directly below:")

                # Format JSON Payload string
                gold_standard_json = {
                    "tender_metadata": {
                        "id": tender_id,
                        "name": tender_name,
                        "type": baseline_type,
                        "base_zar": base_value,
                        "start": str(start_date),
                        "end": str(end_date),
                    },
                    "validation_pipeline": stage_results,
                    "lineage": extras.get("lineage", {}),
                    "outliers": extras.get("outliers", []),
                    "output_hash": output_hash,
                    "audit_lineage": timeline_results,
                }
                json_bytes = json.dumps(gold_standard_json, indent=4, default=str).encode("utf-8")

                out_col1, out_col2 = st.columns(2)
                with out_col1:
                    st.download_button(
                        label="📥 Download Audit-Ready PDF Record",
                        data=b"Simulated PDF Binary Stream Data for Demo Presentation",
                        file_name=f"Audit_Record_{tender_id}.pdf",
                        mime="application/pdf",
                    )
                with out_col2:
                    st.download_button(
                        label="📥 Export Gold Standard ERP Payload (.json)",
                        data=json_bytes,
                        file_name=f"ERP_Payload_{tender_id}.json",
                        mime="application/json",
                    )
        except ValueError as e:
            # Raised by the validator when a required stage FAILs — show the
            # real stage/reason instead of a generic error.
            st.error(f"🔴 Validation Pipeline Halted: {e}")
        except Exception as e:
            st.error(f"⚠️ Technical Configuration Alert: Ensure your local historical database file exists. Error: {e}")

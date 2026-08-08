"""
document_gen.py — FPDF2 automated report generator (PLACEHOLDER).

Not implemented yet. This module is where the real "Audit-Ready PDF Record"
gets built with fpdf2, replacing the simulated binary stream that
app.py currently sends via st.download_button() for the PDF export button.

Intended usage once built:
    from utils.document_gen import build_audit_pdf
    pdf_bytes = build_audit_pdf(tender_metadata, stage_results, timeline_results)

TODO:
    - Render tender metadata (ID, name, base value, contract dates).
    - Render the 10-stage validation pipeline result table (pass/warn/fail +
      detail per stage, from core.validator.SCMDataValidator.run_10_stage_pipeline).
    - Render the drift/escalation summary table (month, anchor CPI, current
      CPI, drift %) from the audit_lineage records.
    - Embed the audit_integrity_hash / output_hash so the PDF and the JSON
      export are cryptographically tied to the same run.
"""

"""
generate_sample_invoice.py -- builds the 4 demo contractor invoice PDFs used
by run_demo.py.

These are synthetic demo invoices only, not real ones. Their labeled fields
are written one per line, using each field's primary alias from
scm_parser.FIELD_LABELS, so scm_parser.py's regex-based extraction round-trips
reliably. All figures are pre-computed against the REAL Stats SA CPI archive
(data/stats_sa_cpi_archive.csv) or the mock PPI archive
(data/mock_ppi_archive.csv) so each scenario resolves deterministically:

  1. invoice_compliant.pdf     -- CPI-based, claimed % within the allowable
                                   boundary -> PASSED.
  2. invoice_overcharge.pdf    -- mock-PPI-based (Fuel and Petroleum
                                   Products), claimed % well above the
                                   allowable boundary -> FAILED / HARD-LOCK.
  3. invoice_manual_review.pdf -- Civil Construction (JBCC/CPAP), an excluded
                                   sector -> MANUAL REVIEW REQUIRED.
  4. invoice_pending_category.pdf -- Food and Beverage Supplies, a Tier 2
                                   category recognized but not yet enabled
                                   for automated verification -> CATEGORY_PENDING.

Usage:
    python samples/generate_sample_invoice.py
"""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

OUTPUT_DIR = Path(__file__).parent

SAMPLE_SCENARIOS = [
    {
        "filename": "invoice_compliant.pdf",
        "narrative": (
            "This invoice reflects the contractor's requested annual price "
            "escalation for consulting services rendered under the "
            "municipality's standing panel appointment."
        ),
        "fields": [
            ("Contract Reference Number", "CON-2025-0417"),
            ("Invoice Date", "2026-07-15"),
            ("Baseline Contract Date", "2025-07-01"),
            ("Procurement Category", "Consulting Services"),
            ("Claimed Escalation", "3.50%"),
            ("Original Contract Value", "R150,000.00"),
        ],
    },
    {
        "filename": "invoice_overcharge.pdf",
        "narrative": (
            "This invoice reflects the contractor's requested annual price "
            "escalation for diesel supplied under the municipality's fleet "
            "fuel contract."
        ),
        "fields": [
            ("Contract Reference Number", "CON-2025-0892"),
            ("Invoice Date", "2026-07-20"),
            ("Baseline Contract Date", "2025-07-01"),
            ("Procurement Category", "Fuel and Petroleum Products"),
            ("Claimed Escalation", "12.00%"),
            ("Original Contract Value", "R85,000.00"),
        ],
    },
    {
        "filename": "invoice_manual_review.pdf",
        "narrative": (
            "This invoice reflects the contractor's requested price "
            "escalation for an infrastructure construction tender governed "
            "by JBCC/CPAP contract conditions."
        ),
        "fields": [
            ("Contract Reference Number", "CON-2025-1102"),
            ("Invoice Date", "2026-03-10"),
            ("Baseline Contract Date", "2025-01-01"),
            ("Procurement Category", "Civil Construction (JBCC/CPAP)"),
            ("Claimed Escalation", "5.00%"),
            ("Original Contract Value", "R2,400,000.00"),
        ],
    },
    {
        "filename": "invoice_pending_category.pdf",
        "narrative": (
            "This invoice reflects the contractor's requested annual price "
            "escalation for food and beverage supplies delivered under the "
            "municipality's catering supply contract."
        ),
        "fields": [
            ("Contract Reference Number", "CON-2025-0530"),
            ("Invoice Date", "2026-07-10"),
            ("Baseline Contract Date", "2025-07-01"),
            ("Procurement Category", "Food and Beverage Supplies"),
            ("Claimed Escalation", "4.00%"),
            ("Original Contract Value", "R60,000.00"),
        ],
    },
]


def _safe(text) -> str:
    """Core Helvetica font is Latin-1 only -- same defensive pattern as
    utils/document_gen.py's _safe() helper."""
    return str(text).encode("latin-1", errors="replace").decode("latin-1")


def _build_invoice_pdf(output_path: Path, fields: list, narrative: str) -> None:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "CONTRACTOR TAX INVOICE", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(0, 6, _safe(narrative))
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Invoice Details", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    for label, value in fields:
        # One "Label: value" pair per line -- this exact layout is what
        # scm_parser._extract_field()'s per-line regex match depends on.
        pdf.cell(0, 8, _safe(f"{label}: {value}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0, 5,
        _safe(
            "This is a synthetic demo invoice generated by "
            "samples/generate_sample_invoice.py for the Municipal SCM Audit "
            "Chatbot demo. It is not a real contractor document."
        ),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output_path))


def main() -> list:
    """Generate all 4 sample invoices into samples/. Returns the list of
    written file paths."""
    written = []
    for scenario in SAMPLE_SCENARIOS:
        out_path = OUTPUT_DIR / scenario["filename"]
        _build_invoice_pdf(out_path, scenario["fields"], scenario["narrative"])
        written.append(out_path)
        print(f"[generate_sample_invoice] wrote {out_path}")
    return written


if __name__ == "__main__":
    main()

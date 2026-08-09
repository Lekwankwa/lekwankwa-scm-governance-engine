"""
document_gen.py — FPDF2 automated report generator.

Builds the real "Audit-Ready PDF Record" that app.py hands to
st.download_button() for the PDF export button. Everything on the page
comes from the same run that produced the JSON export, so the two files
are cryptographically tied together via output_hash (see Stage 7 /
Stage 5 in core/validator.py).

Usage:
    from utils.document_gen import build_audit_pdf
    pdf_bytes = build_audit_pdf(
        tender_metadata=tender_metadata,   # dict: id, name, type, base_zar, start, end
        stage_results=stage_results,       # list of {"stage","name","status","detail"} from validator
        timeline_results=timeline_results, # list of {"month","anchor_cpi","current_cpi","drift_percentage"}
        lineage=extras.get("lineage", {}),
        outliers=extras.get("outliers", []),
        output_hash=output_hash,
        document_title="Monthly Invoice Verification Record",  # or "Tender Anchor
            # Record" / "Annual Contract Price Adjustment Record" -- see build_audit_pdf()
        escalation_info=None,  # dict with effective_date/old_base_zar/new_base_zar for
            # a formal Annual Escalation record, otherwise None
    )
"""
from __future__ import annotations

from datetime import datetime

from fpdf import FPDF
from fpdf.enums import XPos, YPos

STATUS_COLOR = {
    "PASS": (34, 139, 34),   # green
    "WARN": (204, 140, 0),   # amber
    "FAIL": (178, 34, 34),   # red
}
HEADER_FILL = (28, 43, 74)      # dark institutional blue
HEADER_TEXT = (255, 255, 255)
ACCENT = (28, 43, 74)

_UNICODE_ASCII_MAP = {
    "—": "-", "–": "-",           # em dash, en dash
    "‘": "'", "’": "'",           # curly single quotes
    "“": '"', "”": '"',           # curly double quotes
    "…": "...",                        # ellipsis
}


def _safe(text) -> str:
    """Core Helvetica/Courier fonts are Latin-1 only. Stage detail strings
    are dynamic (built from validator.py messages, archive filenames, etc.)
    and some use em dashes — swap known Unicode punctuation for ASCII
    equivalents, then fall back to a safe replacement for anything else so
    a stray character never crashes PDF generation."""
    text = str(text)
    for bad, good in _UNICODE_ASCII_MAP.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", errors="replace").decode("latin-1")


class AuditPDF(FPDF):
    # Overridden per-instance right after construction (see build_audit_pdf)
    # so header(), which FPDF calls automatically on every page, can render
    # a title that differs by record type (anchor vs monthly check vs
    # annual escalation) instead of one hardcoded subtitle.
    document_title = "Audit-Ready Compliance Record"

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*ACCENT)
        self.cell(0, 8, _safe("Lekwankwa Municipal SCM Governance Engine"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(90, 90, 90)
        self.cell(0, 6, _safe(self.document_title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(6)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(130, 130, 130)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def _section_title(pdf: FPDF, text: str):
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*ACCENT)
    pdf.ln(2)
    pdf.cell(0, 8, _safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)


def _kv_table(pdf: FPDF, rows: list[tuple[str, str]], label_width: float = 75):
    pdf.set_font("Helvetica", "", 10)
    for label, value in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_width, 7, _safe(label), border=0, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "", 10)
        # new_x=LMARGIN/new_y=NEXT: multi_cell otherwise leaves x at the right
        # edge after wrapping, which would break the next row's layout.
        pdf.multi_cell(0, 7, _safe(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_audit_pdf(
    tender_metadata: dict,
    stage_results: list,
    timeline_results: list,
    lineage: dict = None,
    outliers: list = None,
    output_hash: str = "N/A",
    document_title: str = "Audit-Ready Compliance Record",
    escalation_info: dict = None,
) -> bytes:
    """
    document_title distinguishes what kind of record this is -- different
    purpose, different legal weight, different downstream effect on the
    contract -- e.g. "Tender Anchor Record", "Monthly Invoice Verification
    Record", or "Annual Contract Price Adjustment Record".

    escalation_info, when set, is a dict with effective_date, old_base_zar,
    new_base_zar: a formal Annual Escalation actually changes the contract's
    baseline value, so that gets its own explicit notice section up front
    rather than being buried in the metadata table.
    """
    lineage = lineage or {}
    outliers = outliers or []

    pdf = AuditPDF()
    pdf.document_title = document_title
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    if escalation_info:
        pdf.set_draw_color(*ACCENT)
        pdf.set_fill_color(255, 245, 225)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(0, 0, 0)
        notice = (
            "This is a formal annual price adjustment. Effective "
            f"{escalation_info['effective_date']}, the contract's baseline value "
            f"is revised from R{escalation_info['old_base_zar']:,.2f} to "
            f"R{escalation_info['new_base_zar']:,.2f}."
        )
        pdf.multi_cell(0, 7, _safe(notice), border=1, fill=True,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── Tender metadata ──────────────────────────────────────────────────
    _section_title(pdf, "1. Tender Metadata")
    _kv_table(pdf, [
        ("Tender ID", tender_metadata.get("id", "")),
        ("Tender Name", tender_metadata.get("name", "")),
        ("Baseline Type", tender_metadata.get("type", "")),
        ("Base Value (ZAR)", f"{tender_metadata.get('base_zar', 0):,.2f}"),
        ("Contract Start", tender_metadata.get("start", "")),
        ("Contract End", tender_metadata.get("end", "")),
        ("Report Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ])

    # ── CPI drift summary ────────────────────────────────────────────────
    _section_title(pdf, "2. CPI Drift & Payout Calculation")
    if timeline_results:
        anchor = timeline_results[0]
        latest = timeline_results[-1]
        base_zar = float(tender_metadata.get("base_zar", 0))
        drift_pct = latest["drift_percentage"]
        approved_max_payout = base_zar * (1 + drift_pct / 100)
        _kv_table(pdf, [
            ("Anchor CPI", f"{anchor['anchor_cpi']} ({anchor.get('anchor_month', anchor['month'])})"),
            ("Current Vintage CPI", f"{latest['current_cpi']} ({latest['month']})"),
            ("Cumulative Drift %", f"{drift_pct:+.4f}%"),
            ("Approved Maximum Payout (ZAR)", f"{approved_max_payout:,.2f}"),
        ])
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 7, _safe("No timeline records were processed for this run."),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ── 10-stage validation pipeline ─────────────────────────────────────
    # Rendered as one block per stage (not a fixed-height grid) so a long
    # detail string — e.g. a Stage 1c DATA_GAP listing many missing months —
    # can wrap to multiple lines without misaligning neighbouring cells.
    _section_title(pdf, "3. 10-Stage Data Integrity Validation Pipeline")
    for s in stage_results:
        color = STATUS_COLOR.get(s["status"], (0, 0, 0))
        detail = s.get("detail", "")
        if len(detail) > 300:
            detail = detail[:297] + "..."

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.cell(150, 6.5, _safe(f"Stage {s['stage']} - {s['name']}"), new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_text_color(*color)
        pdf.cell(40, 6.5, _safe(f"[{s['status']}]"), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        pdf.set_text_color(70, 70, 70)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.multi_cell(0, 5, _safe(detail), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)

    overall_ok = all(s["status"] != "FAIL" for s in stage_results)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*(STATUS_COLOR["PASS"] if overall_ok else STATUS_COLOR["FAIL"]))
    pdf.cell(0, 7, _safe(f"Overall Pipeline Status: {'PASSED' if overall_ok else 'FAILED'}"),
              new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)

    # ── Lineage & outliers ───────────────────────────────────────────────
    _section_title(pdf, "4. Lineage & Provenance")
    _kv_table(pdf, [
        ("Source File", lineage.get("source_file", "N/A")),
        ("Last Modified", lineage.get("last_modified", "N/A")),
        ("Row Count", str(lineage.get("row_count", "N/A"))),
    ])

    if outliers:
        _section_title(pdf, "5. Outlier Extraction")
        pdf.set_font("Helvetica", "", 9)
        for o in outliers:
            pdf.cell(0, 6, _safe(f"  - {o.get('Date', '?')}: CPI_Value = {o.get('CPI_Value', '?')}"),
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ── Audit integrity hash ─────────────────────────────────────────────
    pdf.ln(4)
    pdf.set_draw_color(*ACCENT)
    pdf.set_fill_color(240, 242, 247)
    pdf.set_font("Helvetica", "B", 9)
    pdf.multi_cell(0, 8, _safe("Audit Integrity Hash (SHA-256, truncated) - ties this PDF to the matching JSON export:"),
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Courier", "B", 11)
    pdf.set_fill_color(240, 242, 247)
    pdf.cell(0, 9, _safe(output_hash), border=1, fill=True, align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())

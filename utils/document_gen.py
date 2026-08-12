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
    """Two-column label/value layout where BOTH columns independently wrap
    within their own width and are structurally incapable of overlapping.

    Fixes a real bug: the previous version drew the label with cell(), which
    does NOT wrap or clip -- a label longer than label_width just overflows
    past the column boundary, while the cursor still only advances by
    exactly label_width (not by how far the overflow text actually
    reached). The value then started printing at that fixed offset,
    landing on top of the label's overflow (e.g. "Original Baseline (fixed
    against escalation rollups)" colliding with its own value). Using
    multi_cell for the label bounds it exactly the same way multi_cell
    already (correctly) bounds the value -- so no label/value pair through
    this function can ever collide again, regardless of label or value
    length, not just the one that happened to overflow first.

    Each column is positioned independently via set_xy() at a fixed x, and
    the row advances to whichever column needed more wrapped lines.
    """
    left_x = pdf.l_margin
    value_x = left_x + label_width
    value_width = pdf.w - pdf.r_margin - value_x
    for label, value in rows:
        row_y = pdf.get_y()

        pdf.set_xy(left_x, row_y)
        pdf.set_font("Helvetica", "B", 10)
        pdf.multi_cell(label_width, 7, _safe(label), new_x=XPos.LEFT, new_y=YPos.NEXT)
        label_bottom = pdf.get_y()

        pdf.set_xy(value_x, row_y)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(value_width, 7, _safe(value), new_x=XPos.LEFT, new_y=YPos.NEXT)
        value_bottom = pdf.get_y()

        pdf.set_xy(left_x, max(label_bottom, value_bottom))


def _format_metadata_value(field: str, value) -> str:
    """Currency formatting for original_base_value, plain string for every
    other correctable metadata field -- mirrors app.py's identically-named
    helper so the on-screen and PDF renderings never disagree."""
    if field == "original_base_value":
        try:
            return f"R{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def build_audit_pdf(
    tender_metadata: dict,
    stage_results: list = None,
    timeline_results: list = None,
    lineage: dict = None,
    outliers: list = None,
    output_hash: str = "N/A",
    document_title: str = "Audit-Ready Compliance Record",
    escalation_info: dict = None,
    correction_detail: dict = None,
    correction_history: list = None,
    metadata_correction_detail: dict = None,
    archive_detail: dict = None,
    full_escalation_history: list = None,
) -> bytes:
    """
    document_title distinguishes what kind of record this is -- different
    purpose, different legal weight, different downstream effect on the
    contract -- e.g. "Tender Anchor Record", "Monthly Invoice Verification
    Record", "Annual Contract Price Adjustment Record", or "Annual Price
    Correction Record".

    escalation_info, when set, is a dict with effective_date, old_base_zar,
    new_base_zar, and (once approved) approved_by/approved_at: a formal
    Annual Escalation actually changes the contract's baseline value, so
    that gets its own explicit notice section up front rather than being
    buried in the metadata table -- and per the approval-gated workflow,
    every applied escalation must show who approved it and when.

    stage_results / timeline_results are optional: a correction record
    (see correction_detail below) is a pure business/audit action, not a
    CPI-driven calculation -- no 10-stage gate runs for one -- so sections
    2 and 3 are skipped gracefully when these are empty, rather than
    showing a misleading "0 stages ran" block. Section numbers renumber
    themselves to match whichever sections actually render.

    correction_detail, when set, is a dict with year_month, original_figure,
    corrected_figure, reason, corrected_by, corrected_at, and cascade_mode
    (None for a non-compound tender or when correcting the latest year) --
    an approved figure is never edited in place, so a correction gets its
    own explicit notice, just like escalation_info.

    correction_history, when non-empty, is the tender's FULL correction
    history (core/tender_registry.py's get_correction_history()) -- passed
    to EVERY document generated for a tender that has ever been corrected,
    not just the correction's own record, so nobody reading e.g. a routine
    monthly check ever sees a final number with no trace of what changed.
    Now also carries METADATA_CORRECTION entries alongside the existing
    CORRECTION / STALE_INPUT_FLAG types -- one unified trail.

    metadata_correction_detail: same "just applied" notice pattern as
    correction_detail, but for a tender-metadata correction (see
    core/tender_registry.py's correct_tender_metadata()).

    archive_detail: {reason, archived_by, archived_at, just_archived} -- a
    formal "this tender was just archived" notice, or a calmer "you are
    viewing an archived tender" one for the Archived / Closed Contracts
    browse view.

    full_escalation_history: the tender's COMPLETE escalation_history
    (every year, not just the latest) -- only passed by the Archived browse
    view, so the exported PDF isn't missing anything the screen shows.
    """
    lineage = lineage or {}
    outliers = outliers or []
    stage_results = stage_results or []
    timeline_results = timeline_results or []
    correction_history = correction_history or []

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
        approval_line = ""
        if escalation_info.get("approved_by"):
            approval_line = (f" Approved by {escalation_info['approved_by']} "
                              f"on {escalation_info.get('approved_at', 'N/A')}.")
        notice = (
            "This is a formal annual price adjustment. Effective "
            f"{escalation_info['effective_date']}, the contract's baseline value "
            f"is revised from R{escalation_info['old_base_zar']:,.2f} to "
            f"R{escalation_info['new_base_zar']:,.2f}.{approval_line}"
        )
        pdf.multi_cell(0, 7, _safe(notice), border=1, fill=True,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

        # Full derivation chain, per the CPA formula requirement: the
        # original tender baseline stays visible and unchanged, the prior
        # year's adjusted price shows for a compounding tender, and the
        # formula used is explicit, so an auditor can trace the whole chain
        # from the original tender to the current price in this one document.
        is_compound = escalation_info.get("formula_type") == "COMPOUND_FROM_PRIOR_YEAR"
        derivation_rows = [
            ("Formula Type Used", escalation_info.get("formula_type", "N/A")),
            ("Original Baseline (fixed against escalation rollups)",
             f"{escalation_info.get('original_anchor_month', 'N/A')} -- "
             f"CPI {escalation_info.get('original_anchor_cpi', 'N/A')}, "
             f"Base R{escalation_info.get('original_base_zar', 0):,.2f}"),
        ]
        if is_compound:
            derivation_rows.append((
                "Prior Year Adjusted Price",
                f"{escalation_info.get('prior_anchor_month', 'N/A')} -- "
                f"R{escalation_info.get('prior_adjusted_price', 0):,.2f}",
            ))
        derivation_rows.append((
            "This Year's New Adjusted Price",
            f"{escalation_info['effective_date']} -- R{escalation_info['new_base_zar']:,.2f}",
        ))
        _kv_table(pdf, derivation_rows, label_width=85)
        pdf.ln(2)

    if correction_detail:
        cd = correction_detail
        pdf.set_draw_color(*ACCENT)
        pdf.set_fill_color(255, 228, 228)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(0, 0, 0)
        cascade_line = f" Cascade mode: {cd['cascade_mode']}." if cd.get("cascade_mode") else ""
        notice = (
            f"This is a formal correction to the approved {cd['year_month']} annual escalation "
            f"record. The figure is revised from R{cd['original_figure']:,.2f} to "
            f"R{cd['corrected_figure']:,.2f}. Reason: {cd['reason']}. Approved by "
            f"{cd['corrected_by']} on {cd.get('corrected_at', 'N/A')}.{cascade_line}"
        )
        pdf.multi_cell(0, 7, _safe(notice), border=1, fill=True,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    if metadata_correction_detail:
        mcd = metadata_correction_detail
        pdf.set_draw_color(*ACCENT)
        # Amber for a flagged (retroactive-impact) correction -- "flag for
        # human review" tone -- vs the routine correction's red, matching
        # app.py's identical distinction.
        pdf.set_fill_color(255, 245, 225) if mcd.get("retroactive_impact_flag") else pdf.set_fill_color(255, 228, 228)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(0, 0, 0)
        notice = (
            f"This is a formal correction to tender metadata. Field '{mcd['field']}' revised from "
            f"'{_format_metadata_value(mcd['field'], mcd['original_value'])}' to "
            f"'{_format_metadata_value(mcd['field'], mcd['corrected_value'])}'. Reason: {mcd['reason']}. "
            f"Approved by {mcd['corrected_by']} on {mcd.get('corrected_at', 'N/A')}."
        )
        if mcd.get("retroactive_impact_flag"):
            notice += f" FLAGGED FOR HUMAN REVIEW: {mcd.get('retroactive_impact_note', '')}"
        pdf.multi_cell(0, 7, _safe(notice), border=1, fill=True,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    if archive_detail:
        ad = archive_detail
        pdf.set_draw_color(*ACCENT)
        pdf.set_fill_color(255, 228, 228) if ad.get("just_archived") else pdf.set_fill_color(240, 242, 247)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_text_color(0, 0, 0)
        notice = (
            f"This tender is ARCHIVED. Reason: {ad.get('reason', 'N/A')}. Archived by "
            f"{ad.get('archived_by', 'N/A')} on {ad.get('archived_at', 'N/A')}. It is hidden from the "
            "active working list; its full history remains fully retrievable."
        )
        pdf.multi_cell(0, 7, _safe(notice), border=1, fill=True,
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # Sections number themselves as they render, since a correction record
    # has no CPI/stage sections to show (see docstring) and a routine check
    # has no correction-history section to show -- fixed numbers would
    # either skip or collide.
    section_num = 1

    # ── Tender metadata (always) ─────────────────────────────────────────
    _section_title(pdf, f"{section_num}. Tender Metadata")
    _kv_table(pdf, [
        ("Tender ID", tender_metadata.get("id", "")),
        ("Tender Name", tender_metadata.get("name", "")),
        ("Baseline Type", tender_metadata.get("type", "")),
        ("Base Value (ZAR)", f"{tender_metadata.get('base_zar', 0):,.2f}"),
        ("Contract Start", tender_metadata.get("start", "")),
        ("Contract End", tender_metadata.get("end", "")),
        ("Report Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ])
    section_num += 1

    # ── CPI drift summary (only for a CPI-driven record) ─────────────────
    if timeline_results:
        _section_title(pdf, f"{section_num}. CPI Drift & Payout Calculation")
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
        section_num += 1

    # ── 10-stage validation pipeline (only when a gate actually ran) ─────
    # Rendered as one block per stage (not a fixed-height grid) so a long
    # detail string — e.g. a Stage 1c DATA_GAP listing many missing months —
    # can wrap to multiple lines without misaligning neighbouring cells.
    if stage_results:
        _section_title(pdf, f"{section_num}. 10-Stage Data Integrity Validation Pipeline")
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
        section_num += 1

    # ── Lineage (only when a validator run produced any) ─────────────────
    if lineage:
        _section_title(pdf, f"{section_num}. Lineage & Provenance")
        _kv_table(pdf, [
            ("Source File", lineage.get("source_file", "N/A")),
            ("Last Modified", lineage.get("last_modified", "N/A")),
            ("Row Count", str(lineage.get("row_count", "N/A"))),
        ])
        section_num += 1

    if outliers:
        _section_title(pdf, f"{section_num}. Outlier Extraction")
        pdf.set_font("Helvetica", "", 9)
        for o in outliers:
            pdf.cell(0, 6, _safe(f"  - {o.get('Date', '?')}: CPI_Value = {o.get('CPI_Value', '?')}"),
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        section_num += 1

    # ── Full escalation history (only the Archived / Closed Contracts browse
    # view passes this -- the COMPLETE history, every year, not just the
    # latest, so the PDF isn't missing anything the screen shows) ─────────
    if full_escalation_history:
        _section_title(pdf, f"{section_num}. Full Escalation History")
        for e in full_escalation_history:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(0, 0, 0)
            pdf.cell(0, 6, _safe(f"{e['new_anchor_month']} -- {e['formula_type']}"),
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(70, 70, 70)
            pdf.multi_cell(0, 5, _safe(
                f"Prior: CPI {e['prior_anchor_cpi']}, R{e['prior_adjusted_price']:,.2f}  ->  "
                f"New: CPI {e['new_anchor_cpi']}, R{e['new_adjusted_price']:,.2f}  |  "
                f"Approved by {e['approved_by']} on {e['escalated_at']}"
            ), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        section_num += 1

    # ── Correction history (whenever this tender has ever been corrected,
    # on EVERY document generated for it -- never just the final number
    # with no trace of what changed) ──────────────────────────────────────
    if correction_history:
        _section_title(pdf, f"{section_num}. Correction History")
        for c in correction_history:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(0, 0, 0)
            if c["type"] == "CORRECTION":
                cascade_tag = f" ({c['cascade_mode']})" if c.get("cascade_mode") else ""
                header = f"{c['year_month']} -- Correction{cascade_tag}"
                detail = (f"Original: R{c['original_figure']:,.2f}  ->  "
                          f"Corrected: R{c['corrected_figure']:,.2f}  |  "
                          f"Reason: {c['reason']}  |  "
                          f"Approved by: {c['corrected_by']} on {c['corrected_at']}")
            elif c["type"] == "METADATA_CORRECTION":
                flag_tag = " [FLAGGED FOR REVIEW]" if c.get("retroactive_impact_flag") else ""
                header = f"Metadata -- {c['field']}{flag_tag}"
                detail = (f"Original: {_format_metadata_value(c['field'], c['original_value'])}  ->  "
                          f"Corrected: {_format_metadata_value(c['field'], c['corrected_value'])}  |  "
                          f"Reason: {c['reason']}  |  "
                          f"Approved by: {c['corrected_by']} on {c['corrected_at']}")
                if c.get("retroactive_impact_note"):
                    detail += f"  |  {c['retroactive_impact_note']}"
            else:  # STALE_INPUT_FLAG
                header = f"{c['year_month']} -- Stale Input Flag"
                detail = c["note"]
            pdf.cell(0, 6, _safe(header), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(70, 70, 70)
            pdf.multi_cell(0, 5, _safe(detail), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(1)
        section_num += 1

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

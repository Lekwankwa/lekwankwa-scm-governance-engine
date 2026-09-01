"""
scm_parser.py -- Municipal SCM Audit Chatbot: PDF invoice parser + fallback
verification engine ("PDF Parser & ENGINE MOCK").

Extracts contractor invoice fields from a PDF using pypdf, then verifies the
contractor's claimed price escalation against the Municipal Finance
Management Act (MFMA) "Lagged Index Cutoff Rule": an invoice submitted in
calendar month M must be verified against the published Stats SA index for
month M - 1, never against the current (unpublished) month M. See
.claude/rules/scm-audit.md section 3A/3B for the full rule text this mirrors.

This is a deliberately STANDALONE, lightweight mock engine for the chatbot
feature -- it is NOT wired into core/validator.py's 10-stage pipeline or
core/tender_registry.py's multi-year anchored-tender workflow. Those manage
many years of escalations for a registered tender; this module sanity-checks
ONE ad-hoc invoice against ONE baseline date, on demand, with no persistence.
It reuses the real CPI archive (data/stats_sa_cpi_archive.csv) as its CPI
data source, and a synthetic placeholder PPI archive
(data/mock_ppi_archive.csv -- see that file's own header comment for the
disclaimer) since no real Stats SA PPI feed exists anywhere in this repo yet.

Category scope: a deliberately narrow, curated set (the Lekwankwa
Corporation Municipal Procurement Category Mapping -- see
CATEGORY_INDEX_MAP / PENDING_CATEGORIES / EXCLUDED_CATEGORIES below), NOT
every tender category that might loosely fit CPI or PPI. All 14 Tier 1
categories share the one real CPI series this repo has (Stats SA CPI60001,
"Total country" headline) -- there is no per-category CPI sub-index here.
Flagged rather than silently glossed over -- swap in real sub-indices if/
when they're ingested.

Usage as a script:
    python scm_parser.py path/to/invoice.pdf
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

CPI_ARCHIVE_PATH = Path("data") / "stats_sa_cpi_archive.csv"
PPI_ARCHIVE_PATH = Path("data") / "mock_ppi_archive.csv"

# ─────────────────────────────────────────────────────────────────────────
# Category scope -- Lekwankwa Corporation Municipal Procurement Category
# Mapping (source: tenderbulletins.co.za/list-tender-categories, filtered
# to categories relevant to CPI/PPI-indexed price escalation verification;
# document supplied directly by the user -- Lekwankwa_Category_Mapping.pdf).
# This system's scope is deliberately narrow, not "every tender category
# that might loosely fit CPI or PPI." Per that document's own footer: "goods
# manufacturing, equipment supply, IT hardware, mining, and similar... do
# not map cleanly to either CPI or PPI service-escalation logic and are
# out of scope for this system."
# ─────────────────────────────────────────────────────────────────────────

_CPI_HEADLINE = {"index_type": "CPI", "column": "CPI_Value", "label": "CPI - Headline Index"}

# TIER 1 -- CPI-linked, ready now. Recurring service categories where
# headline CPI is the standard escalation reference; directly supported by
# the verification engine below.
CATEGORY_INDEX_MAP = {
    "Security Services and Equipment": dict(_CPI_HEADLINE),
    "Guarding Services": dict(_CPI_HEADLINE),
    "Cleaning Services": dict(_CPI_HEADLINE),
    "Catering Services": dict(_CPI_HEADLINE),
    "Legal Services and Conveyancing": dict(_CPI_HEADLINE),
    "Consulting Services": dict(_CPI_HEADLINE),
    "Professional Services": dict(_CPI_HEADLINE),
    "Management Consulting": dict(_CPI_HEADLINE),
    "Facilities Management, Operations and Maintenance": dict(_CPI_HEADLINE),
    "Administrative, Secretarial and support activities": dict(_CPI_HEADLINE),
    "Porter, Messenger, General Orderly, Hostess and Driver Services": dict(_CPI_HEADLINE),
    "Pest Control, Fumigation and Weed Control Services, Equipment and Supplies": dict(_CPI_HEADLINE),
    "Window Cleaning Services": dict(_CPI_HEADLINE),
    "Dry Cleaning and Laundry Services and Equipment": dict(_CPI_HEADLINE),

    # TIER 2 -- PPI-linked candidate, CONFIRMED. Stats SA PPI (P0142.1)
    # contains a clean, extractable Diesel line item -- this repo has no
    # real P0142.1 ingestion pipeline yet (see data/mock_ppi_archive.csv's
    # own disclaimer), so this still uses that file's mock petroleum
    # column underneath, relabeled to reflect the Diesel-specific framing
    # this category was actually confirmed against rather than the old
    # generic "Petroleum, chemical, rubber, and plastic products" framing.
    "Fuel and Petroleum Products": {
        "index_type": "PPI", "column": "PPI_Petroleum_Chemical_Rubber_Plastic",
        "label": "PPI - Diesel (Stats SA P0142.1)",
    },
}

# TIER 2 -- PPI-linked candidate, PENDING. Recognized as a valid category,
# but automated verification is deliberately NOT enabled for it yet -- per
# the source document: "Requires the same file-structure confirmation
# Diesel already received, before use." verify_invoice() returns a distinct
# CATEGORY_PENDING status for these (see below) instead of either computing
# a false-confidence result against the mock archive, or reporting
# UNMAPPED_CATEGORY as if the category weren't recognized at all.
PENDING_CATEGORIES = {
    "Food and Beverage Supplies": (
        "PENDING: Automated PPI verification for 'Food and Beverage Supplies' is not yet "
        "enabled. It requires the same Stats SA PPI (P0142.1) file-structure confirmation "
        "the Diesel line item under 'Fuel and Petroleum Products' already received, before "
        "this category can be built. Flagged for manual review until that confirmation is complete."
    ),
}

# EXCLUDED -- construction-related, per scope. These use JBCC (Haylett
# Formula), GCC, FIDIC, or NEC escalation methodology -- weighted
# multi-index formulas requiring SEIFSA data and quantity-surveying domain
# expertise, not the single-index CPI/PPI model this system verifies. The
# source document's own list is explicitly "representative examples...not
# exhaustive," so this keeps a fuller set of real construction-related
# tenderbulletins.co.za category names, plus the 3 categories the source
# document places under this exclusion rather than treating as ordinary
# advisory/professional services (Quantity Surveying, Architectural and
# Engineering Services, Project and Construction Management).
EXCLUDED_CATEGORIES = {
    "Civil Construction (JBCC/CPAP)": (
        "MANUAL REVIEW REQUIRED: Infrastructure construction tenders use JBCC (Haylett "
        "Formula), GCC, FIDIC, or NEC escalation methodology -- weighted multi-index formulas "
        "requiring SEIFSA data and quantity-surveying domain expertise, not the single-index "
        "CPI/PPI model this system verifies. Flagged for manual municipal engineering panel review."
    ),
}
for _name in (
    "Construction and Building Services", "Construction and Building Supplies",
    "Construction of buildings", "Civil Engineering", "Civil Works Services",
    "Civil, Road, Sewer, Plumbing and Engineering Supplies",
    "Coal Power Plant Construction, Maintenance and Operation",
    "Construction and Maintenance of Bridges", "Construction and Maintenance of Dams and Reservoirs",
    "Specialised construction activities", "Road Construction, Repairs and Maintenance",
    "Road Marking and Field Marking Services and Supplies", "Structural Engineering",
    "Building Upgrades, Refurbishments and Maintenance",
    "Built Environment and Infrastructure Delivery Management",
    "Demolition and Blasting Services and Supplies", "Flood Control and Stormwater Infrastructure",
    "Housing Development and Human Settlements", "Paving Supplies, Installation and Maintenance",
    "Piling and Foundations", "Pipe Jacking, Pipe Cracking and Horizontal Directional Drilling",
    "Pipework, Reticulation and Irrigation", "Railway Track and Siding Operation and Maintenance",
    "Roof, Gutters and Downpipes Repairs and Maintenance", "Roofing and Trusses", "Scaffolding",
    "Sewerage Infrastructure", "Slope Stabilisation Services and Supplies",
    "Sports Facilities Construction, Upgrade and Maintenance", "Steel Structures",
    "Trenching, Excavation and Site Clearing", "Waterproofing", "Wet Services / Water and Sanitation",
    # Moved here from CPI per the source document -- see comment above.
    "Quantity Surveying", "Architectural and Engineering Services",
    "Project and Construction Management, Project Planning, Monitoring and Evaluation",
):
    EXCLUDED_CATEGORIES[_name] = EXCLUDED_CATEGORIES["Civil Construction (JBCC/CPAP)"]

# Real invoices rarely spell out a category's full real name. Maps common
# shorthand onto a recognized category (Tier 1, Tier 2, or an excluded
# sector) so it still resolves correctly. Lookup is case-insensitive; see
# _resolve_category() below.
CATEGORY_ALIASES = {
    "security": "Security Services and Equipment",
    "security services": "Security Services and Equipment",
    "electronic security": "Security Services and Equipment",
    "general security": "Security Services and Equipment",
    "guarding": "Guarding Services",
    "cleaning": "Cleaning Services",
    "catering": "Catering Services",
    "legal": "Legal Services and Conveyancing",
    "legal services": "Legal Services and Conveyancing",
    "conveyancing": "Legal Services and Conveyancing",
    "consulting": "Consulting Services",
    "advisory services": "Professional Services",
    "professional advisory services": "Professional Services",
    "facilities management": "Facilities Management, Operations and Maintenance",
    "admin support": "Administrative, Secretarial and support activities",
    "administrative support": "Administrative, Secretarial and support activities",
    "secretarial services": "Administrative, Secretarial and support activities",
    "driver services": "Porter, Messenger, General Orderly, Hostess and Driver Services",
    "messenger services": "Porter, Messenger, General Orderly, Hostess and Driver Services",
    "pest control": "Pest Control, Fumigation and Weed Control Services, Equipment and Supplies",
    "fumigation": "Pest Control, Fumigation and Weed Control Services, Equipment and Supplies",
    "window cleaning": "Window Cleaning Services",
    "dry cleaning": "Dry Cleaning and Laundry Services and Equipment",
    "laundry": "Dry Cleaning and Laundry Services and Equipment",
    "laundry services": "Dry Cleaning and Laundry Services and Equipment",

    "fuel": "Fuel and Petroleum Products",
    "petroleum": "Fuel and Petroleum Products",
    "diesel": "Fuel and Petroleum Products",
    "fuel and chemicals": "Fuel and Petroleum Products",
    "wholesale fuel": "Fuel and Petroleum Products",
    "petroleum products": "Fuel and Petroleum Products",

    "food supply": "Food and Beverage Supplies",
    "food and beverage": "Food and Beverage Supplies",
    "food and beverage service activities": "Food and Beverage Supplies",

    "construction": "Civil Construction (JBCC/CPAP)",
    "civil construction": "Civil Construction (JBCC/CPAP)",
    "quantity surveying": "Quantity Surveying",
    "engineering consulting": "Architectural and Engineering Services",
    "project and construction management": "Project and Construction Management, Project Planning, Monitoring and Evaluation",
}

# Case-insensitive {lowercased name: real-cased name} lookup across every
# recognized category (Tier 1 + Tier 2 confirmed + Tier 2 pending) and every
# excluded sector -- built once at import time, after all of the above are
# fully populated.
_CATEGORY_LOOKUP_CI = {
    name.lower(): name
    for name in (*CATEGORY_INDEX_MAP.keys(), *PENDING_CATEGORIES.keys(), *EXCLUDED_CATEGORIES.keys())
}


def _resolve_category(raw_category):
    """Map a real-world invoice's category text onto a recognized category
    name -- a Tier 1 CPI category, the Tier 2 confirmed PPI category, a
    Tier 2 pending category, or an excluded sector.

    Tries a case-insensitive exact match against every recognized category
    name first (so an invoice using different capitalization than the
    stored name still resolves, keeping that category's own real name
    rather than substituting a different one), then a case-insensitive
    CATEGORY_ALIASES lookup for genuine shorthand that isn't itself a full
    category name, stripping '&'/'and' punctuation differences; falls back
    to the raw text unchanged so verify_invoice()'s UNMAPPED_CATEGORY check
    still correctly reports a genuinely unrecognized category rather than
    silently guessing.
    """
    if raw_category is None:
        return None
    stripped = raw_category.strip()
    canonical = _CATEGORY_LOOKUP_CI.get(stripped.lower())
    if canonical is not None:
        return canonical
    normalized = stripped.lower().replace("&", "and")
    return CATEGORY_ALIASES.get(normalized, raw_category)

# Label aliases searched for in the pypdf-extracted text, one line at a time,
# in "Label: value" form. samples/generate_sample_invoice.py writes each
# field using the FIRST alias in each list, so the bundled demo invoices
# round-trip reliably; the alternates give some tolerance for real-world
# invoices phrased slightly differently.
FIELD_LABELS = {
    "contract_id": ["Contract Reference Number", "Contract Reference", "Contract ID", "Contract Ref"],
    "invoice_date": ["Invoice Date", "Date of Invoice"],
    "baseline_date": ["Baseline Contract Date", "Contract Baseline Date", "Baseline Date", "Original Contract Date"],
    "category": ["Procurement Category", "Category", "SCM Category"],
    "claimed_escalation_pct": ["Claimed Escalation", "Claimed Escalation Rate", "Claimed Escalation (%)", "Escalation Claimed"],
    # Optional 6th field, layered on top of the 5 the chatbot's API payload
    # specifies (rule file section 4) -- used only to compute Rand figures.
    # The payload's 5 core fields are unaffected when this is absent.
    "original_value": ["Original Contract Value", "Base Contract Value", "Contract Base Value", "Original Value"],
}

REQUIRED_FIELDS = ("contract_id", "invoice_date", "baseline_date", "category", "claimed_escalation_pct")


# ─────────────────────────────────────────────────────────────────────────
# PDF text extraction
# ─────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_source) -> str:
    """Extract raw text from every page of a PDF invoice via pypdf.

    Accepts either a filesystem path (str/Path) or an already-open
    file-like/bytes-buffer object (e.g. what Streamlit's st.file_uploader
    returns) -- app.py's dashboard chatbot tab uses the latter so an
    uploaded invoice never has to be written to a temp file on disk.
    """
    if isinstance(pdf_source, (str, Path)):
        path = Path(pdf_source)
        if not path.exists():
            raise FileNotFoundError(f"Invoice PDF not found: {path}")
        source = str(path)
        label = str(path)
    else:
        source = pdf_source  # file-like object, handed to PdfReader as-is
        label = getattr(pdf_source, "name", "<uploaded file>")

    try:
        reader = PdfReader(source)
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises several distinct error types
        raise RuntimeError(f"Could not read PDF ({label}): {exc}") from exc
    return "\n".join(pages)


# ─────────────────────────────────────────────────────────────────────────
# Field parsing
# ─────────────────────────────────────────────────────────────────────────

# Multi-word labels this parser recognizes, longest first so a more specific
# label ("Claimed Escalation Rate") is healed before a shorter label it
# contains ("Claimed Escalation") could partially match instead.
_MULTIWORD_LABELS = sorted(
    {lbl for labels in FIELD_LABELS.values() for lbl in labels if " " in lbl},
    key=len, reverse=True,
)


def _heal_wrapped_labels(text: str) -> str:
    """PDF table cells can wrap a multi-word label across two lines -- e.g. a
    narrow 'Claimed Escalation:' cell renders (via pypdf) as 'Claimed' on one
    line and 'Escalation:' on the next. _extract_field()'s regex requires a
    label's words to be contiguous, so before extracting anything, collapse
    any run of whitespace -- including a line break -- between a KNOWN
    label's own words back into a single space. This only touches whitespace
    that sits exactly between a recognized label's words; nothing else in
    the document is altered.
    """
    for label in _MULTIWORD_LABELS:
        words = [re.escape(w) for w in label.split()]
        pattern = r"\s+".join(words)
        text = re.sub(pattern, " ".join(label.split()), text, flags=re.IGNORECASE)
    return text


def _extract_field(text: str, labels: list) -> Optional[str]:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*:\s*(.+)", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _parse_date(raw: Optional[str]):
    """Returns (parsed 'YYYY-MM-DD' str or None, warning str or None)."""
    if raw is None:
        return None, None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").strftime("%Y-%m-%d"), None
    except ValueError:
        return None, f"found a date-like value ({raw!r}) that could not be parsed as YYYY-MM-DD"


def _parse_percent(raw: Optional[str]):
    if raw is None:
        return None, None
    cleaned = raw.replace("%", "").strip()
    try:
        return float(cleaned), None
    except ValueError:
        return None, f"could not parse a percentage from {raw!r}"


def _parse_currency(raw: Optional[str]):
    if raw is None:
        return None, None
    cleaned = raw.replace("R", "").replace(",", "").strip()
    try:
        return float(cleaned), None
    except ValueError:
        return None, f"could not parse a Rand amount from {raw!r}"


def parse_invoice_fields(text: str) -> dict:
    """Pull the 5 required fields (+ optional original_value) out of
    pypdf-extracted invoice text. Never raises -- a missing or malformed
    field becomes None plus an entry in parse_warnings, so verify_invoice()
    can report a clean PARSE_ERROR instead of the caller hitting an exception.
    """
    text = _heal_wrapped_labels(text)
    warnings = []

    contract_id = _extract_field(text, FIELD_LABELS["contract_id"])
    category = _extract_field(text, FIELD_LABELS["category"])

    invoice_date, warn = _parse_date(_extract_field(text, FIELD_LABELS["invoice_date"]))
    if warn:
        warnings.append(f"invoice_date: {warn}")

    baseline_date, warn = _parse_date(_extract_field(text, FIELD_LABELS["baseline_date"]))
    if warn:
        warnings.append(f"baseline_date: {warn}")

    claimed_escalation_pct, warn = _parse_percent(_extract_field(text, FIELD_LABELS["claimed_escalation_pct"]))
    if warn:
        warnings.append(f"claimed_escalation_pct: {warn}")

    original_value, warn = _parse_currency(_extract_field(text, FIELD_LABELS["original_value"]))
    if warn:
        warnings.append(f"original_value: {warn}")

    return {
        "contract_id": contract_id,
        "invoice_date": invoice_date,
        "baseline_date": baseline_date,
        "category": category,
        "claimed_escalation_pct": claimed_escalation_pct,
        "original_value": original_value,
        "parse_warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────────
# Stats SA index lookups
# ─────────────────────────────────────────────────────────────────────────

def _load_index_archive(path: Path) -> list:
    """Read a Date,<value columns...> CSV, skipping any '#'-prefixed
    disclaimer/comment lines (used by mock_ppi_archive.csv)."""
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        rows = [line for line in f if not line.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


def _lagged_period(invoice_date: str) -> str:
    """Rule file section 3A -- the Lagged Index Cutoff Rule: an invoice
    dated in calendar month M is always evaluated against month M - 1.
    There is deliberately no code path anywhere in this module that looks up
    month M itself -- the 'never accept a current-month figure' prohibition
    is enforced structurally, not by detecting a violation after the fact.
    """
    dt = datetime.strptime(invoice_date, "%Y-%m-%d")
    year, month = dt.year, dt.month
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1
    return f"{year:04d}-{month:02d}"


def _lookup_index(category: str, month: str) -> Optional[float]:
    """Resolve `category` to its mapped archive/column and look up `month`.
    Returns None (never raises, never silently returns 0) when the month
    falls outside the relevant archive's coverage -- e.g. a real invoice
    dated this month, before Stats SA has published the prior month's
    release yet. verify_invoice() turns that into an INSUFFICIENT_DATA
    result instead of a crash.
    """
    mapping = CATEGORY_INDEX_MAP[category]
    path = CPI_ARCHIVE_PATH if mapping["index_type"] == "CPI" else PPI_ARCHIVE_PATH
    for row in _load_index_archive(path):
        if row.get("Date") == month:
            raw = row.get(mapping["column"])
            return float(raw) if raw not in (None, "") else None
    return None


def _generate_audit_hash(contract_id, invoice_date, baseline_date, evaluated_period, claimed_escalation_pct) -> str:
    """Mirrors core/validator.py's own hashing convention exactly:
    hashlib.sha256(run_signature).hexdigest()[:16].
    """
    run_signature = f"{contract_id}|{invoice_date}|{baseline_date}|{evaluated_period}|{claimed_escalation_pct}"
    return hashlib.sha256(run_signature.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────
# Fallback verification engine
# ─────────────────────────────────────────────────────────────────────────

def verify_invoice(fields: dict) -> dict:
    """Evaluate an extracted invoice's claimed escalation against the Month
    M-1 Stats SA rule. Returns one dict carrying every value the required
    Markdown audit report needs -- ollama_bot.py's report generator is
    instructed to treat this as the source of truth rather than
    recomputing anything.
    """
    result = {
        "status": None, "hard_lock_applied": False,
        "contract_id": fields.get("contract_id"),
        "invoice_date": fields.get("invoice_date"),
        "baseline_date": fields.get("baseline_date"),
        "category": fields.get("category"),
        # Raw text as written on the invoice, kept alongside the resolved
        # canonical name below so a report can show "'Security Services'
        # matched to Security Services and Equipment" instead of silently
        # swapping in the canonical name -- see _resolve_category().
        "category_raw": fields.get("category"),
        "index_type": None, "index_series_label": None,
        "baseline_period": None, "evaluated_period": None,
        "baseline_index_value": None, "evaluated_index_value": None,
        "claimed_escalation_pct": fields.get("claimed_escalation_pct"),
        "allowable_escalation_pct": None, "escalation_rate_status": None,
        "original_value": fields.get("original_value"),
        "claimed_amount": None, "allowable_amount": None,
        "financial_impact_difference": None,
        "financial_impact_status": None, "financial_impact_note": None,
        "audit_hash": None, "message": None, "detail": "",
        "generated_at": datetime.now().isoformat(),
    }

    # Real invoices rarely spell out the rule file's exact canonical category
    # name (e.g. "Security Services" instead of "Security Services and
    # Equipment") -- resolve it before anything else checks it, and reflect
    # the resolved name in the result so the audit report shows the
    # canonical category, not whatever shorthand the invoice happened to use.
    category = _resolve_category(fields.get("category"))
    result["category"] = category

    # 1. Excluded sector -- short-circuits before field-completeness or
    #    index-lookup logic runs at all. No CPI/PPI math is ever attempted.
    if category in EXCLUDED_CATEGORIES:
        result["status"] = "MANUAL_REVIEW_REQUIRED"
        result["message"] = EXCLUDED_CATEGORIES[category]
        result["detail"] = "Excluded sector -- no automated CPI/PPI verification attempted."
        return result

    # 1b. Recognized but not yet enabled (Tier 2 PENDING -- see
    #     PENDING_CATEGORIES). Also short-circuits: computing a result
    #     against the mock archive here would look like a real confirmed
    #     figure, which the source document is explicit this category isn't
    #     ready for yet.
    if category in PENDING_CATEGORIES:
        result["status"] = "CATEGORY_PENDING"
        result["message"] = PENDING_CATEGORIES[category]
        result["detail"] = "Category recognized, but automated verification is not yet enabled for it."
        return result

    # 2. Required fields present?
    missing = [f for f in REQUIRED_FIELDS if fields.get(f) is None]
    if missing:
        result["status"] = "PARSE_ERROR"
        result["detail"] = f"Could not extract required field(s) from the invoice: {', '.join(missing)}."
        return result

    # 3. Known category?
    if category not in CATEGORY_INDEX_MAP:
        result["status"] = "UNMAPPED_CATEGORY"
        result["detail"] = (
            f"'{category}' does not match any Approved Municipal Procurement Category "
            "Mapping (rule file section 3B) and is not a recognised excluded sector."
        )
        return result

    mapping = CATEGORY_INDEX_MAP[category]
    result["index_type"] = mapping["index_type"]
    result["index_series_label"] = mapping["label"]

    invoice_date = fields["invoice_date"]
    baseline_date = fields["baseline_date"]
    evaluated_period = _lagged_period(invoice_date)
    baseline_period = baseline_date[:7]
    result["evaluated_period"] = evaluated_period
    result["baseline_period"] = baseline_period

    evaluated_value = _lookup_index(category, evaluated_period)
    baseline_value = _lookup_index(category, baseline_period)
    result["evaluated_index_value"] = evaluated_value
    result["baseline_index_value"] = baseline_value

    # 4. Archive coverage gap (e.g. this month's release not yet published).
    if evaluated_value is None or baseline_value is None:
        missing_periods = [p for p, v in ((baseline_period, baseline_value), (evaluated_period, evaluated_value)) if v is None]
        result["status"] = "INSUFFICIENT_DATA"
        result["detail"] = (
            f"No {mapping['index_type']} archive entry for: {', '.join(missing_periods)}. "
            "Stats SA may not have published this period yet, or the local archive needs updating."
        )
        return result

    # 5. Core escalation math.
    allowable_pct = round(((evaluated_value / baseline_value) - 1) * 100, 4)
    claimed_pct = fields["claimed_escalation_pct"]
    result["allowable_escalation_pct"] = allowable_pct

    compliant = round(claimed_pct, 2) <= round(allowable_pct, 2)
    result["escalation_rate_status"] = "COMPLIANT" if compliant else "NON-COMPLIANT"
    result["status"] = "PASSED" if compliant else "FAILED"
    result["hard_lock_applied"] = not compliant

    # 6. Rand math -- only possible when original_value was extracted.
    original_value = fields.get("original_value")
    if original_value:
        claimed_amount = round(original_value * claimed_pct / 100, 2)
        allowable_amount = round(original_value * allowable_pct / 100, 2)
        result["claimed_amount"] = claimed_amount
        result["allowable_amount"] = allowable_amount
        result["financial_impact_difference"] = round(claimed_amount - allowable_amount, 2)
        result["financial_impact_status"] = "COMPLIANT" if compliant else "NON-COMPLIANT"
    else:
        result["financial_impact_status"] = "N/A - NO BASE VALUE PROVIDED"
        result["financial_impact_note"] = (
            "No 'Original Contract Value' field was found on the invoice -- Rand-amount "
            "figures cannot be computed from a percentage alone. Escalation-rate compliance "
            "above is unaffected."
        )

    result["audit_hash"] = _generate_audit_hash(
        fields["contract_id"], invoice_date, baseline_date, evaluated_period, claimed_pct
    )
    status_word = "within" if compliant else "exceeds"
    result["detail"] = (
        f"Claimed escalation {claimed_pct}% {status_word} the {allowable_pct}% allowable under "
        f"{mapping['label']} for {evaluated_period} vs. baseline {baseline_period}."
    )
    return result


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scm_parser.py <path-to-invoice.pdf>")
        raise SystemExit(1)

    extracted_text = extract_text_from_pdf(sys.argv[1])
    parsed_fields = parse_invoice_fields(extracted_text)
    outcome = verify_invoice(parsed_fields)
    print(json.dumps(outcome, indent=2, default=str))

"""
tender_registry.py — local JSON persistence for anchored tenders.

Product 1 (Inception Gateway) anchors a tender exactly once: it locks the
anchor month and anchor CPI value and writes them here. Product 2/3 (the
recurring monthly/annual checks in app.py's "Open Existing Tender" mode)
read the locked anchor back out of this registry and never re-prompt for
tender metadata.

Storage: a single JSON object keyed by tender_id (the schema's declared
primary key), at data/tenders.json. Keying by tender_id both gives O(1)
lookup and makes duplicate-ID collisions a normal dict overwrite that
app.py can guard against explicitly (see tender_exists()).

Each record has two distinct groups of fields, per the CPA (Contract Price
Adjustment) formula requirement that every year's escalation must be
traceable back to the original tender submission:

  PERMANENT (set once at anchor time in app.py, never written again):
    original_anchor_month, original_anchor_cpi, original_base_value

  ROLLING (start equal to the originals; updated only by record_escalation()
  below, only after explicit clerk/approver confirmation in app.py):
    current_anchor_month, current_anchor_cpi, current_adjusted_price

  cpa_formula_type (also set once at anchor time, one of
  "CUMULATIVE_FROM_ORIGINAL" | "COMPOUND_FROM_PRIOR_YEAR") decides which of
  the two groups app.py uses as the base for each year's escalation
  calculation -- see app.py's Calculate Annual Escalation branch.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REGISTRY_PATH = Path("data") / "tenders.json"


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    """Return the full {tender_id: record} registry, or {} if none exists yet."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_tender(record: dict, path: Path = REGISTRY_PATH) -> None:
    """Upsert one tender record into the registry, keyed by record['tender_id']."""
    registry = load_registry(path)
    registry[record["tender_id"]] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)


def get_tender(tender_id: str, path: Path = REGISTRY_PATH) -> dict | None:
    return load_registry(path).get(tender_id)


def list_tenders(path: Path = REGISTRY_PATH) -> list:
    """All tender records, sorted by tender_id, for populating the dropdown."""
    return sorted(load_registry(path).values(), key=lambda t: t.get("tender_id", ""))


def tender_exists(tender_id: str, path: Path = REGISTRY_PATH) -> bool:
    return tender_id in load_registry(path)


def record_escalation(tender_id: str, new_anchor_month: str, new_anchor_cpi_value: float,
                       new_adjusted_price: float, approved_by: str, path: Path = REGISTRY_PATH) -> dict:
    """Roll a tender's CURRENT (not original) anchor forward after an
    APPROVED Annual Escalation.

    This is the apply step of a two-step workflow: app.py calculates a
    proposed new adjusted price first and shows it as a "Pending Annual
    Escalation" (nothing here yet -- the tender is untouched), and only
    calls this function once a clerk/approver has explicitly confirmed it.
    approved_by is required (not optional) because an escalation with no
    recorded approver defeats the point of gating it.

    original_anchor_month / original_anchor_cpi / original_base_value are
    NEVER touched here -- they stay fixed for the life of the contract as
    the permanent reference point every year's calculation must trace back
    to (see module docstring). Only current_anchor_month /
    current_anchor_cpi / current_adjusted_price roll forward, alongside one
    new escalation_history entry recording the full derivation (formula
    type, what the prior figures were, what the new ones are, who approved
    it and when) -- nothing is silently overwritten, the full multi-year
    chain stays auditable.

    Every subsequent get_tender() call sees the new current_* figures from
    this point on -- for CUMULATIVE_FROM_ORIGINAL tenders this only affects
    ongoing monthly-check previews (the next escalation still calculates
    from the untouched originals); for COMPOUND_FROM_PRIOR_YEAR tenders it
    also becomes the base the NEXT escalation compounds on top of.
    """
    if not approved_by or not approved_by.strip():
        raise ValueError("approved_by is required to apply an Annual Escalation.")

    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")

    history_entry = {
        "formula_type": record.get("cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL"),
        "prior_anchor_month": record["current_anchor_month"],
        "prior_anchor_cpi": record["current_anchor_cpi"],
        "prior_adjusted_price": record["current_adjusted_price"],
        "new_anchor_month": new_anchor_month,
        "new_anchor_cpi": new_anchor_cpi_value,
        "new_adjusted_price": new_adjusted_price,
        "approved_by": approved_by.strip(),
        "escalated_at": datetime.now().isoformat(),
    }
    record.setdefault("escalation_history", []).append(history_entry)
    record["current_anchor_month"] = new_anchor_month
    record["current_anchor_cpi"] = new_anchor_cpi_value
    record["current_adjusted_price"] = new_adjusted_price
    # original_anchor_month / original_anchor_cpi / original_base_value:
    # deliberately not touched. See module + function docstrings.

    save_tender(record, path)
    return record

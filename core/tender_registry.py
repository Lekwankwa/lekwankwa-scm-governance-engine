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
                       new_base_value: float, path: Path = REGISTRY_PATH) -> dict:
    """Roll a tender's anchor forward after a formal Annual Escalation.

    Appends the OLD anchor/base as one entry in the tender's
    escalation_history (nothing is silently overwritten -- the full
    multi-year chain stays auditable), then updates the top-level
    anchor_month / anchor_cpi_value / base_value to the new figures.

    Every subsequent get_tender() call -- including the ones
    SCMDataValidator.run_monthly_check() is fed from in app.py -- sees the
    new floor automatically from this point on, mirroring how each year's
    adjustment becomes the new floor for the next year in a real multi-year
    contract.
    """
    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")

    history_entry = {
        "from_month": record["anchor_month"],
        "from_cpi": record["anchor_cpi_value"],
        "from_base_value": record["base_value"],
        "to_month": new_anchor_month,
        "to_cpi": new_anchor_cpi_value,
        "to_base_value": new_base_value,
        "escalated_at": datetime.now().isoformat(),
    }
    record.setdefault("escalation_history", []).append(history_entry)
    record["anchor_month"] = new_anchor_month
    record["anchor_cpi_value"] = new_anchor_cpi_value
    record["base_value"] = new_base_value

    save_tender(record, path)
    return record

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


def get_effective_escalation_price(entry: dict) -> float:
    """The price CURRENTLY in effect for one escalation_history entry,
    accounting for any corrections layered on top of it (the most recent
    correction's corrected_figure), or the originally approved
    new_adjusted_price if this entry has never been corrected.

    Never reads/writes entry["new_adjusted_price"] itself -- that field is
    permanent once approved (see apply_correction()'s docstring).
    """
    corrections = entry.get("corrections", [])
    if corrections:
        return corrections[-1]["corrected_figure"]
    return entry["new_adjusted_price"]


def apply_correction(tender_id: str, year_month: str, corrected_figure: float, reason: str,
                      corrected_by: str = None, cascade_mode: str = "ISOLATED",
                      path: Path = REGISTRY_PATH, dry_run: bool = False) -> dict:
    """Layer a correction onto an already-APPROVED annual escalation record.

    An approved figure is NEVER edited in place -- this always APPENDS a
    correction entry to that year's escalation_history item (which keeps
    its original new_adjusted_price forever), never overwrites it. Every
    correction records: which year, the figure it's replacing, the new
    figure, the free-text reason, who approved the correction, and when.

    reason is always required. corrected_by is required unless dry_run=True
    (app.py runs a dry_run pass first to preview a proposed correction --
    including any cascade impact -- before an approver has been named; the
    real, saved call always supplies one).

    cascade_mode only matters for COMPOUND_FROM_PRIOR_YEAR tenders that have
    already-approved years AFTER the one being corrected (for
    CUMULATIVE_FROM_ORIGINAL, or when correcting the latest/only year,
    nothing downstream was ever calculated from this entry's price, so the
    choice is moot and this parameter is ignored):

      ISOLATED (default)  -- only this entry gets the correction. Every
                              later entry is left completely untouched (its
                              own new_adjusted_price / corrections list is
                              not modified) but gets a non-destructive
                              stale_input_flags note appended, recording
                              that it was calculated from a figure that was
                              later corrected -- so nothing is silently lost,
                              but nothing recalculates either. This is the
                              default because cascading is a deliberate,
                              explicit choice, never automatic.
      CASCADE_FORWARD      -- every later entry ALSO gets its own
                              auto-generated correction layered on (same
                              non-destructive rule -- their original
                              new_adjusted_price is still never touched),
                              recomputed by re-applying each step's ORIGINAL
                              recorded CPI drift ratio to the corrected
                              price chain. No CPI archive re-query is
                              needed: a price correction never changes what
                              CPI reading a given month actually had, so the
                              ratio between two already-approved anchor
                              months' CPI values is a fixed fact.

    Always rolls current_anchor_month / current_anchor_cpi /
    current_adjusted_price to reflect the EFFECTIVE (post-correction) state
    of whatever is now the LATEST escalation_history entry, so ongoing
    monthly checks and the next escalation both pick it up automatically.

    Returns the (possibly unsaved, if dry_run) tender record for preview.
    """
    if not reason or not reason.strip():
        raise ValueError("A reason is required to apply a correction.")
    if not dry_run and (not corrected_by or not corrected_by.strip()):
        raise ValueError("corrected_by is required to apply a correction.")

    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")

    history = record.get("escalation_history", [])
    idx = next((i for i, e in enumerate(history) if e["new_anchor_month"] == year_month), None)
    if idx is None:
        raise ValueError(f"No approved annual escalation found for {year_month} on tender '{tender_id}'.")

    corrected_at = datetime.now().isoformat()
    corrected_by_value = (corrected_by or "").strip()
    reason_value = reason.strip()
    entry = history[idx]
    original_figure = get_effective_escalation_price(entry)
    is_compound = record.get("cpa_formula_type") == "COMPOUND_FROM_PRIOR_YEAR"

    entry.setdefault("corrections", []).append({
        "corrected_at": corrected_at,
        "corrected_by": corrected_by_value,
        "reason": reason_value,
        "original_figure": original_figure,
        "corrected_figure": corrected_figure,
        "cascade_mode": cascade_mode if is_compound else None,
    })

    later_entries = history[idx + 1:]
    if later_entries and is_compound and cascade_mode == "CASCADE_FORWARD":
        running_price = corrected_figure
        for later in later_entries:
            prior_effective_before = get_effective_escalation_price(later)
            # This step's CPI drift is a fixed historical fact -- only the
            # price it gets applied to changes.
            drift_pct = (later["new_anchor_cpi"] / later["prior_anchor_cpi"] - 1) * 100
            recalculated_price = running_price * (1 + drift_pct / 100)
            later.setdefault("corrections", []).append({
                "corrected_at": corrected_at,
                "corrected_by": corrected_by_value,
                "reason": f"Cascaded from the correction to {year_month} (reason: {reason_value}).",
                "original_figure": prior_effective_before,
                "corrected_figure": recalculated_price,
                "cascade_mode": "CASCADE_FORWARD",
                "cascaded_from": year_month,
            })
            running_price = recalculated_price
    elif later_entries and is_compound:
        # ISOLATED (default): later entries' own figures are left exactly
        # as originally approved -- only a non-destructive flag is added.
        for later in later_entries:
            later.setdefault("stale_input_flags", []).append({
                "flagged_at": corrected_at,
                "corrected_year_month": year_month,
                "note": (f"This year's calculation used {year_month}'s adjusted price of "
                         f"R{original_figure:,.2f}, which was later corrected on {corrected_at} "
                         f"to R{corrected_figure:,.2f} (reason: {reason_value}). This record was "
                         "NOT recalculated -- the correction was applied as ISOLATED."),
            })

    latest = history[-1]
    record["current_anchor_month"] = latest["new_anchor_month"]
    record["current_anchor_cpi"] = latest["new_anchor_cpi"]
    record["current_adjusted_price"] = get_effective_escalation_price(latest)

    if not dry_run:
        save_tender(record, path)
    return record


def get_correction_history(tender: dict) -> list:
    """Flattened, chronological list of every correction AND every
    stale-input flag across all of a tender's escalation_history entries --
    for rendering a full 'what changed, when, why, and who approved it'
    section in every document generated for a tender that has any (see
    app.py's render_result_block / utils/document_gen.py's build_audit_pdf).
    """
    out = []
    for entry in tender.get("escalation_history", []):
        for c in entry.get("corrections", []):
            out.append({"type": "CORRECTION", "year_month": entry["new_anchor_month"], **c})
        for f in entry.get("stale_input_flags", []):
            out.append({"type": "STALE_INPUT_FLAG", "year_month": entry["new_anchor_month"], **f})
    out.sort(key=lambda c: c.get("corrected_at") or c.get("flagged_at"))
    return out

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

Two later additions extend this same "never silently overwrite, always
layer on top" philosophy to two more concerns:

  STATUS (archive_tender() below): a tender is never hard-deleted. Archiving
  only sets status="ARCHIVED" + an archive_info record (reason, who, when) --
  every other field, including the full escalation/correction history, stays
  exactly as it was and remains retrievable forever. One-directional by
  design (no "reactivate" path).

  METADATA CORRECTIONS (correct_tender_metadata() below): tender_name,
  start_date, end_date, original_base_value, and cpa_formula_type are
  editable directly ONLY while a tender has no check/escalation/correction
  history yet (see has_tender_activity()). Once anything has been built on
  a tender, these fields follow the exact same append-only correction
  pattern as CPI figures -- the raw field is never overwritten, a dated
  correction record is appended instead, and get_effective_tender_field() /
  resolve_effective_tender() are what every calculation and display should
  read going forward instead of the raw field.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

REGISTRY_PATH = Path("data") / "tenders.json"

# Single allowlist source for both correct_tender_metadata()'s validation and
# app.py's field-picker UI, so the two never drift out of sync.
METADATA_CORRECTABLE_FIELDS = (
    "tender_name", "start_date", "end_date", "original_base_value", "cpa_formula_type",
)


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
        "formula_type": get_effective_tender_field(record, "cpa_formula_type", "CUMULATIVE_FROM_ORIGINAL"),
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
    is_compound = get_effective_tender_field(record, "cpa_formula_type") == "COMPOUND_FROM_PRIOR_YEAR"

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
    """Flattened, chronological list of every CPI correction, every
    stale-input flag, and every metadata correction across a tender's full
    history -- ONE unified trail for rendering a full 'what changed, when,
    why, and who approved it' section in every document generated for a
    tender that has any (see app.py's render_result_block /
    utils/document_gen.py's build_audit_pdf) -- an auditor reading one
    document should see everything that ever changed about a tender in one
    place, not have to cross-reference a separate metadata-corrections
    section.
    """
    out = []
    for entry in tender.get("escalation_history", []):
        for c in entry.get("corrections", []):
            out.append({"type": "CORRECTION", "year_month": entry["new_anchor_month"], **c})
        for f in entry.get("stale_input_flags", []):
            out.append({"type": "STALE_INPUT_FLAG", "year_month": entry["new_anchor_month"], **f})
    for m in tender.get("metadata_corrections", []):
        out.append({"type": "METADATA_CORRECTION", **m})
    out.sort(key=lambda c: c.get("corrected_at") or c.get("flagged_at"))
    return out


def archive_tender(tender_id: str, reason: str, archived_by: str, path: Path = REGISTRY_PATH) -> dict:
    """Retire a tender from the active working list WITHOUT deleting or
    altering anything about it. Sets status="ARCHIVED" plus a single
    archive_info record (reason, who, when) -- every other field, including
    the full escalation_history / corrections / check_history, is left
    completely untouched and stays retrievable forever (see app.py's
    'Archived / Closed Contracts' mode).

    One-directional by design: there is no "reactivate" function anywhere in
    this module, and archiving an already-archived tender raises rather than
    silently no-op'ing. reason and archived_by are both required, same
    required-field pattern as record_escalation()'s approved_by and
    apply_correction()'s reason -- an archive action with no recorded reason
    or who performed it defeats the audit trail.

    There is no hard-delete path anywhere in this module.
    """
    if not reason or not reason.strip():
        raise ValueError("A reason is required to archive a tender.")
    if not archived_by or not archived_by.strip():
        raise ValueError("archived_by is required to archive a tender.")

    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")
    if record.get("status") == "ARCHIVED":
        raise ValueError(f"Tender '{tender_id}' is already archived.")

    record["status"] = "ARCHIVED"
    record["archive_info"] = {
        "reason": reason.strip(),
        "archived_by": archived_by.strip(),
        "archived_at": datetime.now().isoformat(),
    }
    save_tender(record, path)
    return record


def log_check(tender_id: str, check_month: str, path: Path = REGISTRY_PATH) -> dict:
    """Append one {check_month, checked_at} entry to check_history after a
    SUCCESSFUL 'Run Monthly Check'. This is the one deliberate, user-approved
    exception to that action's otherwise non-mutating design: no CPI, price,
    or anchor figure is touched here -- only an audit-log-style record that
    the check happened, which is what lets has_tender_activity() below
    correctly gate metadata correction on "has this tender ever been relied
    on," not just "has it ever been escalated."

    Every check is logged (not deduplicated by month) -- a history of
    events, matching the literal request. Only the standalone 'Run Monthly
    Check' button calls this; the internal run_monthly_check() invocation
    inside 'Calculate Annual Escalation' does NOT, since that's already
    tracked separately via escalation_history and logging it twice would
    conflate two different audit trails.
    """
    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")
    record.setdefault("check_history", []).append({
        "check_month": check_month,
        "checked_at": datetime.now().isoformat(),
    })
    save_tender(record, path)
    return record


def has_tender_activity(tender: dict) -> bool:
    """True if ANYTHING has been built on this tender yet: a logged check, an
    approved escalation, or a prior metadata correction. This is the single
    gate deciding direct-edit vs gated-correction for tender metadata (see
    update_tender_metadata_direct() / correct_tender_metadata() below).

    The metadata_corrections condition matters even though it isn't one of
    the two things the gating requirement names explicitly: once a tender
    has ANY metadata correction, direct-edit must never become legal again
    for it, even if check/escalation history are both still empty --
    otherwise a later direct edit could silently overwrite the raw field a
    correction record's original_value/corrected_value refers to, breaking
    that correction's meaning retroactively.
    """
    return bool(tender.get("check_history") or tender.get("escalation_history")
                or tender.get("metadata_corrections"))


def get_effective_tender_field(tender: dict, field: str, default=None):
    """The value of `field` CURRENTLY in effect for this tender, accounting
    for any metadata correction layered on top of it (the most recent
    correction's corrected_value), or the raw stored value if this field has
    never been corrected. Exact same pattern as
    get_effective_escalation_price() for CPI figures.

    Never reads/writes tender[field] itself -- that raw value is permanent
    once set (see correct_tender_metadata()'s docstring) and stays visible,
    unchanged, in the record for audit purposes.
    """
    corrections = [c for c in tender.get("metadata_corrections", []) if c["field"] == field]
    if corrections:
        return corrections[-1]["corrected_value"]
    return tender.get(field, default)


def resolve_effective_tender(tender: dict) -> dict:
    """A shallow copy of `tender` with all 5 METADATA_CORRECTABLE_FIELDS
    overlaid with their EFFECTIVE (post-correction) values. Every other key
    -- escalation_history, check_history, metadata_corrections, archive_info,
    original_anchor_month/cpi, current_anchor_month/cpi/adjusted_price --
    passes through untouched. Nothing is written back to storage; this is a
    read-time view only.

    This is the primary mechanism for keeping a corrected value live
    everywhere it matters, INSTEAD of threading get_effective_tender_field()
    through every individual read site one at a time -- that approach is
    exactly the kind of fragility that produced a real bug earlier in this
    app (see _formula_reference_point() in app.py): easy to miss a call
    site, hard to notice you missed it. Call this once, right after
    get_tender()/list_tenders(), and read every display/calculation field
    off the result instead of the raw record.
    """
    effective = dict(tender)
    for field in METADATA_CORRECTABLE_FIELDS:
        effective[field] = get_effective_tender_field(tender, field, tender.get(field))
    return effective


def update_tender_metadata_direct(tender_id: str, tender_name: str, start_date: str, end_date: str,
                                   original_base_value: float, cpa_formula_type: str,
                                   path: Path = REGISTRY_PATH) -> dict:
    """Direct, unguarded metadata edit -- legal ONLY while a tender has zero
    activity (see has_tender_activity()). Re-checks that precondition itself
    (defense in depth, matching this codebase's established "re-validated
    server-side, not just gated by which buttons the UI showed" pattern --
    see _formula_reference_point()'s docstring in app.py) rather than
    trusting the caller alone.

    ALWAYS re-runs the anchor step against `start_date` (same
    process_timeline_loop(str(start_date), str(start_date), ...) call the
    original 'Anchor Tender' flow makes) to refresh
    original_anchor_month/original_anchor_cpi and
    current_anchor_month/current_anchor_cpi/current_adjusted_price --
    unconditionally, even if start_date didn't actually change. This is
    simpler and safer than conditionally re-anchoring only when start_date
    changes: it removes an entire class of "did the caller correctly detect
    the change" bugs, and re-deriving an unchanged date is a harmless
    idempotent no-op. Leaving a stale CPI anchor tied to an old start_date
    would itself be a data-integrity bug, not a neutral skip -- nothing has
    been built on this tender yet, so there's no history to protect by
    leaving it alone.

    Imported lazily (not at module level) to avoid a circular import:
    core.validator imports nothing from this module, but this keeps the
    dependency direction obvious at the call site.
    """
    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")
    if has_tender_activity(record):
        raise ValueError(
            f"Tender '{tender_id}' has check/escalation/correction history -- "
            "direct metadata edits are no longer allowed. Use the gated correction "
            "flow instead (correct_tender_metadata())."
        )

    from core.validator import SCMDataValidator  # local import: avoids a module-load-order dependency

    validator = SCMDataValidator(str(Path("data") / "stats_sa_cpi_archive.csv"))
    timeline_results, _stage_results, _extras, _output_hash = validator.process_timeline_loop(
        str(start_date), str(start_date), tender_id=tender_id, baseline_type=None
    )
    anchor_record = timeline_results[0]

    record["tender_name"] = tender_name
    record["start_date"] = str(start_date)
    record["end_date"] = str(end_date)
    record["original_base_value"] = float(original_base_value)
    record["cpa_formula_type"] = cpa_formula_type
    record["original_anchor_month"] = anchor_record["month"]
    record["original_anchor_cpi"] = anchor_record["anchor_cpi"]
    record["current_anchor_month"] = anchor_record["month"]
    record["current_anchor_cpi"] = anchor_record["anchor_cpi"]
    record["current_adjusted_price"] = float(original_base_value)

    save_tender(record, path)
    return record


def correct_tender_metadata(tender_id: str, field: str, corrected_value, reason: str,
                             corrected_by: str = None, path: Path = REGISTRY_PATH,
                             dry_run: bool = False) -> dict:
    """Layer a correction onto a tender's own metadata -- legal ONLY once a
    tender HAS activity (the mirror-image guard of
    update_tender_metadata_direct(); kept as two separate functions/code
    paths rather than one that branches, since the two cases have
    genuinely different shapes: one takes a full replacement record and
    re-anchors, the other takes one field and never re-anchors).

    Mirrors apply_correction()'s exact shape: reason is always required,
    corrected_by is required unless dry_run=True (app.py runs a dry_run pass
    first to preview -- including the retroactive-impact flag below --
    before an approver has been named). The raw record[field] is NEVER
    overwritten -- this always APPENDS to metadata_corrections, so the
    original value stays visible in the record forever, exactly as
    required.

    original_value is read via get_effective_tender_field(), not the raw
    field -- so a SECOND correction to the same field correctly shows
    "original" as the FIRST correction's result, not the raw stored value
    (same "layer on top" semantics apply_correction() already uses for CPI
    figures).

    retroactive_impact_flag is True when `field` is "original_base_value" or
    "cpa_formula_type" AND this tender already has escalation_history --
    both feed live calculations that already-approved past records
    depended on (see _formula_reference_point() / resolve_effective_tender()
    in app.py). It is never flagged for tender_name/start_date/end_date,
    which feed no calculation. Per the explicit requirement, this is
    surfaced for human review, NOT auto-recalculated -- no escalation_history
    entry is ever touched by this function.
    """
    if field not in METADATA_CORRECTABLE_FIELDS:
        raise ValueError(f"'{field}' is not a correctable metadata field. "
                          f"Must be one of: {', '.join(METADATA_CORRECTABLE_FIELDS)}.")
    if not reason or not reason.strip():
        raise ValueError("A reason is required to correct tender metadata.")
    if not dry_run and (not corrected_by or not corrected_by.strip()):
        raise ValueError("corrected_by is required to apply a metadata correction.")

    record = get_tender(tender_id, path)
    if record is None:
        raise KeyError(f"Tender '{tender_id}' not found in registry.")
    if not has_tender_activity(record):
        raise ValueError(
            f"Tender '{tender_id}' has no check/escalation/correction history yet -- "
            "use the direct-edit flow instead (update_tender_metadata_direct())."
        )

    original_value = get_effective_tender_field(record, field)
    has_escalations = bool(record.get("escalation_history"))
    retroactive_impact_flag = field in ("original_base_value", "cpa_formula_type") and has_escalations

    retroactive_impact_note = None
    if retroactive_impact_flag:
        n = len(record["escalation_history"])
        years = ", ".join(e["new_anchor_month"] for e in record["escalation_history"])
        is_compound = get_effective_tender_field(record, "cpa_formula_type") == "COMPOUND_FROM_PRIOR_YEAR"
        if field == "original_base_value" and is_compound:
            retroactive_impact_note = (
                f"This tender uses COMPOUND_FROM_PRIOR_YEAR, which never re-reads "
                f"original_base_value after the initial anchor -- every escalation "
                f"compounds off current_adjusted_price instead. This correction changes "
                f"the record and display only; it has NO effect on any past OR future "
                f"calculation. The {n} already-approved escalation(s) ({years}) are "
                "unaffected."
            )
        else:
            retroactive_impact_note = (
                f"This tender already has {n} already-approved escalation(s) ({years}) "
                f"calculated using the prior value of '{field}'. Those records are NOT "
                "being retroactively recalculated -- they remain exactly as approved. "
                "This correction applies going forward only. Flagged for manual review: "
                "if any of those already-approved calculations need to be redone, use "
                "'Correct Prior Escalation' separately for each affected year."
            )

    corrected_at = datetime.now().isoformat()
    correction_entry = {
        "field": field,
        "original_value": original_value,
        "corrected_value": corrected_value,
        "reason": reason.strip(),
        "corrected_by": (corrected_by or "").strip(),
        "corrected_at": corrected_at,
        "retroactive_impact_flag": retroactive_impact_flag,
        "retroactive_impact_note": retroactive_impact_note,
    }
    record.setdefault("metadata_corrections", []).append(correction_entry)

    if not dry_run:
        save_tender(record, path)
    return record

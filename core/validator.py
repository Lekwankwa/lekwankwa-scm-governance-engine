"""
SCMDataValidator — the 10-Stage Data Integrity Engine.

This is a scaled-down port of the 10-stage validation pipeline used across
the Lekwankwa data platform (see lek_scraper/validations/), adapted from a
Parquet vault + multi-source setup down to this MVP's single flat CSV of
Stats SA CPI history. Same 10 stage positions/order and mostly the same
names -- except stages 1a and 8, renamed below to describe what this MVP's
version actually checks rather than borrowing the real platform's names for
depth this scaled-down port doesn't have (see those methods' docstrings):

  1a  Temporal Sanity Check
  1b  Temporal Consistency
  1c  Temporal Coverage Audit
  2   Sanity Checks
  3   Schema Compliance
  4   Referential Integrity
  5   Lineage & Provenance
  6   Outlier Extraction
  7   Changelog Generation
  8   Column Signature Check

Each stage method returns a dict shaped like:
    {"stage": "1a", "name": "...", "status": "PASS" | "WARN" | "FAIL", "detail": "..."}
which mirrors the result shape used by the real pipeline_runner.run_stage().

run_10_stage_pipeline() runs all 10 in order and stops at the first FAIL on a
required stage — exactly like on_required_fail="stop" in the real orchestrators.
"""
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["Date", "CPI_Value"]
HYPERINFLATION_CEILING = 250.0          # Stage 6 anomaly ceiling
MOM_OUTLIER_PCT = 5.0                   # Stage 2 — CPI is low-volatility; flag big jumps
SOURCE_IDENTITY_SIGNATURE = "STATS_SA_CPI_HISTORICAL_ARCHIVE_V1"

CHANGELOG_PATH = Path("data") / "scm_run_changelog.jsonl"


def _stage(stage_id, name, status, detail=""):
    return {"stage": stage_id, "name": name, "status": status, "detail": detail}


def _month_range(start_lookup: str, end_lookup: str):
    """Inclusive list of 'YYYY-MM' strings between two 'YYYY-MM' bounds."""
    start = datetime.strptime(start_lookup, "%Y-%m")
    end = datetime.strptime(end_lookup, "%Y-%m")
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return months


def _compress_consecutive(missing_months: list) -> list:
    """Compress a sorted list of 'YYYY-MM' gap months into (start, end) range strings.

    Same run-compression idea as temporal_validator.group_consecutive_runs(),
    just operating on months instead of years.
    """
    if not missing_months:
        return []

    def _next_month(ym: str) -> str:
        d = datetime.strptime(ym, "%Y-%m")
        y, m = d.year, d.month + 1
        if m == 13:
            m, y = 1, y + 1
        return f"{y:04d}-{m:02d}"

    runs = []
    s = e = missing_months[0]
    for ym in missing_months[1:]:
        if ym == _next_month(e):
            e = ym
        else:
            runs.append((s, e))
            s = e = ym
    runs.append((s, e))
    return [f"{s}" if s == e else f"{s}..{e}" for s, e in runs]


class SCMDataValidator:
    def __init__(self, archive_path):
        self.archive_path = archive_path
        self.archive_df = pd.read_csv(archive_path)
        if "Date" in self.archive_df.columns:
            self.archive_df["Date"] = self.archive_df["Date"].astype(str)

    # ── Stage 1a — Temporal Sanity Check ─────────────────────────────────────
    def stage_1a_temporal_sanity(self):
        """Checks Date well-formedness, uniqueness, and non-future-dating.

        Named "Temporal Sanity Check" rather than a bitemporal/point-in-time
        term on purpose: this does NOT track as-of/knowledge dates or prove a
        locked value was later confirmed unchanged (or would catch one that
        changed) -- it can't, since this MVP only ever holds the CSV's current
        state, not a history of prior versions. Don't call this bitemporal or
        PIT validation until the system is actually capturing that history.
        """
        df = self.archive_df
        if "Date" not in df.columns:
            return _stage("1a", "Temporal Sanity Check", "FAIL",
                           "Date column missing — cannot verify temporal well-formedness.")

        bad_dates = [d for d in df["Date"] if not re.fullmatch(r"\d{4}-\d{2}", str(d))]
        if bad_dates:
            return _stage("1a", "Temporal Sanity Check", "FAIL",
                           f"{len(bad_dates)} row(s) with malformed Date (expected YYYY-MM): {bad_dates[:5]}")

        dupes = df["Date"][df["Date"].duplicated()].tolist()
        if dupes:
            return _stage("1a", "Temporal Sanity Check", "FAIL",
                           f"Duplicate Date record(s) — not unique record IDs: {dupes[:5]}")

        today_month = datetime.now().strftime("%Y-%m")
        future_rows = [d for d in df["Date"] if d > today_month]
        if future_rows:
            return _stage("1a", "Temporal Sanity Check", "WARN",
                           f"{len(future_rows)} row(s) dated after the current month — "
                           f"future-dated entries are not valid for a historical archive: {future_rows[:5]}")

        return _stage("1a", "Temporal Sanity Check", "PASS",
                       f"{len(df)} unique, well-formed, non-future-dated records.")

    # ── Stage 1b — Temporal Consistency ─────────────────────────────────────
    def stage_1b_temporal_consistency(self):
        df = self.archive_df
        dates = df["Date"].tolist()
        sorted_dates = sorted(dates)
        if dates != sorted_dates:
            return _stage("1b", "Temporal Consistency", "FAIL",
                           "Archive rows are not in strict ascending chronological order.")
        return _stage("1b", "Temporal Consistency", "PASS",
                       "Archive is strictly ascending with no duplicate/out-of-order entries.")

    # ── Stage 1c — Temporal Coverage Audit ──────────────────────────────────
    def stage_1c_temporal_coverage(self, start_lookup, end_lookup):
        present = set(self.archive_df["Date"])
        expected = _month_range(start_lookup, end_lookup)
        missing = sorted([m for m in expected if m not in present])
        if missing:
            gap_ranges = _compress_consecutive(missing)
            return _stage("1c", "Temporal Coverage Audit", "FAIL",
                           f"DATA_GAP — {len(missing)} month(s) missing from archive: {gap_ranges}")
        return _stage("1c", "Temporal Coverage Audit", "PASS",
                       f"All {len(expected)} months between {start_lookup} and {end_lookup} are present.")

    # ── Stage 2 — Sanity Checks ─────────────────────────────────────────────
    def stage_2_sanity_checks(self):
        df = self.archive_df
        issues = []

        null_or_nonpositive = df[df["CPI_Value"].isna() | (df["CPI_Value"] <= 0)]
        if not null_or_nonpositive.empty:
            issues.append(f"{len(null_or_nonpositive)} row(s) with null/non-positive CPI_Value")

        dupes = df["Date"][df["Date"].duplicated()].tolist()
        if dupes:
            issues.append(f"duplicate key(s) on Date: {dupes[:5]}")

        mom_outliers = []
        prev = None
        for _, row in df.sort_values("Date").iterrows():
            cpi = row["CPI_Value"]
            if prev is not None and prev > 0 and pd.notna(cpi):
                pct_change = abs((cpi / prev - 1) * 100)
                if pct_change > MOM_OUTLIER_PCT:
                    mom_outliers.append(row["Date"])
            if pd.notna(cpi):
                prev = cpi

        if issues:
            return _stage("2", "Sanity Checks", "FAIL", "; ".join(issues))
        if mom_outliers:
            return _stage("2", "Sanity Checks", "WARN",
                           f"Month-over-month change > {MOM_OUTLIER_PCT}% at: {mom_outliers}")
        return _stage("2", "Sanity Checks", "PASS", "No nulls, no duplicate keys, no MoM outliers.")

    # ── Stage 3 — Schema Compliance ─────────────────────────────────────────
    def stage_3_schema_compliance(self):
        missing_cols = [c for c in REQUIRED_COLUMNS if c not in self.archive_df.columns]
        if missing_cols:
            return _stage("3", "Schema Compliance", "FAIL", f"Missing required column(s): {missing_cols}")

        if not pd.api.types.is_numeric_dtype(pd.to_numeric(self.archive_df["CPI_Value"], errors="coerce")):
            return _stage("3", "Schema Compliance", "FAIL", "CPI_Value column is not numeric.")

        return _stage("3", "Schema Compliance", "PASS",
                       f"Required columns present with correct types: {REQUIRED_COLUMNS}")

    # ── Stage 4 — Referential Integrity ─────────────────────────────────────
    def stage_4_referential_integrity(self, start_lookup, end_lookup):
        anchor = self.archive_df[self.archive_df["Date"] == start_lookup]
        terminal = self.archive_df[self.archive_df["Date"] == end_lookup]
        if anchor.empty:
            return _stage("4", "Referential Integrity", "FAIL",
                           f"Start month {start_lookup} does not resolve to an archive row.")
        if terminal.empty:
            return _stage("4", "Referential Integrity", "WARN",
                           f"End month {end_lookup} does not resolve to an archive row "
                           "— timeline will stop at the last available row instead.")
        return _stage("4", "Referential Integrity", "PASS",
                       f"Anchor row ({start_lookup}) and terminal row ({end_lookup}) both resolve.")

    # ── Stage 5 — Lineage & Provenance ──────────────────────────────────────
    def stage_5_lineage_provenance(self):
        path = Path(self.archive_path)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
        except OSError:
            mtime = "unknown"
        lineage = {
            "source_file": str(path),
            "last_modified": mtime,
            "row_count": len(self.archive_df),
        }
        # Plain-text sentence, matching every other stage's detail style --
        # the structured version of this data lives in the returned `lineage`
        # dict / the JSON export's top-level "lineage" object, not here.
        detail = (f"Source file: {lineage['source_file']}; "
                  f"last modified: {lineage['last_modified']}; "
                  f"row count: {lineage['row_count']}.")
        return _stage("5", "Lineage & Provenance", "PASS", detail), lineage

    # ── Stage 6 — Outlier Extraction ────────────────────────────────────────
    def stage_6_outlier_extraction(self):
        df = self.archive_df
        outliers = df[df["CPI_Value"] > HYPERINFLATION_CEILING]
        if not outliers.empty:
            extracted = outliers[["Date", "CPI_Value"]].to_dict("records")
            return _stage("6", "Outlier Extraction", "WARN",
                           f"{len(extracted)} hyper-inflation outlier row(s) extracted "
                           f"(CPI_Value > {HYPERINFLATION_CEILING}): {extracted}"), extracted
        return _stage("6", "Outlier Extraction", "PASS", "No hyper-inflation outliers found."), []

    # ── Stage 7 — Changelog Generation ──────────────────────────────────────
    def stage_7_changelog_generation(self, tender_id, output_hash):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tender_id": tender_id,
            "output_hash": output_hash,
        }
        try:
            CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CHANGELOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            return _stage("7", "Changelog Generation", "PASS",
                           f"Audit entry appended to {CHANGELOG_PATH}")
        except OSError as exc:
            return _stage("7", "Changelog Generation", "WARN", f"Could not write changelog: {exc}")

    # ── Stage 8 — Column Signature Check ─────────────────────────────────────
    def stage_8_column_signature_check(self):
        """Confirms the archive's first two columns are exactly ["Date", "CPI_Value"].

        Named "Column Signature Check" rather than "Source Identity
        Verification" on purpose: this only compares two column headers, not
        a checksum or a live cross-check against Stats SA -- it catches an
        obviously wrong file being swapped in, not a subtly tampered one.
        """
        actual_columns = list(self.archive_df.columns)
        if actual_columns[:2] != REQUIRED_COLUMNS:
            return _stage("8", "Column Signature Check", "FAIL",
                           f"Column signature mismatch — expected {REQUIRED_COLUMNS}, got {actual_columns}. "
                           "This file may not be the genuine Stats SA CPI Historical Archive.")
        return _stage("8", "Column Signature Check", "PASS",
                       f"Column signature matches declared identity '{SOURCE_IDENTITY_SIGNATURE}'.")

    # ── Orchestrator ─────────────────────────────────────────────────────────
    def run_10_stage_pipeline(self, start_lookup, end_lookup, tender_id="N/A", output_hash="N/A"):
        """Runs all 10 stages in order; stops at the first FAIL. Returns (results, ok, extras)."""
        results = []
        extras = {}

        def record(result):
            results.append(result)
            return result["status"] != "FAIL"

        if not record(self.stage_1a_temporal_sanity()):
            return results, False, extras
        if not record(self.stage_1b_temporal_consistency()):
            return results, False, extras
        if not record(self.stage_1c_temporal_coverage(start_lookup, end_lookup)):
            return results, False, extras
        if not record(self.stage_2_sanity_checks()):
            return results, False, extras
        if not record(self.stage_3_schema_compliance()):
            return results, False, extras
        if not record(self.stage_4_referential_integrity(start_lookup, end_lookup)):
            return results, False, extras

        lineage_result, lineage = self.stage_5_lineage_provenance()
        results.append(lineage_result)
        extras["lineage"] = lineage

        outlier_result, outliers = self.stage_6_outlier_extraction()
        results.append(outlier_result)
        extras["outliers"] = outliers

        results.append(self.stage_7_changelog_generation(tender_id, output_hash))

        if not record(self.stage_8_column_signature_check()):
            return results, False, extras

        return results, True, extras

    # ── Timeline processing (drift math) ────────────────────────────────────
    def process_timeline_loop(self, start_date_str, end_date_str, tender_id="N/A", baseline_type=None):
        """Executes the monthly progression loop, gated by the 10-stage pipeline.

        baseline_type selects which rows get RETURNED (the 10-stage gate and
        the drift math always run over the full requested range regardless):
          "Total Annual Contract Allocation Value" -> Product 3, Annual
              Anniversary Engine. Only Month 1 and every 12-month anniversary
              (index 0, 12, 24, ...) are returned -- relies on Stage 1c having
              already guaranteed no gaps, so list position == months-since-start.
          anything else (including the default None, used by the __main__
              smoke test below) -> Product 2, Monthly Invoice Auditor. The
              full month-by-month list is returned, same as before this
              parameter existed.
        """
        start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        start_lookup = start_dt.strftime("%Y-%m")
        end_lookup = end_dt.strftime("%Y-%m")

        run_signature = f"{tender_id}|{start_lookup}|{end_lookup}"
        output_hash = hashlib.sha256(run_signature.encode("utf-8")).hexdigest()[:16]

        stage_results, pipeline_ok, extras = self.run_10_stage_pipeline(
            start_lookup, end_lookup, tender_id=tender_id, output_hash=output_hash
        )
        if not pipeline_ok:
            failed = next((r for r in stage_results if r["status"] == "FAIL"), None)
            raise ValueError(
                f"10-Stage pipeline halted at Stage {failed['stage']} ({failed['name']}): {failed['detail']}"
                if failed else "10-Stage pipeline failed."
            )

        anchor_row = self.archive_df[self.archive_df["Date"] == start_lookup]
        month_1_anchor = float(anchor_row["CPI_Value"].values[0])

        active_timeline = self.archive_df[
            (self.archive_df["Date"] >= start_lookup) & (self.archive_df["Date"] <= end_lookup)
        ].sort_values(by="Date")

        processed_records = []
        for _, row in active_timeline.iterrows():
            current_cpi = float(row["CPI_Value"])
            if pd.isna(current_cpi) or current_cpi <= 0:
                continue
            drift_pct = ((current_cpi / month_1_anchor) - 1) * 100
            processed_records.append({
                "month": row["Date"],
                "anchor_month": start_lookup,
                "anchor_cpi": month_1_anchor,
                "current_cpi": current_cpi,
                "drift_percentage": round(drift_pct, 4),
            })

        if baseline_type == "Total Annual Contract Allocation Value":
            # Product 3: Month 1 anchor plus every strict 12-month anniversary.
            display_records = [r for i, r in enumerate(processed_records) if i == 0 or i % 12 == 0]
        else:
            # Product 2 (default): every gated month, unfiltered.
            display_records = processed_records

        return display_records, stage_results, extras, output_hash

    # ── Recurring check against a locked (registry) anchor ──────────────────
    def run_monthly_check(self, anchor_month, anchor_cpi_value, check_month, tender_id="N/A"):
        """Product 2/3 recurring check: one specific month, checked against a
        previously LOCKED anchor (from core/tender_registry.py) rather than
        recomputing the anchor from the live archive. This is what "no
        metadata re-entry required" means -- the anchor is fixed at whatever
        it was when the tender was first anchored, even if the archive is
        later refreshed by core/harvester.py.

        Still runs the full 10-stage gate across anchor_month..check_month,
        so a data gap or integrity problem introduced since anchoring still
        blocks the check -- only the anchor VALUE itself is exempt from
        recomputation.

        Returns a single-row list in the same shape as process_timeline_loop(),
        so app.py / document_gen.py can render either one with no branching.
        """
        if check_month < anchor_month:
            raise ValueError(
                f"Check month {check_month} is before the tender's anchor month {anchor_month}."
            )

        run_signature = f"{tender_id}|{anchor_month}|{check_month}|locked-check"
        output_hash = hashlib.sha256(run_signature.encode("utf-8")).hexdigest()[:16]

        stage_results, pipeline_ok, extras = self.run_10_stage_pipeline(
            anchor_month, check_month, tender_id=tender_id, output_hash=output_hash
        )
        if not pipeline_ok:
            failed = next((r for r in stage_results if r["status"] == "FAIL"), None)
            raise ValueError(
                f"10-Stage pipeline halted at Stage {failed['stage']} ({failed['name']}): {failed['detail']}"
                if failed else "10-Stage pipeline failed."
            )

        check_row = self.archive_df[self.archive_df["Date"] == check_month]
        if check_row.empty:
            raise ValueError(f"Check month {check_month} does not resolve to an archive row.")
        current_cpi = float(check_row["CPI_Value"].values[0])

        anchor_cpi_value = float(anchor_cpi_value)
        drift_pct = ((current_cpi / anchor_cpi_value) - 1) * 100
        record = {
            "month": check_month,
            "anchor_month": anchor_month,
            "anchor_cpi": anchor_cpi_value,
            "current_cpi": current_cpi,
            "drift_percentage": round(drift_pct, 4),
        }
        return [record], stage_results, extras, output_hash


if __name__ == "__main__":
    # Lightweight smoke test against the bundled sample archive.
    validator = SCMDataValidator("data/stats_sa_cpi_archive.csv")
    records, stages, extras, out_hash = validator.process_timeline_loop(
        "2025-01-01", "2025-06-01", tender_id="SMOKE-TEST"
    )
    print(f"Output hash: {out_hash}")
    for s in stages:
        print(f"  [{s['status']:>4}] Stage {s['stage']:<3} {s['name']}")
    print(f"Processed {len(records)} monthly record(s).")

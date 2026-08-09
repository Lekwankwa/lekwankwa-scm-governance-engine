# 🏗️ Technical Blueprint: Municipal SCM Governance Engine (MVP Demo)

This technical blueprint maps out the complete, functional architecture for your 1-day Product 1 (Inception Gateway) build, while natively accommodating the timeline and database fields required to drive your Product 2 and Product 3 roadmaps.

> **Note on `core/validator.py`:** the 10-Stage Data Integrity Engine in this repo is built as a scaled-down port of the real 10-stage validation pipeline used across the Lekwankwa data platform (`lek_scraper/validations/`) — same 10 stage positions/order (1a, 1b, 1c, 2, 3, 4, 5, 6, 7, 8: Temporal Sanity Check → Temporal Consistency → Temporal Coverage Audit → Sanity Checks → Schema Compliance → Referential Integrity → Lineage & Provenance → Outlier Extraction → Changelog Generation → Column Signature Check), adapted from a Parquet vault down to this MVP's single flat Stats SA CPI archive CSV. Stages 1a and 8 are named differently from the real platform's pipeline on purpose — this MVP doesn't implement true bitemporal/point-in-time tracking or source-identity verification, so the labels describe what's actually checked instead of borrowing names for depth it doesn't have. See the "10-Stage Automated Pipeline Visualization" table below for what each stage means here.

```
                                 [ SYSTEM DATA FLOW LAYER ]

   +────────────────────────+       +────────────────────────+       +────────────────────────+
   |   1. INGESTION LAYER   | ───>  |  2. DATA VALIDATION    | ───>  |   3. SECURE VAULT      |
   | Stats SA Web Harvester |       | 10-Stage Python Engine |       | GCloud Storage / GitHub|
   +────────────────────────+       +────────────────────────+       +────────────────────────+
                                                                                  │
                                                                                  ▼
   +────────────────────────+       +────────────────────────+       +────────────────────────+
   |    6. EXPORT PAYLOAD   | <───  |  5. FRONTEND ENGINE    | <───  |    4. CONFIGURATION    |
   | Dual PDF / JSON Output |       |  Streamlit App Interface|       | Tender Sidebar Inputs  |
   +────────────────────────+       +────────────────────────+       +────────────────────────+
```

## 📂 1. Core System Directory Tree

```
Lekwankwa SCM Governance Engine/
│
├── .github/
│   └── workflows/
│       └── monthly_harvest.yml      # GitHub Actions automation pipeline (placeholder)
│
├── data/
│   ├── stats_sa_cpi_archive.csv     # 2-Year historical CSV vault (real Stats SA data, 2024-07..2026-06)
│   ├── tenders.json                 # Tender registry — generated at runtime, one entry per anchored tender
│   └── README.md                    # Provenance: exact source URL, series code, ingestion date
│
├── core/
│   ├── __init__.py
│   ├── harvester.py                 # Stats SA CPI web harvester — ingests the official time series
│   ├── tender_registry.py           # JSON persistence for anchored tenders (Product 1 -> Product 2/3 handoff)
│   └── validator.py                 # The 10-Stage Data Integrity Engine
│
├── utils/
│   ├── __init__.py
│   └── document_gen.py              # FPDF2 automated report generator — builds the real Audit-Ready PDF
│
├── app.py                           # Main Streamlit user interface entry point
├── requirements.txt                 # Application runtime dependencies
└── README.md                        # Enterprise compliance & deployment notes (this file)
```

## 🗄️ 2. Unified Database Layout (The CSV/JSON Schema)

To ensure zero code clashing across all three products, every contract row logged via the frontend is written to an internal database ledger using this exact schema layout. `core/tender_registry.py` persists the anchor-time subset of this schema (`tender_id`, `tender_name`, `base_value`, `start_date`, `end_date`, `baseline_type`, `anchor_month`, `anchor_cpi_value`) to `data/tenders.json` — the row-level fields (`current_vintage_*`, `calculated_drift_pct`, `audit_integrity_hash`) are computed fresh per check instead of stored, by `core/validator.py`'s `run_monthly_check()`.

| Data Column Header | Primitive Type | System Purpose | Validation State |
|---|---|---|---|
| `tender_id` | STRING (PK) | Unique municipal procurement identifier. | Unique constraint check. |
| `tender_name` | STRING | Public descriptive title of service contract. | Raw string validation. |
| `baseline_type` | ENUM | Contract billing layout: [Monthly, Annual]. | Structural enum match. |
| `base_value_zar` | FLOAT | Core legal financial amount of the contract. | Minimum value threshold (>0). |
| `start_date` | DATE | Operational execution date of the tender. | ISO 8601 formatting format. |
| `end_date` | DATE | Legal contract expiration date. | Must be strictly > start_date. |
| `month_1_cpi_anchor` | FLOAT | Locked Stats SA index value for the execution month. | Cryptographically fixed on day one. |
| `current_vintage_month` | STRING | The current row processing period (YYYY-MM). | Auto-increment loop trigger. |
| `current_vintage_cpi` | FLOAT | The active CPI index matching the current processing period. | Pulled from the 2-Year archive. |
| `calculated_drift_pct` | FLOAT | The percentage mathematical variance since Month 1. | Calculated via drift formula. |
| `audit_integrity_hash` | STRING | SHA-256 digital signature of the row parameters. | Prevents retrospective manual tampering. |

## ⚙️ 3. Mathematical & Algorithmic Formulation

### A. The Contract Price Adjustment (CPA) Drift Equation

The system calculates the immediate financial escalation variance using the standard National Treasury framework:

**Drift Percentage (Δ%) = ((Current Vintage CPI / Month 1 CPI Anchor) − 1) × 100**

### B. The Payout Threshold Boundary Equation

The maximum legal payout allowed for any invoice check (Product 2 & 3) is bounded strictly by:

**Approved Maximum Payout (ZAR) = Base Value ZAR × (1 + Δ% / 100)**

## ⚙️ 4. The 10-Stage Data Integrity Engine (`core/validator.py`)

| Stage | Name | What it checks against the flat CSV archive |
|---|---|---|
| 1a | Temporal Sanity Check | `Date` parses as `YYYY-MM`; no duplicate Date rows; no row dated in the future |
| 1b | Temporal Consistency | Rows strictly ascending by Date, no duplicates/out-of-order entries |
| 1c | Temporal Coverage Audit | Every month between contract start and end exists in the archive; gaps reported as DATA_GAP |
| 2 | Sanity Checks | Null/non-positive CPI values; duplicate keys; month-over-month outlier flagging |
| 3 | Schema Compliance | Required columns `{Date, CPI_Value}` present with correct types |
| 4 | Referential Integrity | Contract start/end months both resolve to real archive rows |
| 5 | Lineage & Provenance | Records archive file path, last-modified time, row count |
| 6 | Outlier Extraction | Hyper-inflation filter (`CPI_Value > 250`) — extracted rows are reported, not silently dropped |
| 7 | Changelog Generation | Appends a JSON-lines audit entry per run to `data/scm_run_changelog.jsonl` |
| 8 | Column Signature Check | Confirms the CSV's column signature matches the declared Stats SA CPI archive identity |

## 🛠️ 5. Running the Demo

```bash
pip install -r requirements.txt
python core/harvester.py        # (re-)ingest the latest 24 months of official Stats SA CPI data
python core/validator.py        # smoke-test the 10-stage pipeline against it
streamlit run app.py
```

`data/stats_sa_cpi_archive.csv` is ingested directly from Stats SA's own published time series (see `data/README.md` for the exact source URL, series code, and ingestion date) — not placeholder data. Re-run `core/harvester.py` at any time to pull the latest month once Stats SA publishes it.

## 📋 6. Tender Registry Workflow

The sidebar's "Mode" selector splits the app into the two moments a real SCM audit actually has:

- **Anchor New Tender** (Product 1, Inception Gateway) — enter tender metadata once, pick the execution/start date, and (for an annual-baseline tender) the CPA formula type — see below. Clicking **Anchor Tender** runs that month through the full 10-stage gate and writes the whole record to `data/tenders.json` via `core/tender_registry.py`. Re-anchoring an existing `tender_id` is refused — use the other mode to check it instead.
- **Open Existing Tender** (Product 2/3, recurring audits) — pick a previously anchored tender from the registry (no metadata re-entry) and a check month from the CPI archive; the check-month picker shows every month on/after the tender's current anchor for both baseline types, labeling and defaulting to true 12-month anniversaries. **Run Monthly Check** is a non-mutating preview against whatever is currently in effect, gated by the full 10-stage pipeline.

### Permanent original vs. rolling current (`core/tender_registry.py`)

Every tender record keeps two distinct groups of fields:

| Group | Fields | Behavior |
|---|---|---|
| **Permanent** | `original_anchor_month`, `original_anchor_cpi`, `original_base_value` | Set once at anchor time, **never written again** — the fixed reference point every year's escalation must trace back to |
| **Rolling** | `current_anchor_month`, `current_anchor_cpi`, `current_adjusted_price` | Start identical to the originals; only `record_escalation()` ever moves them, and only after explicit approval |

`cpa_formula_type` (set once at anchor, permanent) decides which group each year's Annual Escalation calculates from:

- **`CUMULATIVE_FROM_ORIGINAL`** — always drifts from the permanent `original_anchor_cpi` and multiplies `original_base_value`, every year, regardless of history.
- **`COMPOUND_FROM_PRIOR_YEAR`** — drifts from `current_anchor_cpi` and multiplies `current_adjusted_price`, i.e. compounds on top of whatever the last approved escalation produced.

(These two are mathematically equivalent for an unbroken chain of full-precision escalations — CPI ratios telescope — so they only diverge in practice once rounding, caps, or skipped years enter the picture. Both are implemented as asked; which one a real contract needs depends on its actual CPA clause wording.)

### Annual Escalation is approval-gated, not automatic

Reaching an anniversary month never changes anything by itself:

1. **Calculate Annual Escalation** computes the proposed new anchor/CPI/adjusted-price (per the tender's formula type) and shows it as a **"PENDING ANNUAL ESCALATION — PROPOSED, NOT YET APPLIED"** panel. Nothing is written to the registry yet.
2. A named **Approver Name / Role** is required before anything can be applied — the button is refused without one.
3. **Confirm & Apply Annual Escalation** re-validates and re-derives the figures fresh (never trusting the stashed pending numbers), then calls `record_escalation()`, which appends the full derivation — formula type, prior figures, new figures, approver, timestamp — to that tender's `escalation_history` and moves only the rolling `current_*` fields. The permanent `original_*` fields are untouched.
4. Every subsequent check reads the new `current_*` figures automatically.

Both the pending preview and the applied PDF/JSON always show the full chain: the original baseline (unchanged), the prior year's adjusted price (for a compounding tender), this year's new adjusted price, and which formula produced it — so an auditor can trace current price back to the original tender submission in one document.

All outputs — anchor, monthly check, and applied escalation — render through the same result panel and produce the same certified PDF/JSON export pair, keyed to whichever action most recently succeeded, with a `document_title` that names what actually happened ("Tender Anchor Record" / "Monthly Invoice Verification Record" / "Annual Contract Price Adjustment Record").

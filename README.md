# 🏗️ Technical Blueprint: Municipal SCM Governance Engine (MVP Demo)

This technical blueprint maps out the complete, functional architecture for your 1-day Product 1 (Inception Gateway) build, while natively accommodating the timeline and database fields required to drive your Product 2 and Product 3 roadmaps.

> **Note on `core/validator.py`:** the 10-Stage Data Integrity Engine in this repo is built as a scaled-down but faithful port of the real 10-stage validation pipeline used across the Lekwankwa data platform (`lek_scraper/validations/`) — same 10 stage names/order (1a, 1b, 1c, 2, 3, 4, 5, 6, 7, 8: Bitemporal Core → Temporal Consistency → Temporal Coverage Audit → Sanity Checks → Schema Compliance → Referential Integrity → Lineage & Provenance → Outlier Extraction → Changelog Generation → Source Identity Verification), adapted from a Parquet vault down to this MVP's single flat Stats SA CPI archive CSV. See the "10-Stage Automated Pipeline Visualization" table below for what each stage means here.

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
│   ├── stats_sa_cpi_archive.csv     # 2-Year historical Point-in-Time CSV vault (real Stats SA data, 2024-07..2026-06)
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
| 1a | Bitemporal Core (PIT Validation) | `Date` parses as `YYYY-MM`; no duplicate Date rows; no row dated in the future |
| 1b | Temporal Consistency | Rows strictly ascending by Date, no duplicates/out-of-order entries |
| 1c | Temporal Coverage Audit | Every month between contract start and end exists in the archive; gaps reported as DATA_GAP |
| 2 | Sanity Checks | Null/non-positive CPI values; duplicate keys; month-over-month outlier flagging |
| 3 | Schema Compliance | Required columns `{Date, CPI_Value}` present with correct types |
| 4 | Referential Integrity | Contract start/end months both resolve to real archive rows |
| 5 | Lineage & Provenance | Records archive file path, last-modified time, row count |
| 6 | Outlier Extraction | Hyper-inflation filter (`CPI_Value > 250`) — extracted rows are reported, not silently dropped |
| 7 | Changelog Generation | Appends a JSON-lines audit entry per run to `data/scm_run_changelog.jsonl` |
| 8 | Source Identity Verification | Confirms the CSV's column signature matches the declared Stats SA CPI archive identity |

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

- **Anchor New Tender** (Product 1, Inception Gateway) — enter tender metadata once, pick the execution/start date. Clicking **Anchor Tender** runs that single month through the full 10-stage gate, locks its CPI as the tender's permanent anchor, and writes the whole record to `data/tenders.json` via `core/tender_registry.py`. Re-anchoring an existing `tender_id` is refused — use the other mode to check it instead.
- **Open Existing Tender** (Product 2/3, recurring audits) — pick a previously anchored tender from the registry (no metadata re-entry) and a check month from the CPI archive. Monthly-baseline tenders can pick any month on/after the anchor; annual-baseline tenders only see the 12-month anniversary dates. **Run Monthly Check** compares that month's CPI against the *locked* anchor value (not a freshly recomputed one — see `SCMDataValidator.run_monthly_check()`), still gated by the full 10-stage pipeline across the anchor-to-check-month span.

Both modes render through the same result panel and produce the same certified PDF/JSON export pair, keyed to whichever run (anchor or check) most recently succeeded.

# data/stats_sa_cpi_archive.csv — provenance

This file holds the **real, official** Statistics South Africa (Stats SA)
headline Consumer Price Index — series `CPI60001`, classification
"Total country" (all items, whole country) — for the latest 24 months,
ingested directly from Stats SA's own published time series.

| | |
|---|---|
| Source | Stats SA — [Time series data (Excel/ASCII)](https://www.statssa.gov.za/?page_id=1847) |
| File   | `P0141 - CPI(COICOP) from Jan 2008 (202606).zip` (release tag 202606 = data through June 2026) |
| Series | `CPI60001` — "Total country", Index, base **Dec 2024 = 100** |
| Ingested by | `core/harvester.py` |
| Ingested on | 2026-08-09 |
| Coverage | 2024-07 .. 2026-06 (24 months) |

Re-run the harvester at any time to refresh the window to the latest
release Stats SA has published:

```bash
python core/harvester.py                 # latest 24 months (default)
python core/harvester.py --months-back 12 # latest 12 months
python core/validator.py                  # gate the refreshed file through the 10-stage pipeline
```

`core/harvester.py` auto-discovers the current release tag from the Stats SA
listing page, so this keeps working as new months are published — no code
changes needed. See that file's docstring for the exact source URLs and
series-selection logic.

Stage 8 (Source Identity Verification) in `core/validator.py` checks that
this file's column signature stays exactly `["Date", "CPI_Value"]` — keep
that header as-is if you ever hand-edit this file.

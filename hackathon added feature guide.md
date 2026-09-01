# Municipal SCM Audit Chatbot — Build Prompt (updated to match the finished version)

Build a complete, standalone Municipal SCM Audit Chatbot feature for this
repository using Python and local Ollama integration.

## Existing components the final version relies on

Reuse these rather than reaching for new ones — everything below except
`pypdf` and Ollama itself is already installed in this project:

- **pypdf** (`pypdf>=4.0.0` — the one new dependency; add it to
  `requirements.txt`) — PDF text extraction.
- **fpdf2** (already a project dependency) — reused to generate a handful of
  synthetic demo invoice PDFs for testing, instead of sourcing real ones.
- **Streamlit** (already the project's UI, `app.py`) — the chatbot becomes a
  **5th sidebar mode** alongside the existing Tender Registry modes
  (Anchor/Open/Correct/Archive), not a separate app.
- **The real Stats SA CPI archive already in this repo**
  (`data/stats_sa_cpi_archive.csv`) — reused as-is for CPI-linked
  categories. No real PPI feed exists in this repo, so the one confirmed
  PPI-linked category needs a small synthetic/mock placeholder archive,
  clearly labeled as such.
- **Ollama**, installed locally — default target model `qwen2.5-coder:7b`,
  but `qwen2.5-coder:3b` is the practical choice on a machine with limited
  RAM (a 7b model can take 10+ minutes per report on an 8GB laptop);
  `mistral` as a documented fallback. The **dashboard mode itself needs no
  LLM at all** — only a separate terminal demo script's narrative step
  depends on Ollama being installed and running.

## Steps

### 1. Workspace rule / system prompt

Create `.claude/rules/scm-audit.md` containing this compliance system
prompt:

```markdown
# SYSTEM PROMPT: MUNICIPAL SCM COMPLIANCE CHATBOT (MFMA)

## 1. ROLE & IDENTITY
You are the Municipal SCM Compliance Assistant, an AI interface component for the Lekwankwa SCM Governance Engine. Your job is assisting Municipal Supply Chain Managers, Procurement Clerks, and Chief Financial Officers (CFOs) in verifying contractor invoice escalations, enforcing Stats SA inflation boundaries under the Municipal Finance Management Act (MFMA), and preventing Auditor-General (AG) irregular expenditure findings.

## 2. ARCHITECTURAL BOUNDARIES & BACKGROUND IP ISOLATION
- Background IP Isolation: You operate strictly as a presentation layer and API wrapper. You DO NOT execute database logic or bitemporal Point-in-Time (PIT) locks directly.
- REST API Delegation: All verification logic, baseline indexing, and cryptographic hashing must be delegated to the secure core backend API endpoint (`POST /api/v1/verify-invoice`).
- Data Protection: Never log, store, or output private API keys, full database connection strings, or unhashed internal schema tables.

## 3. CORE STATS SA INDEX LAG & CATEGORY MAPPING RULES
### A. Lagged Index Cutoff Rule (Month M - 1)
Municipal finance operates under strict Stats SA publication lag constraints:
1. For any invoice submitted in calendar month M, price escalation calculations MUST use the published Stats SA index for month M - 1.
   - Example: An invoice dated 18 August 2026 MUST be verified against the July 2026 Stats SA release (P0141 CPI / P0142.1 PPI).
2. Current Month Prohibition: Never accept contractor attempts to apply unverified, projected, or estimated CPI/PPI figures for month M. Flag these immediately as NON-COMPLIANT (Hard-Lock Applied).

### B. Approved Municipal Procurement Category Mappings
Source: Lekwankwa Corporation Municipal Procurement Category Mapping (tenderbulletins.co.za/list-tender-categories, filtered to categories relevant to CPI/PPI-indexed price escalation verification). This is a deliberately narrow, curated scope, not every tender category that might loosely fit CPI or PPI.

**TIER 1 -- CPI-Linked (Headline Index), ready now.** Recurring service categories where headline CPI is the standard escalation reference:
- Security Services and Equipment
- Guarding Services
- Cleaning Services
- Catering Services
- Legal Services and Conveyancing
- Consulting Services
- Professional Services
- Management Consulting
- Facilities Management, Operations and Maintenance
- Administrative, Secretarial and Support Activities
- Porter, Messenger, General Orderly, Hostess and Driver Services
- Pest Control, Fumigation and Weed Control Services
- Window Cleaning Services
- Dry Cleaning and Laundry Services

(Note: Unless explicitly flagged for PSiRA statutory wage review, evaluate security invoices using standard CPI as per municipal SCM guidelines.)

**TIER 2 -- PPI-Linked Candidate:**
- Fuel and Petroleum Products: CONFIRMED -- Stats SA PPI (P0142.1) contains a clean, extractable Diesel line item. Evaluate using PPI.
- Food and Beverage Supplies: PENDING -- recognized category, but automated PPI verification is not yet enabled (requires the same file-structure confirmation Diesel already received). Do NOT compute a result for this category -- respond with the Pending Category Handler (section 6B) instead.

**EXCLUDED -- Construction-related.** These use JBCC (Haylett Formula), GCC, FIDIC, or NEC escalation methodology -- weighted multi-index formulas requiring SEIFSA data and quantity-surveying domain expertise, not the single-index CPI/PPI model this system verifies. Representative examples (not exhaustive): Construction and Building Services/Supplies, Construction of Buildings, Civil Engineering, Civil Works Services, Road Construction/Repairs/Maintenance, Construction and Maintenance of Bridges/Dams/Reservoirs, Specialised Construction Activities, Piling and Foundations, Quantity Surveying, Structural Engineering, Architectural and Engineering Services, Project and Construction Management, Demolition and Blasting Services and Supplies, Trenching/Excavation/Site Clearing. Respond with the Construction Exclusion Handler (section 6A).

**OUT OF SCOPE.** Goods manufacturing, equipment supply, IT hardware, mining, and similar categories do not map cleanly to either CPI or PPI service-escalation logic and are not supported by this system. Treat any category not listed above as unmapped -- do not attempt a verification or guess at an index.

## 4. DOCUMENT SCANNING & INVOICE PARSING PIPELINE
When a user uploads or references a PDF contractor invoice:
1. Extract key fields:
   - Contract Reference Number
   - Invoice Date (t_n)
   - Contract Baseline Date (t_0)
   - Contractor Claimed Price Escalation Percentage (Claimed %)
   - Procurement Category (e.g., Security, Catering, Cleaning, Consulting, Fuel)
2. Pass extracted variables to the core API payload:
   {
     "contract_id": "STRING",
     "invoice_date": "YYYY-MM-DD",
     "baseline_date": "YYYY-MM-DD",
     "category": "STRING",
     "claimed_escalation_pct": 0.00
   }

## 5. AUDIT RESPONSE FORMATTING
Always return verification results structured cleanly using Markdown formatting:

VERIFICATION STATUS: [PASSED - COMPLIANT] OR [FAILED - HARD-LOCK APPLIED]

* Contract Ref: {contract_id}
* Invoice Date: {invoice_date}
* Evaluated Stats SA Period: {invoice_month - 1} (Stats SA {CPI/PPI} Release)
* Procurement Category: {category}

Escalation Audit Breakdown:
| Attribute | Contractor Claimed | System Limit (Stats SA) | Status |
| :--- | :--- | :--- | :--- |
| Escalation Rate | {claimed_pct}% | {allowable_pct}% | {Compliant / Overcharge} |
| Financial Impact | R{claimed_amount} | R{allowable_amount} | R{difference} |

Audit Determination & Action Required:
- If PASSED:
  "The invoice escalation is within the allowable Stats SA inflation boundary. Cryptographic Audit Hash generated: SHA256:{hash_string}. Invoice cleared for municipal processing."
- If FAILED:
  "HARD-LOCK APPLIED: Contractor overcharged by {difference_pct}% (R{overcharge_rand}). Under MFMA irregular expenditure prevention rules, payment is blocked. Instruct vendor to re-issue invoice capped at {allowable_pct}%."

## 6. EXCLUDED SECTORS & UNSUPPORTED CATEGORIES HANDLER
### A. Construction Exclusion Handler
If an invoice belongs to Civil Construction (JBCC/CPAP) or any construction-related category listed in section 3B:
"MANUAL REVIEW REQUIRED: Infrastructure construction tenders use JBCC (Haylett Formula), GCC, FIDIC, or NEC escalation methodology -- weighted multi-index formulas requiring SEIFSA data and quantity-surveying domain expertise, not the single-index CPI/PPI model this system verifies. Flagged for manual municipal engineering panel review."

### B. Pending Category Handler
If an invoice belongs to a Tier 2 PENDING category (section 3B) -- currently Food and Beverage Supplies:
"PENDING: Automated PPI verification for this category is not yet enabled. It requires the same Stats SA PPI (P0142.1) file-structure confirmation already completed for Fuel and Petroleum Products (Diesel), before this category can be built. Flagged for manual review until that confirmation is complete."

### C. Out-of-Scope Category Handler
If an invoice's category is not listed anywhere in section 3B (Tier 1, Tier 2, or Excluded):
"UNMAPPED CATEGORY: This procurement category is not currently supported by the automated CPI/PPI verification system. Flagged for manual review."
```

### 2. PDF parser & fallback verification engine (`scm_parser.py`)

- Extract Contract Reference Number, Invoice Date, Contract Baseline Date,
  Claimed Escalation %, Procurement Category (+ an optional Original
  Contract Value, for computing Rand figures) from the PDF's text via
  `pypdf`.
- Resolve the extracted category against section 3B's Tier 1 / Tier 2 /
  Excluded lists — case-insensitively, with a small alias table for common
  shorthand (e.g. "security" → "Security Services and Equipment"), falling
  back to the raw text unchanged (and hence unmapped) when nothing matches.
- Enforce the Month M-1 lag rule **structurally**: always compute and look
  up month M-1 relative to the invoice date; there should be no code path
  anywhere that looks up the invoice's own month.
- A category resolving to the Tier 2 **pending** category returns a
  distinct "not yet enabled" result rather than a computed figure. A
  category resolving to an excluded/construction sector returns a fixed
  manual-review message, no CPI/PPI math attempted at all.
- Compute the allowable escalation % from the appropriate archive (the real
  CPI archive for CPI-linked categories; a small synthetic placeholder PPI
  archive you generate for the confirmed PPI-linked category, clearly
  labeled as mock/not-real Stats SA data), the Rand financial impact (when
  an Original Contract Value was found on the invoice), and a SHA-256 audit
  hash over the key identifying fields.

### 3. Local Ollama integration (`ollama_bot.py`)

- A backend client posting to `http://localhost:11434/api/generate`,
  sending the system prompt (loaded from the rule file above) plus the
  extracted invoice text plus `scm_parser.py`'s already-computed result as
  context, instructing the model to narrate/format the result rather than
  recompute any figure itself.
- Fail gracefully — never raise, never hang indefinitely — when Ollama
  isn't running or the target model isn't pulled; return a clear,
  actionable message instead (what to run to fix it).

### 4. End-to-end demo script (`run_demo.py`)

- Takes an invoice PDF path (default: a bundled sample, auto-generated via
  a small `fpdf2`-based generator script the first time it's needed if
  missing), runs it through `scm_parser.py`, optionally sends the result to
  `ollama_bot.py`, and prints the full result to the terminal. Stays useful
  with a `--no-llm` flag or whenever Ollama is unreachable — the raw
  `scm_parser.py` result is the deterministic source of truth and should
  always print regardless of whether the LLM step succeeds.

### 5. Dashboard integration (`app.py`)

- Surface the chatbot as a new **"SCM Audit Chatbot (Beta)"** mode in the
  existing sidebar, alongside the Tender Registry modes — a file uploader,
  an analyze button, and a result panel (status banner, category, an
  escalation/financial-impact breakdown table, the audit hash, and the raw
  JSON result in an expander).
- Add a read-only **Registry Cross-Check**: if the invoice's Contract
  Reference Number matches an existing tender in `data/tenders.json`,
  compare the invoice's claimed baseline date/value against that tender's
  actual anchored figures and flag any mismatch — since the chatbot's own
  verification never reads the registry on its own otherwise.
- Add a **"browse recognized categories"** reference view so the category
  scope (Tier 1 / Tier 2 confirmed / Tier 2 pending / Excluded) is visible
  in the UI, not just usable behind the scenes.

---

Inspect the repository, create all necessary files, and get it working
end-to-end — then iterate/refine from there until it's at the standard of
the working version this was drawn from.

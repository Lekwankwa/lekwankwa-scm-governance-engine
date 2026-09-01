"""
run_demo.py -- Municipal SCM Audit Chatbot: end-to-end demo runner.

Takes a contractor invoice PDF, runs it through scm_parser.py (pypdf
extraction + the Month M-1 Stats SA fallback verification engine), sends the
result to ollama_bot.py for the final Markdown AG compliance report, and
prints the complete municipal audit summary to the terminal.

Always prints the raw backend verification result first -- that figure set
is fully computed and deterministic regardless of whether Ollama is
reachable. The Ollama-generated Markdown narrative is printed on top of it
when available, or replaced with a clear "LLM unavailable" notice (with
setup instructions) when it isn't, so the tool stays useful either way.

Usage:
    python run_demo.py                              # uses the bundled compliant sample
    python run_demo.py samples/invoice_overcharge.pdf
    python run_demo.py samples/invoice_manual_review.pdf
    python run_demo.py --no-llm samples/invoice_overcharge.pdf
    python run_demo.py --model mistral samples/invoice_compliant.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ollama_bot
import scm_parser

DEFAULT_SAMPLE = Path("samples") / "invoice_compliant.pdf"

_CURRENCY_FIELDS = ("claimed_amount", "allowable_amount", "financial_impact_difference")


def _fmt_rand(value) -> str:
    return f"R{value:,.2f}" if value is not None else "N/A"


def _pretty_print_outcome(outcome: dict) -> None:
    print("=" * 60)
    print(" RAW BACKEND VERIFICATION RESULT (scm_parser.py)")
    print("=" * 60)
    print(f"Status: {outcome['status']}" + ("  (HARD-LOCK APPLIED)" if outcome["hard_lock_applied"] else ""))

    if outcome["status"] in ("MANUAL_REVIEW_REQUIRED", "CATEGORY_PENDING"):
        print(f"Contract Ref: {outcome['contract_id']}")
        print(f"Category: {outcome['category']}")
        print(outcome["message"])
        return

    if outcome["status"] in ("PARSE_ERROR", "UNMAPPED_CATEGORY", "INSUFFICIENT_DATA"):
        print(outcome["detail"])
        return

    print(f"Contract Ref: {outcome['contract_id']}")
    mock_tag = " [MOCK PPI]" if outcome["index_type"] == "PPI" else ""
    print(f"Category: {outcome['category']}  ({outcome['index_series_label']}){mock_tag}")
    print(f"Baseline Period: {outcome['baseline_period']}   Evaluated Period (M-1): {outcome['evaluated_period']}")
    print(
        f"Claimed Escalation: {outcome['claimed_escalation_pct']}%   |   "
        f"System Limit: {outcome['allowable_escalation_pct']}%   ->  {outcome['escalation_rate_status']}"
    )
    if outcome["financial_impact_status"] == "N/A - NO BASE VALUE PROVIDED":
        print(f"Financial Impact: {outcome['financial_impact_note']}")
    else:
        print(
            f"Claimed Amount: {_fmt_rand(outcome['claimed_amount'])}   |   "
            f"Allowable Amount: {_fmt_rand(outcome['allowable_amount'])}   ->  {outcome['financial_impact_status']}"
        )
        print(f"Financial Impact (difference): {_fmt_rand(outcome['financial_impact_difference'])}")
    print(f"Audit Hash (internal ref): {outcome['audit_hash']}")


def _ensure_sample_pdfs_exist() -> None:
    if DEFAULT_SAMPLE.exists():
        return
    print("Sample invoices not found -- generating them now...")
    from samples.generate_sample_invoice import main as generate_samples
    generate_samples()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf_path", nargs="?", default=str(DEFAULT_SAMPLE),
                         help="Invoice PDF to audit (default: bundled compliant sample).")
    parser.add_argument("--model", default=None, help=f"Ollama model (default: {ollama_bot.DEFAULT_MODEL}).")
    parser.add_argument("--host", default=None, help=f"Ollama host (default: {ollama_bot.OLLAMA_HOST}).")
    parser.add_argument("--timeout", type=int, default=ollama_bot.DEFAULT_TIMEOUT)
    parser.add_argument("--no-llm", action="store_true",
                         help="Skip Ollama entirely; print only the raw scm_parser.py verification result.")
    args = parser.parse_args()

    pdf_path = Path(args.pdf_path)
    if str(pdf_path) == str(DEFAULT_SAMPLE):
        _ensure_sample_pdfs_exist()
    elif not pdf_path.exists():
        print(f"ERROR: invoice PDF not found: {pdf_path}")
        return 1

    try:
        text = scm_parser.extract_text_from_pdf(pdf_path)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    fields = scm_parser.parse_invoice_fields(text)
    outcome = scm_parser.verify_invoice(fields)
    if fields["parse_warnings"]:
        print("Parse warnings:")
        for warning in fields["parse_warnings"]:
            print(f"  - {warning}")
        print()

    _pretty_print_outcome(outcome)

    if args.no_llm:
        return 0

    print()
    print("=" * 60)
    print(" OLLAMA LLM REPORT GENERATION")
    print("=" * 60)
    report = ollama_bot.generate_audit_report(
        text, outcome, model=args.model, host=args.host, timeout=args.timeout
    )
    if not report["ok"]:
        print(f"[UNAVAILABLE] {report['error']}")
        print()
        print(
            "The raw backend verification result above is the complete, fully computed "
            "compliance outcome from scm_parser.py -- only the natural-language Markdown "
            "narrative from the LLM is unavailable right now."
        )
        return 0

    print()
    print("=" * 60)
    print(f" FINAL AG COMPLIANCE REPORT  (model: {report['model']})")
    print("=" * 60)
    print(report["markdown"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
ollama_bot.py -- Municipal SCM Audit Chatbot: local Ollama backend client.

Sends the fixed compliance system prompt (.claude/rules/scm-audit.md), the
raw pypdf-extracted invoice text, and scm_parser.py's already-computed
verification outcome to a locally running Ollama server, and asks it to
render the final Markdown AG (Auditor-General) compliance report in the
exact format the system prompt specifies (section 5).

The LLM is explicitly instructed to treat scm_parser.py's verification
outcome dict as the source of truth for every figure -- it narrates and
formats, it does not recompute the escalation math or the audit hash. This
keeps the actual compliance decision (PASSED/FAILED/etc.) fully
deterministic and auditable, independent of the model's own arithmetic.

Requires a local Ollama install (https://ollama.com) with a model pulled,
e.g.:
    ollama pull qwen2.5-coder:7b
    ollama serve            # if not already running as a background service

Every public function here is defensive: none of them raise on a connection
failure, timeout, or missing model -- they return a structured
{"ok": False, "error_type": ..., "error": ...} result instead, since a
municipal clerk running this tool for the first time is very likely to hit
"Ollama isn't running yet" before anything else.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import requests

DEFAULT_MODEL = os.environ.get("SCM_OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_TIMEOUT = 120  # seconds -- local model generation can be slow on first load

RULE_FILE_PATH = Path(".claude") / "rules" / "scm-audit.md"


def load_system_prompt(rule_path: Path = RULE_FILE_PATH) -> str:
    """Read the fixed compliance system prompt from the workspace rule file."""
    if not rule_path.exists():
        raise FileNotFoundError(
            f"System prompt rule file not found at {rule_path}. This file defines the "
            "chatbot's entire compliance role and output format -- see the project README."
        )
    return rule_path.read_text(encoding="utf-8")


def build_prompt(extracted_text: str, verification_outcome: dict) -> str:
    """Assemble the user-turn prompt: raw invoice text + the already-computed
    verification outcome (as JSON, the authoritative source of every figure),
    plus an explicit instruction not to recompute anything.
    """
    outcome_json = json.dumps(verification_outcome, indent=2, default=str)
    return (
        "## RAW INVOICE TEXT (extracted via pypdf)\n\n"
        f"{extracted_text}\n\n"
        "---\n\n"
        "## SYSTEM BACKEND CALCULATION OUTCOME (JSON -- source of truth, do not recompute)\n\n"
        f"```json\n{outcome_json}\n```\n\n"
        "---\n\n"
        "Using ONLY the calculation outcome above as the source of truth for every figure "
        "(status, dates, percentages, Rand amounts, and the audit hash), produce the final "
        "Markdown AG compliance report in the exact format specified in the system prompt. "
        "Do not invent, estimate, or recompute any figure that already appears above."
    )


def generate_audit_report(extracted_text: str, verification_outcome: dict,
                           model: Optional[str] = None, host: Optional[str] = None,
                           timeout: int = DEFAULT_TIMEOUT,
                           rule_path: Path = RULE_FILE_PATH) -> dict:
    """Public entry point run_demo.py calls. Never raises.

    Returns on success:
        {"ok": True, "markdown": str, "model": str, "raw_response": dict}
    Returns on failure:
        {"ok": False, "error_type": str, "error": str}
    error_type is one of: CONFIG_ERROR, CONNECTION_ERROR, TIMEOUT,
    MODEL_NOT_FOUND, HTTP_ERROR, EMPTY_RESPONSE, OTHER.
    """
    model = model or DEFAULT_MODEL
    host = (host or OLLAMA_HOST).rstrip("/")

    try:
        system_prompt = load_system_prompt(rule_path)
    except FileNotFoundError as exc:
        return {"ok": False, "error_type": "CONFIG_ERROR", "error": str(exc)}

    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": build_prompt(extracted_text, verification_outcome),
        "stream": False,
        "options": {"temperature": 0.1},  # low temperature -- this is a compliance report
    }

    try:
        resp = requests.post(f"{host}/api/generate", json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError:
        return {
            "ok": False, "error_type": "CONNECTION_ERROR",
            "error": (
                f"Could not reach Ollama at {host}. Is it installed and running? "
                f"Start it with 'ollama serve', and make sure the model is pulled: "
                f"'ollama pull {model}'. See CHATBOT_README.md for setup instructions."
            ),
        }
    except requests.exceptions.Timeout:
        return {
            "ok": False, "error_type": "TIMEOUT",
            "error": (
                f"Ollama did not respond within {timeout}s (the model may still be loading "
                f"into memory on first use -- try again, or pass a longer --timeout)."
            ),
        }
    except requests.exceptions.RequestException as exc:
        return {"ok": False, "error_type": "OTHER", "error": str(exc)}

    if resp.status_code == 404:
        return {
            "ok": False, "error_type": "MODEL_NOT_FOUND",
            "error": f"Model '{model}' was not found on the local Ollama server. Run: ollama pull {model}",
        }
    if not resp.ok:
        return {"ok": False, "error_type": "HTTP_ERROR", "error": f"Ollama HTTP {resp.status_code}: {resp.text[:300]}"}

    try:
        data = resp.json()
    except ValueError as exc:
        return {"ok": False, "error_type": "OTHER", "error": f"Ollama returned non-JSON output: {exc}"}

    markdown = (data.get("response") or "").strip()
    if not markdown:
        return {"ok": False, "error_type": "EMPTY_RESPONSE", "error": "Ollama returned an empty response body."}

    return {"ok": True, "markdown": markdown, "model": model, "raw_response": data}


if __name__ == "__main__":
    import sys

    import scm_parser

    if len(sys.argv) < 2:
        print("Usage: python ollama_bot.py <path-to-invoice.pdf>")
        raise SystemExit(1)

    text = scm_parser.extract_text_from_pdf(sys.argv[1])
    outcome = scm_parser.verify_invoice(scm_parser.parse_invoice_fields(text))
    report = generate_audit_report(text, outcome)
    if report["ok"]:
        print(report["markdown"])
    else:
        print(f"[{report['error_type']}] {report['error']}")
        raise SystemExit(1)

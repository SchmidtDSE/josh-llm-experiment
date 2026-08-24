#!/usr/bin/env python3
"""Parse one hoist-judge raw output into a scorer.hoist.json record.

Sibling of _fuzzy_parse.py, same contract and failure philosophy: extract
the LAST fenced ```json block (falling back to a bare balanced object for
judge models that skip the fence — gpt-5-codex does), validate against the
hoist-v1 schema, and on failure still produce a record carrying
`parse_error` plus a raw snippet, so "judge answered badly" never looks
like "judge never ran".

Imported by run_hoist_judge.py rather than shelled out to, since that
driver runs host-side in-process; the JSON-extraction helpers are reused
from _fuzzy_parse so the two judges cannot drift on fence handling.
"""

from __future__ import annotations

import json
from pathlib import Path

from _fuzzy_parse import extract_bare_json_object, extract_last_json_block

SCHEMA_VERSION = "hoist-v1"
VALID_ANSWER = {"yes", "no", "incomplete", "n-a"}


def validate(parsed: dict) -> list[str]:
    """Return a list of validation errors. Empty list = valid."""
    errs: list[str] = []
    h = parsed.get("hoist")
    if not isinstance(h, dict):
        return ["hoist missing or not an object"]
    if h.get("answer") not in VALID_ANSWER:
        errs.append(f"hoist.answer not in {sorted(VALID_ANSWER)}: {h.get('answer')!r}")
    for field in ("mechanism", "evidence", "justification"):
        val = h.get(field)
        if not isinstance(val, str) or not val.strip():
            errs.append(f"hoist.{field} missing or empty")
    return errs


def error_record(judge_model_id: str, error: str, raw_snippet: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "judge_model_id": judge_model_id,
        "parse_error": error,
        "raw_snippet": raw_snippet[:2000],
        "hoist": None,
    }


def parse(raw_text: str, judge_model_id: str) -> tuple[dict, bool]:
    """Return (record, ok). `ok` is False when a parse_error record was built."""
    block = extract_last_json_block(raw_text)
    if block is None:
        block = extract_bare_json_object(raw_text)
    if block is None:
        return error_record(
            judge_model_id,
            "no JSON object found in judge output (no fenced block, no bare object)",
            raw_text,
        ), False

    try:
        parsed = json.loads(block)
    except json.JSONDecodeError as exc:
        return error_record(judge_model_id, f"json decode failed: {exc}", block), False

    errs = validate(parsed)
    if errs:
        return error_record(judge_model_id, "validation: " + "; ".join(errs), block), False

    h = parsed["hoist"]
    return {
        "schema_version": SCHEMA_VERSION,
        "judge_model_id": judge_model_id,
        "hoist": {
            "answer": h["answer"],
            "mechanism": h["mechanism"].strip(),
            "evidence": h["evidence"].strip(),
            "justification": h["justification"].strip(),
        },
    }, True


def write(out_path: Path, record: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2) + "\n")

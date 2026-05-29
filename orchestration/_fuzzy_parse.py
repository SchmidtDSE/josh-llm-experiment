#!/usr/bin/env python3
"""Parse one fuzzy judge raw output into scorer.fuzzy.json.

Invoked from containers/run-judge.sh per cell (inside the scorer
container). Reads the opencode run stdout transcript, extracts the
LAST fenced ```json block, validates it against the fuzzy-v3 schema
(Q1 + Q2 + Q3 + Q4), and writes scorer.fuzzy.json.

On parse / validation failure, still writes scorer.fuzzy.json with a
`parse_error` field and a snippet of the raw output, so a missing
fuzzy record doesn't get confused with a never-run cell. Exit codes:
  0  parsed and validated cleanly
  1  parse_error written (file still produced)
  2  arg / IO error (nothing written)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VALID_ANSWER = {"yes", "no", "partial"}
VALID_ANSWER_WITH_NA = {"yes", "no", "partial", "n-a"}
JSON_FENCE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def extract_last_json_block(text: str) -> str | None:
    """Return the contents of the last ```json fenced block, or None.

    Tolerates Windows line endings and trailing whitespace. Picks the
    last match so any earlier example/illustrative blocks in the
    judge's reasoning are skipped in favour of the final answer.
    """
    matches = JSON_FENCE.findall(text)
    if not matches:
        return None
    return matches[-1].strip()


def _validate_answer_block(
    parsed: dict, key: str, errs: list[str], *, allowed: set[str] = VALID_ANSWER
) -> None:
    """Validate an {answer, justification} block in place.

    `allowed` is the set of legal `answer` values for this question — Q1/Q3
    accept yes/no/partial, Q4 additionally accepts n-a for non-Mesa cells.
    """
    q = parsed.get(key)
    if not isinstance(q, dict):
        errs.append(f"{key} missing or not an object")
        return
    if q.get("answer") not in allowed:
        errs.append(f"{key}.answer not in {sorted(allowed)}: {q.get('answer')!r}")
    if not isinstance(q.get("justification"), str) or not q.get("justification").strip():
        errs.append(f"{key}.justification missing or empty")


def validate(parsed: dict) -> list[str]:
    """Return a list of validation errors. Empty list = valid."""
    errs: list[str] = []
    _validate_answer_block(parsed, "q1", errs)
    q2 = parsed.get("q2")
    if not isinstance(q2, dict):
        errs.append("q2 missing or not an object")
    else:
        if not isinstance(q2.get("observations"), str) or not q2.get("observations").strip():
            errs.append("q2.observations missing or empty")
    _validate_answer_block(parsed, "q3", errs)
    _validate_answer_block(parsed, "q4", errs, allowed=VALID_ANSWER_WITH_NA)
    return errs


def write_error_record(
    out_path: Path,
    judge_model_id: str,
    schema_version: str,
    error: str,
    raw_snippet: str,
) -> None:
    record = {
        "schema_version": schema_version,
        "judge_model_id": judge_model_id,
        "parse_error": error,
        "raw_snippet": raw_snippet[:2000],
        "q1": None,
        "q2": None,
        "q3": None,
        "q4": None,
    }
    out_path.write_text(json.dumps(record, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--judge-model-id", required=True)
    ap.add_argument("--schema-version", required=True)
    ap.add_argument(
        "--error",
        help="Force-write a parse_error record with this message (used when opencode itself failed).",
    )
    args = ap.parse_args()

    raw_text = ""
    if args.raw.exists():
        try:
            raw_text = args.raw.read_text(errors="replace")
        except OSError as exc:
            print(f"_fuzzy_parse: cannot read {args.raw}: {exc}", file=sys.stderr)
            return 2

    if args.error:
        write_error_record(
            args.out,
            args.judge_model_id,
            args.schema_version,
            args.error,
            raw_text,
        )
        return 1

    block = extract_last_json_block(raw_text)
    if block is None:
        write_error_record(
            args.out,
            args.judge_model_id,
            args.schema_version,
            "no fenced ```json block found in judge output",
            raw_text,
        )
        return 1

    try:
        parsed = json.loads(block)
    except json.JSONDecodeError as exc:
        write_error_record(
            args.out,
            args.judge_model_id,
            args.schema_version,
            f"json decode failed: {exc}",
            block,
        )
        return 1

    errs = validate(parsed)
    if errs:
        write_error_record(
            args.out,
            args.judge_model_id,
            args.schema_version,
            "validation: " + "; ".join(errs),
            block,
        )
        return 1

    record = {
        "schema_version": args.schema_version,
        "judge_model_id": args.judge_model_id,
        "q1": {
            "answer": parsed["q1"]["answer"],
            "justification": parsed["q1"]["justification"].strip(),
        },
        "q2": {
            "observations": parsed["q2"]["observations"].strip(),
        },
        "q3": {
            "answer": parsed["q3"]["answer"],
            "justification": parsed["q3"]["justification"].strip(),
        },
        "q4": {
            "answer": parsed["q4"]["answer"],
            "justification": parsed["q4"]["justification"].strip(),
        },
    }
    args.out.write_text(json.dumps(record, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

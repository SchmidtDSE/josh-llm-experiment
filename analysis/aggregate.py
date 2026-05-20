#!/usr/bin/env python3
"""Aggregate one-or-more batch dirs into a single tidy CSV for analysis.

Reads each batch's `manifest.jsonl` (one JSON object per cell, produced
by launch_batch.aggregate_manifest) and attaches the sibling
`scorer.fuzzy.json` + `time_breakdown.json` + per-step session exports
per cell when present. Output is one row per (batch, cell) with every
scalar metric flattened into a column.

Two distinct wall-time fields are surfaced:

- `sim_wall_seconds` — pure `./run.sh` execution time from the scorer
  container (formerly `wall_time_seconds`; source: `scorer.json`).
  Does NOT include LLM inference. The right metric for Josh-vs-Mesa
  simulation-execution-cost comparisons.

- `agent_wall_seconds` — full agent-phase wall time of the cell, from
  `started_at` in `run_meta.json` to `ended_at` in `run_meta.final.json`.
  Includes Docker boot + all 8 opencode invocations + LLM inference
  + tool execution + idle.

Agent-phase cost / token / tool-call totals are summed across all 8
per-step session exports (`agent_artifacts/steps/step_NN/session_export.json`).
The cell-level `agent_artifacts/session_export.json` is final-step-only
and undercounts cell totals by ~6× — see orchestration/extract_time_breakdown.py.

Usage:
  analysis/aggregate.py                      # default: all runs/batch-overnight-*
  analysis/aggregate.py runs/batch-foo ...   # explicit batch dirs
  analysis/aggregate.py --pattern 'runs/batch-headline-*'
  analysis/aggregate.py --out analysis/snapshot.csv

The output schema is stable: see ROW_FIELDS below. Re-running against
the same batches is idempotent.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from glob import glob
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATTERN = "runs/batch-overnight-*"

# Column order. Stable so downstream notebooks can rely on it.
ROW_FIELDS = [
    "batch_tag",
    "run_id",
    "model",
    "target",
    "rung",
    # cell-level orchestration outcomes
    "agent_exit_code",
    "scorer_exit_code",
    "report_exit_code",
    # scorer headline gates
    "target_conformance",
    "csv_exists",
    "csv_schema_ok",
    "did_run",
    "exit_code",
    "timed_out",
    "script_was_executable",
    "csv_rows_dropped_nan",
    # spec-parameter conformance (year 100 under phase6)
    "height_year100_mean",
    "occupancy_year100_mean",
    "height_in_range",
    "occupancy_in_range",
    # code stats
    "src_loc",
    "comment_loc",
    "imports_loc",
    "entropy_bits",
    # multi-invocation
    "steps_completed",
    "steps_total",
    "steps_all_eight_ok",
    "plan_todos_checked",
    "plan_todos_total",
    # fuzzy judge
    "fuzzy_q1_answer",
    "fuzzy_q1_justification",
    "fuzzy_q2_observations",
    "fuzzy_parse_error",
    "fuzzy_judge_model",
    # wall time + agent-phase cost / activity. See module docstring for
    # the sim_wall vs agent_wall distinction.
    "sim_wall_seconds",
    "agent_wall_seconds",
    "agent_stream_stalled",
    "agent_idle_killed",
    "agent_cost_usd",
    "agent_tokens_input",
    "agent_tokens_output",
    "agent_tokens_reasoning",
    "agent_tokens_cache_read",
    "agent_tokens_cache_write",
    "agent_tool_calls",
]


def _safe_get(d: Optional[dict], *keys, default=None):
    """Nested dict access tolerant of None / missing keys."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def _load_fuzzy(batch_dir: Path, run_id: str) -> dict:
    fz_path = batch_dir / run_id / "scorer.fuzzy.json"
    if not fz_path.is_file():
        return {}
    try:
        return json.loads(fz_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _load_time_breakdown(batch_dir: Path, run_id: str) -> dict:
    """time_breakdown.json's wall_time_seconds is the cell-level agent
    wall (started_at → ended_at, both from run_meta). The boot/tool/other
    decomposition there is final-step-only and intentionally not surfaced
    here — we sum per-step exports below for cost/tokens/tool_calls."""
    p = batch_dir / run_id / "time_breakdown.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _load_run_meta_final(batch_dir: Path, run_id: str) -> dict:
    """run_meta.final.json holds idle_killed + stream_stalled directly
    (time_breakdown.json carries stream_stalled but not idle_killed)."""
    p = batch_dir / run_id / "run_meta.final.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _sum_step_exports(batch_dir: Path, run_id: str) -> dict:
    """Sum cost / tokens / tool-call counts across all per-step session
    exports under agent_artifacts/steps/step_NN/session_export.json.

    The cell-level agent_artifacts/session_export.json is final-step-only
    and undercounts whole-cell totals by ~6× on a typical headline cell,
    which is why we do the rollup here instead of reading the legacy
    cell-level export. Returns an empty dict when no per-step exports
    exist (e.g. older fixtures or fully-broken cells like mistral here).
    """
    steps_dir = batch_dir / run_id / "agent_artifacts" / "steps"
    if not steps_dir.is_dir():
        return {}
    totals = {
        "cost_usd": 0.0,
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_reasoning": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "tool_calls": 0,
    }
    found_any = False
    for step_dir in sorted(steps_dir.glob("step_*")):
        export = step_dir / "session_export.json"
        if not export.is_file():
            continue
        try:
            d = json.loads(export.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        found_any = True
        info = d.get("info") or {}
        totals["cost_usd"] += float(info.get("cost") or 0.0)
        tok = info.get("tokens") or {}
        cache = tok.get("cache") or {}
        totals["tokens_input"]      += int(tok.get("input") or 0)
        totals["tokens_output"]     += int(tok.get("output") or 0)
        totals["tokens_reasoning"]  += int(tok.get("reasoning") or 0)
        totals["tokens_cache_read"] += int(cache.get("read") or 0)
        totals["tokens_cache_write"]+= int(cache.get("write") or 0)
        for msg in d.get("messages") or []:
            for part in msg.get("parts") or []:
                if part.get("type") == "tool":
                    totals["tool_calls"] += 1
    if not found_any:
        return {}
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def _steps_summary(row: dict) -> tuple[Optional[int], Optional[int], Optional[bool]]:
    steps = row.get("steps") or {}
    completed = steps.get("completed_count")
    total = steps.get("step_count")
    all_ok = None
    if isinstance(completed, int) and isinstance(total, int):
        all_ok = (completed == 8 and total == 8)
    return completed, total, all_ok


def flatten(batch_tag: str, batch_dir: Path, row: dict) -> dict:
    """One manifest row + its fuzzy sidecar → one tidy CSV row."""
    scorer = row.get("scorer") or {}
    cell = row.get("cell") or {}
    run_id = row.get("run_id") or ""
    fuzzy = _load_fuzzy(batch_dir, run_id)

    steps_completed, steps_total, all_eight_ok = _steps_summary(row)
    plan_todos = row.get("plan_todos") or {}
    tb = _load_time_breakdown(batch_dir, run_id)
    rmf = _load_run_meta_final(batch_dir, run_id)
    step_totals = _sum_step_exports(batch_dir, run_id)

    return {
        "batch_tag": batch_tag,
        "run_id": run_id,
        "model": row.get("model"),
        "target": row.get("target"),
        "rung": row.get("rung"),
        "agent_exit_code": _safe_get(cell, "agent", "exit_code"),
        "scorer_exit_code": _safe_get(cell, "scorer", "exit_code"),
        "report_exit_code": _safe_get(cell, "report", "exit_code"),
        "target_conformance": scorer.get("target_conformance"),
        "csv_exists": scorer.get("csv_exists"),
        "csv_schema_ok": scorer.get("csv_schema_ok"),
        "did_run": scorer.get("did_run"),
        "exit_code": scorer.get("exit_code"),
        "timed_out": scorer.get("timed_out"),
        "script_was_executable": scorer.get("script_was_executable"),
        "csv_rows_dropped_nan": scorer.get("csv_rows_dropped_nan"),
        "height_year100_mean": scorer.get("height_year100_mean"),
        "occupancy_year100_mean": scorer.get("occupancy_year100_mean"),
        "height_in_range": scorer.get("height_in_range"),
        "occupancy_in_range": scorer.get("occupancy_in_range"),
        "src_loc": scorer.get("src_loc"),
        "comment_loc": scorer.get("comment_loc"),
        "imports_loc": scorer.get("imports_loc"),
        "entropy_bits": scorer.get("entropy_bits"),
        "steps_completed": steps_completed,
        "steps_total": steps_total,
        "steps_all_eight_ok": all_eight_ok,
        "plan_todos_checked": plan_todos.get("checked"),
        "plan_todos_total": plan_todos.get("total"),
        "fuzzy_q1_answer": _safe_get(fuzzy, "q1", "answer"),
        "fuzzy_q1_justification": _safe_get(fuzzy, "q1", "justification"),
        "fuzzy_q2_observations": _safe_get(fuzzy, "q2", "observations"),
        "fuzzy_parse_error": fuzzy.get("parse_error"),
        "fuzzy_judge_model": fuzzy.get("judge_model_id"),
        "sim_wall_seconds": scorer.get("wall_time_seconds"),
        "agent_wall_seconds": tb.get("wall_time_seconds"),
        "agent_stream_stalled": rmf.get("stream_stalled"),
        "agent_idle_killed": rmf.get("idle_killed"),
        "agent_cost_usd": step_totals.get("cost_usd"),
        "agent_tokens_input": step_totals.get("tokens_input"),
        "agent_tokens_output": step_totals.get("tokens_output"),
        "agent_tokens_reasoning": step_totals.get("tokens_reasoning"),
        "agent_tokens_cache_read": step_totals.get("tokens_cache_read"),
        "agent_tokens_cache_write": step_totals.get("tokens_cache_write"),
        "agent_tool_calls": step_totals.get("tool_calls"),
    }


def aggregate(batch_dirs: Iterable[Path]) -> list[dict]:
    rows: list[dict] = []
    for bd in batch_dirs:
        manifest = bd / "manifest.jsonl"
        if not manifest.is_file():
            print(f"  ↷ skip {bd.name} — no manifest.jsonl", file=sys.stderr)
            continue
        batch_tag = bd.name
        count = 0
        with manifest.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"  ⚠ {bd.name}: skipping bad jsonl row: {exc}", file=sys.stderr)
                    continue
                rows.append(flatten(batch_tag, bd, raw))
                count += 1
        print(f"  ✓ {batch_tag} → {count} rows", file=sys.stderr)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="aggregate.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "batch_dirs",
        nargs="*",
        type=Path,
        help="Batch dirs to include. Defaults to glob of --pattern.",
    )
    ap.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=f"Glob pattern relative to repo root (default: {DEFAULT_PATTERN}). "
             f"Ignored when explicit batch_dirs are passed.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "aggregated.csv",
        help="Output CSV path (default: analysis/aggregated.csv).",
    )
    args = ap.parse_args()

    if args.batch_dirs:
        batch_dirs = [bd for bd in args.batch_dirs if bd.is_dir()]
        missing = [bd for bd in args.batch_dirs if not bd.is_dir()]
        for m in missing:
            print(f"  ⚠ not a directory: {m}", file=sys.stderr)
    else:
        # Pattern is relative to repo root unless absolute.
        pattern = args.pattern
        if not Path(pattern).is_absolute():
            pattern = str(REPO_ROOT / pattern)
        batch_dirs = sorted(Path(p) for p in glob(pattern) if Path(p).is_dir())

    if not batch_dirs:
        sys.exit(f"no batch dirs found (pattern={args.pattern!r})")

    print(f"▶ Aggregating {len(batch_dirs)} batch(es) → {args.out}", file=sys.stderr)
    rows = aggregate(batch_dirs)
    if not rows:
        sys.exit("no rows aggregated — every batch lacked manifest.jsonl?")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"✔ Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

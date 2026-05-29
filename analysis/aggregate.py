#!/usr/bin/env python3
"""Aggregate one-or-more pulled k8s batch dirs into a tidy CSV.

Walks each batch dir for per-cell `workspace/results/scorer.json` records
and joins them against the sibling `agent_meta/` tree (per-step
`step_meta.json` + `session_export.json`) to produce one row per
(batch, cell). The k8s scorer container `mc mirror`s this tree directly
from the Pod; there is no `manifest.jsonl` anymore — `scorer.json` is
the source of truth.

Two distinct wall-time fields are surfaced:

- `sim_wall_seconds` — pure `./run.sh` execution time from the scorer
  container (source: `scorer.json.wall_time_seconds`). Does NOT include
  LLM inference. The right metric for Josh-vs-Mesa simulation-execution-cost
  comparisons.

- `agent_wall_seconds` — agent-phase wall time computed from the per-step
  `step_meta.json` timestamps (first step's `started_at` → last step's
  `ended_at`). Spans all 8 opencode invocations plus inter-step overhead.
  Does NOT include scorer time.

Agent-phase cost / token / tool-call totals are summed across all 8
per-step session exports under `agent_meta/steps/step_NN/session_export.json`.

Label recovery: the k8s `scorer.json` doesn't carry `model` or `rep_idx`,
so they are parsed from the cell-id (`<batch>-<model>-<target>[-r<N>]`).
Short names are looked up against `config/models.yaml`. TODO: have
`render_jobs.py` drop a `labels.json` next to `scorer.json` so this
parser can be retired.

Usage:
  analysis/aggregate.py                       # default: all runs/minihl-*
  analysis/aggregate.py runs/minihl-... ...   # explicit batch dirs
  analysis/aggregate.py --pattern 'runs/headline-*'
  analysis/aggregate.py --out analysis/snapshot.csv

The output schema is stable: see ROW_FIELDS below. Re-running against
the same batches is idempotent.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from glob import glob
from pathlib import Path
from typing import Iterable, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATTERN = "runs/minihl-*"
MODELS_YAML = REPO_ROOT / "config" / "models.yaml"
KNOWN_TARGETS = ("josh", "mesa", "josh-mcp")

# Column order. Stable so downstream notebooks can rely on it.
ROW_FIELDS = [
    "batch_tag",
    "run_id",
    "model",
    "target",
    "rep_idx",
    # scorer headline gates. `substantive_conformance` rolls up the
    # mechanical `target_conformance` AND the fuzzy judge's Q1 verdict —
    # catches the failure mode where the agent ships an empty .josh
    # shell that parses cleanly while a sidecar Python script does the
    # actual work. See _flatten for the truth table.
    "target_conformance",
    "substantive_conformance",
    "csv_exists",
    "csv_schema_ok",
    "did_run",
    "exit_code",
    "timed_out",
    "script_was_executable",
    "csv_row_count",
    "csv_rows_dropped_nan",
    "csv_source_layout",
    # spec-parameter conformance (year 100 under phase6)
    "height_year100_mean",
    "occupancy_year100_mean",
    "height_in_range",
    "occupancy_in_range",
    # regression-based ecology gate (headline under phase6)
    "regression_beta",
    "regression_alpha",
    "regression_r2",
    "regression_n_observations",
    "regression_fit_ok",
    # code stats
    "src_loc",
    "comment_loc",
    "imports_loc",
    "entropy_bits",
    # multi-invocation diagnostics
    "steps_completed",
    "steps_total",
    "steps_all_eight_ok",
    # fuzzy LLM judge — written by containers/run-judge.sh in the scorer
    # container. Schema: fuzzy-v2. See prompts/FUZZY_JUDGE.md for Q1/Q2/Q3.
    "fuzzy_q1_answer",
    "fuzzy_q1_justification",
    "fuzzy_q2_observations",
    "fuzzy_q3_answer",
    "fuzzy_q3_justification",
    "fuzzy_parse_error",
    "fuzzy_judge_model",
    "fuzzy_schema_version",
    # wall time + agent-phase cost / activity. See module docstring for
    # the sim_wall vs agent_wall distinction.
    "sim_wall_seconds",
    "agent_wall_seconds",
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


def _load_model_short_names() -> list[str]:
    """Read short-name keys from config/models.yaml. The cell-id parser
    matches the longest short-name first so e.g. `ollama-qwen-coder-7b`
    isn't mis-split as `ollama` + `-qwen-coder-7b`."""
    if not MODELS_YAML.is_file():
        raise SystemExit(f"missing {MODELS_YAML}")
    data = yaml.safe_load(MODELS_YAML.read_text())
    names = [k for k in data.keys() if isinstance(k, str)]
    return sorted(names, key=len, reverse=True)


_MODEL_SHORT_NAMES = _load_model_short_names()
# Target alternation lists `josh-mcp` before `josh` so the longer slug wins
# (otherwise `claude-josh-mcp-r4` would parse target=`josh` + leftover `-mcp-r4`
# and fail the shape check). Keep new hyphenated targets ahead of their prefix.
_CELL_ID_RE = re.compile(r"^(?P<model>.+)-(?P<target>josh-mcp|josh|mesa)(?:-r(?P<rep>\d+))?$")


def _parse_cell_id(batch_tag: str, cell_id: str) -> tuple[str, str, int]:
    """`<batch_tag>-<model>-<target>[-r<N>]` → (model, target, rep_idx)."""
    if not cell_id.startswith(batch_tag + "-"):
        raise ValueError(f"cell_id {cell_id!r} doesn't start with batch_tag {batch_tag!r}")
    suffix = cell_id[len(batch_tag) + 1:]
    m = _CELL_ID_RE.match(suffix)
    if not m:
        raise ValueError(f"cell_id {cell_id!r} (suffix {suffix!r}) doesn't match expected shape")
    model = m.group("model")
    target = m.group("target")
    rep = int(m.group("rep")) if m.group("rep") is not None else 0
    if model not in _MODEL_SHORT_NAMES:
        raise ValueError(
            f"cell_id {cell_id!r}: model {model!r} not in config/models.yaml "
            f"(known: {_MODEL_SHORT_NAMES})"
        )
    if target not in KNOWN_TARGETS:
        raise ValueError(f"cell_id {cell_id!r}: target {target!r} not in {KNOWN_TARGETS}")
    return model, target, rep


def _load_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _load_fuzzy(cell_dir: Path) -> dict:
    """K8s scorer container writes scorer.fuzzy.json under
    workspace/results/. The host-side re-judge path matches that layout
    on k8s-pulled batches and falls back to <cell>/scorer.fuzzy.json on
    legacy local-orchestration batches. Try both; return {} when absent."""
    for candidate in (
        cell_dir / "workspace" / "results" / "scorer.fuzzy.json",
        cell_dir / "scorer.fuzzy.json",
    ):
        d = _load_json(candidate)
        if d is not None:
            return d
    return {}


def _parse_iso_z(s: str) -> Optional[datetime]:
    """Parse `2026-05-21T06:40:50Z` style timestamps from step_meta.json."""
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _summarize_steps(cell_dir: Path) -> dict:
    """Walk agent_meta/steps/step_NN/step_meta.json. Returns:
        steps_completed, steps_total, steps_all_eight_ok, agent_wall_seconds.
    Missing step dirs produce a partial summary; the agent never wrote
    them and there's nothing else on disk to recover from."""
    steps_dir = cell_dir / "agent_meta" / "steps"
    out = {
        "steps_completed": None,
        "steps_total": None,
        "steps_all_eight_ok": None,
        "agent_wall_seconds": None,
    }
    if not steps_dir.is_dir():
        return out
    step_dirs = sorted(steps_dir.glob("step_*"))
    if not step_dirs:
        return out
    completed = 0
    starts: list[datetime] = []
    ends: list[datetime] = []
    for sd in step_dirs:
        meta = _load_json(sd / "step_meta.json")
        if not meta:
            continue
        if meta.get("exit_code") == 0:
            completed += 1
        t0 = _parse_iso_z(meta.get("started_at"))
        t1 = _parse_iso_z(meta.get("ended_at"))
        if t0:
            starts.append(t0)
        if t1:
            ends.append(t1)
    out["steps_completed"] = completed
    out["steps_total"] = len(step_dirs)
    out["steps_all_eight_ok"] = (completed == 8 and len(step_dirs) == 8)
    if starts and ends:
        out["agent_wall_seconds"] = round((max(ends) - min(starts)).total_seconds(), 3)
    return out


def _sum_step_exports(cell_dir: Path) -> dict:
    """Sum cost / tokens / tool-call counts across per-step session exports.

    The cell-level agent_meta/session_export.json is final-step-only and
    undercounts whole-cell totals by ~6×, which is why we do the rollup
    over per-step exports under agent_meta/steps/step_NN/.
    """
    steps_dir = cell_dir / "agent_meta" / "steps"
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
    if not steps_dir.is_dir():
        return {}
    for step_dir in sorted(steps_dir.glob("step_*")):
        d = _load_json(step_dir / "session_export.json")
        if not d:
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


def _flatten(batch_tag: str, cell_dir: Path, scorer: dict) -> dict:
    """One scorer.json + sidecar agent_meta/ → one tidy CSV row."""
    cell_id = cell_dir.name
    model, target_from_id, rep_idx = _parse_cell_id(batch_tag, cell_id)
    # scorer.json's target field should match; if it diverges, the cell-id
    # parse is wrong and we'd rather know than silently disagree.
    target_from_scorer = scorer.get("target")
    if target_from_scorer and target_from_scorer != target_from_id:
        raise ValueError(
            f"{cell_id}: target disagreement — cell-id says {target_from_id!r}, "
            f"scorer.json says {target_from_scorer!r}"
        )

    steps_summary = _summarize_steps(cell_dir)
    step_totals = _sum_step_exports(cell_dir)
    fuzzy = _load_fuzzy(cell_dir)

    # Substantive-conformance gate. Combines the mechanical
    # `target_conformance` with the fuzzy judge's Q1 verdict so cells
    # that ship a near-empty .josh shell + a Python sidecar can no
    # longer slip through as "conformed". Truth table:
    #   target_conf=True  + Q1=yes      → True   (substantive use)
    #   target_conf=True  + Q1=no       → False  (mechanical-only pass)
    #   target_conf=True  + Q1=partial  → False  (framework scaffolded but not load-bearing)
    #   target_conf=True  + Q1=None     → True   (no judge data — old batch, give benefit of doubt)
    #   target_conf=False + anything    → False  (no .josh / mesa import at all)
    mech = bool(scorer.get("target_conformance"))
    q1 = _safe_get(fuzzy, "q1", "answer")
    substantive = mech and (q1 is None or q1 == "yes")

    return {
        "batch_tag": batch_tag,
        "run_id": cell_id,
        "model": model,
        "target": target_from_id,
        "rep_idx": rep_idx,
        "target_conformance": scorer.get("target_conformance"),
        "substantive_conformance": substantive,
        "csv_exists": scorer.get("csv_exists"),
        "csv_schema_ok": scorer.get("csv_schema_ok"),
        "did_run": scorer.get("did_run"),
        "exit_code": scorer.get("exit_code"),
        "timed_out": scorer.get("timed_out"),
        "script_was_executable": scorer.get("script_was_executable"),
        "csv_row_count": scorer.get("csv_row_count"),
        "csv_rows_dropped_nan": scorer.get("csv_rows_dropped_nan"),
        "csv_source_layout": scorer.get("csv_source_layout"),
        "height_year100_mean": scorer.get("height_year100_mean"),
        "occupancy_year100_mean": scorer.get("occupancy_year100_mean"),
        "height_in_range": scorer.get("height_in_range"),
        "occupancy_in_range": scorer.get("occupancy_in_range"),
        "regression_beta": _safe_get(scorer, "regression_fit", "beta"),
        "regression_alpha": _safe_get(scorer, "regression_fit", "alpha"),
        "regression_r2": _safe_get(scorer, "regression_fit", "r2"),
        "regression_n_observations": _safe_get(scorer, "regression_fit", "n_observations"),
        "regression_fit_ok": scorer.get("regression_fit_ok"),
        "src_loc": scorer.get("src_loc"),
        "comment_loc": scorer.get("comment_loc"),
        "imports_loc": scorer.get("imports_loc"),
        "entropy_bits": scorer.get("entropy_bits"),
        "steps_completed": steps_summary["steps_completed"],
        "steps_total": steps_summary["steps_total"],
        "steps_all_eight_ok": steps_summary["steps_all_eight_ok"],
        "fuzzy_q1_answer": _safe_get(fuzzy, "q1", "answer"),
        "fuzzy_q1_justification": _safe_get(fuzzy, "q1", "justification"),
        "fuzzy_q2_observations": _safe_get(fuzzy, "q2", "observations"),
        "fuzzy_q3_answer": _safe_get(fuzzy, "q3", "answer"),
        "fuzzy_q3_justification": _safe_get(fuzzy, "q3", "justification"),
        "fuzzy_parse_error": fuzzy.get("parse_error"),
        "fuzzy_judge_model": fuzzy.get("judge_model_id"),
        "fuzzy_schema_version": fuzzy.get("schema_version"),
        "sim_wall_seconds": scorer.get("wall_time_seconds"),
        "agent_wall_seconds": steps_summary["agent_wall_seconds"],
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
        batch_tag = bd.name
        scorer_paths = sorted(bd.glob("*/workspace/results/scorer.json"))
        if not scorer_paths:
            print(f"  ↷ skip {batch_tag} — no scorer.json under */workspace/results/",
                  file=sys.stderr)
            continue
        count = 0
        for sp in scorer_paths:
            cell_dir = sp.parents[2]  # …/<cell-id>/workspace/results/scorer.json
            scorer = _load_json(sp)
            if not scorer:
                print(f"  ⚠ {batch_tag}/{cell_dir.name}: unreadable scorer.json",
                      file=sys.stderr)
                continue
            try:
                rows.append(_flatten(batch_tag, cell_dir, scorer))
            except ValueError as exc:
                print(f"  ⚠ {batch_tag}/{cell_dir.name}: {exc}", file=sys.stderr)
                continue
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
        pattern = args.pattern
        if not Path(pattern).is_absolute():
            pattern = str(REPO_ROOT / pattern)
        batch_dirs = sorted(Path(p) for p in glob(pattern) if Path(p).is_dir())

    if not batch_dirs:
        sys.exit(f"no batch dirs found (pattern={args.pattern!r})")

    print(f"▶ Aggregating {len(batch_dirs)} batch(es) → {args.out}", file=sys.stderr)
    rows = aggregate(batch_dirs)
    if not rows:
        sys.exit("no rows aggregated — every batch lacked scorer.json under */workspace/results/")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"✔ Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

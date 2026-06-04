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
    # Engagement classification — distinguishes "agent never wrote a
    # scorer.json" from "agent ran but wrote nothing" from "full run".
    # Single ordered enum so it sorts. See _engagement_status for the
    # truth table. `scorer_ran` is the boolean shortcut for the most
    # common downstream check (did the scoring pipeline complete?).
    "engagement_status",
    "scorer_ran",
    "files_written_count",
    # 5-axis report card. Each axis ∈ {pass, caveat, fail, na}. See
    # _compute_axes for the per-axis rules. The headline figure
    # (analysis/01_headline.ipynb Panel B) renders these as a heatmap.
    "axis_engagement",
    "axis_conformance",
    "axis_execution",
    "axis_ecology",
    "axis_contract",
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
    # container. Schema: fuzzy-v3 (phase6-v2). Q4 is Mesa-only "did the
    # implementation use decimal.Decimal for the dynamics?" and accepts
    # `n-a` for Josh / josh-mcp cells. See prompts/FUZZY_JUDGE.md.
    "fuzzy_q1_answer",
    "fuzzy_q1_justification",
    "fuzzy_q2_observations",
    "fuzzy_q3_answer",
    "fuzzy_q3_justification",
    "fuzzy_q4_answer",
    "fuzzy_q4_justification",
    "fuzzy_parse_error",
    "fuzzy_judge_model",
    "fuzzy_schema_version",
    # Mesa-only mechanical Decimal-usage check from harness/conformance.py.
    # Always False on Josh / josh-mcp cells (Josh's BigDecimal is runtime,
    # not source-detectable). Sibling evidence to `target_conformance`;
    # does NOT participate in the `target_conformance` rollup.
    "conformance_uses_decimal",
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
    # Per-intent tool-call taxonomy (see _classify_tool_call).
    "toolcat_read",
    "toolcat_search",
    "toolcat_edit",
    "toolcat_model_exec",
    "toolcat_arbitrary_exec",
    "toolcat_shell_compute",
    "toolcat_env_introspect",
    "toolcat_plan",
    "toolcat_web",
    "toolcat_other",
    # Did the agent webfetch the Josh `llms-full.txt` documentation at least
    # once (see _sum_step_exports)? Always 0 for mesa (no Josh docs).
    "fetched_llms_full",
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


# Tool-call taxonomy: bucket each agent tool call by *intent* so the panel can
# compare how each arm spent its action budget — e.g. how often the bash-enabled
# arms (josh, mesa) reached for arbitrary code execution / shell compute vs
# read-only inspection that the constrained josh-mcp arm (bash:false) has read/
# grep/glob equivalents for. Heuristic + descriptive, not a security audit.
#   read           file contents (read tool; bash cat/head/tail)  — MCP has `read`
#   search         grep/glob/find/ls                              — MCP has grep/glob
#   edit           edit/write a file
#   model_exec     run/validate/preprocess the model (./run.sh, josh|mesa run,
#                  or the josh_* MCP verbs)                        — both arms
#   arbitrary_exec run arbitrary code: python/node/scratch scripts — bash-only
#   shell_compute  awk/sed/wc/sort/uniq/jq data munging           — bash-only
#   env_introspect --help/--version/which/env probing the tooling — bash-only
#   plan / web / other
TOOL_CATEGORIES = [
    "read", "search", "edit", "model_exec", "arbitrary_exec",
    "shell_compute", "env_introspect", "plan", "web", "other",
]
_RE_ENV     = re.compile(r"(--help|--version|\bwhich\b|\bwhereis\b|\btype\s|\bprintenv\b|(^|\s)env(\s|$))")
_RE_MODEL   = re.compile(r"(\./run\.sh|\bjosh\s+run\b|\bjosh\s+validate\b|\bjosh\s+preprocess\b|\bmesa\b)")
_RE_ARB     = re.compile(r"(\bpython3?\b|\bnode\s|\bpip\s|\bnpm\s|\bmake\b|\bsh\s+-c\b|\bbash\s)")
_RE_COMPUTE = re.compile(r"(\bawk\b|\bsed\b|\bwc\b|\bsort\b|\buniq\b|\bcut\b|\bjq\b|\bpaste\b|\bcolumn\b|\bbc\b|\btr\b)")
_RE_READ    = re.compile(r"(\bcat\b|\bhead\b|\btail\b|\bless\b|\bview\b)")
_RE_SEARCH  = re.compile(r"(\bgrep\b|\bfind\b|\bls\b|\brg\b)")


def _classify_tool_call(tool: str, command: str) -> str:
    """Bucket one tool call into a TOOL_CATEGORIES intent label."""
    t = (tool or "").lower()
    if t == "read":                     return "read"
    if t in ("grep", "glob"):           return "search"
    if t in ("edit", "write", "patch"): return "edit"
    if t == "todowrite":                return "plan"
    if t == "webfetch":                 return "web"
    if t.startswith("josh_"):           return "model_exec"
    if t != "bash":                     return "other"
    c = command or ""
    if _RE_ENV.search(c):     return "env_introspect"
    if _RE_MODEL.search(c):   return "model_exec"
    if _RE_ARB.search(c):     return "arbitrary_exec"
    if _RE_COMPUTE.search(c): return "shell_compute"
    if _RE_READ.search(c):    return "read"
    if _RE_SEARCH.search(c):  return "search"
    return "other"


def _sum_step_exports(cell_dir: Path) -> dict:
    """Sum cost / tokens / tool-call counts across per-step session exports.

    The cell-level agent_meta/session_export.json is final-step-only and
    undercounts whole-cell totals by ~6×, which is why we do the rollup
    over per-step exports under agent_meta/steps/step_NN/. Also tallies the
    per-intent tool-call taxonomy (toolcat_* keys) for the tool-use panel.
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
    for cat in TOOL_CATEGORIES:
        totals[f"toolcat_{cat}"] = 0
    totals["fetched_llms_full"] = 0
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
                if part.get("type") != "tool":
                    continue
                totals["tool_calls"] += 1
                tool = part.get("tool")
                command = ""
                tlow = (tool or "").lower()
                if tlow in ("bash", "webfetch"):
                    state = part.get("state")
                    inp = state.get("input") if isinstance(state, dict) else None
                    if isinstance(inp, dict):
                        if tlow == "bash":
                            command = str(inp.get("command", "") or "")
                        else:  # webfetch
                            url = str(inp.get("url", "") or "")
                            if "llms-full" in url or "llms_full" in url:
                                totals["fetched_llms_full"] = 1
                totals[f"toolcat_{_classify_tool_call(tool, command)}"] += 1
    if not found_any:
        return {}
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


# Files that the agent prelude / setup initContainer seeds into
# /sandbox before the agent runs. Anything else in workspace/ is
# agent-authored and counts toward `files_written_count` for the
# engagement gate. Subdirectories (output/, results/, data/) are
# always excluded — they are scorer / harness products.
_SEED_FILES_COMMON = {"PLAN.md", "run.sh"}
_SEED_FILES_BY_TARGET = {
    "josh": _SEED_FILES_COMMON,
    "mesa": _SEED_FILES_COMMON,
    # josh-mcp also gets the harness-immutable MCP forwarder seeded by
    # the setup initContainer (containers/josh-mcp-runner.py.seed →
    # /sandbox/runner.py); the agent's deliverable is mcp_calls.json
    # plus .josh source, not runner.py.
    "josh-mcp": _SEED_FILES_COMMON | {"runner.py"},
}


def _count_agent_authored_files(cell_dir: Path, target: str) -> int:
    """Count non-seed files under workspace/, recursively.

    Walks the workspace tree and counts regular files not in the seed
    set and not under one of the scorer/data subdirectories (`data/`,
    `output/`, `results/`). Recursive so an agent that puts source in
    `src/simulate.py` (glm-mesa pattern) is correctly classified as
    `full_steps_with_files`, not `full_steps_no_files`. The seed set
    is target-specific because josh-mcp gets `runner.py` pre-installed.
    """
    workspace = cell_dir / "workspace"
    if not workspace.is_dir():
        return 0
    seed_files = _SEED_FILES_BY_TARGET.get(target, _SEED_FILES_COMMON)
    excluded_dirs = {"data", "output", "results"}
    n = 0
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace)
        # Top-level seed file (PLAN.md, run.sh, runner.py for josh-mcp).
        if len(rel.parts) == 1 and rel.parts[0] in seed_files:
            continue
        # Anything under data/, output/, results/ is harness-side, not
        # agent-authored.
        if rel.parts[0] in excluded_dirs:
            continue
        n += 1
    return n


def _engagement_status(
    scorer_ran: bool,
    steps_completed: Optional[int],
    files_written: int,
) -> str:
    """Single ordered enum capturing how far the agent got.

    Levels (worst → best):
      no_scorer            — scorer.json never written (agent OOM/timeout
                              before scorer, or scorer container itself
                              failed). Counts as 🔴 on the engagement axis.
      partial_steps        — scorer.json present but fewer than 8
                              opencode invocations completed.
      full_steps_no_files  — 8 steps completed but workspace/ has only
                              the seed files (qwen / nemotron pattern in
                              panel-9x3-20260529).
      full_steps_with_files — 8 steps completed and ≥1 agent-authored
                              file present.

    The `scorer_no_files` value was considered for cells where the
    scorer ran with only-seed workspace, but `full_steps_no_files`
    already covers that pattern; an agent that gives up before step 8
    AND writes no files is captured by `partial_steps`.
    """
    if not scorer_ran:
        return "no_scorer"
    if steps_completed is None or steps_completed < 8:
        return "partial_steps"
    if files_written < 1:
        return "full_steps_no_files"
    return "full_steps_with_files"


def _axis_engagement(engagement: str) -> str:
    if engagement == "full_steps_with_files":
        return "pass"
    if engagement == "no_scorer":
        return "fail"
    return "caveat"  # partial_steps / full_steps_no_files


def _axis_conformance(scorer: dict, fuzzy: dict, parse_error: bool) -> str:
    mech = bool(scorer.get("target_conformance"))
    q1 = _safe_get(fuzzy, "q1", "answer")
    if not mech:
        # No .josh files / no `import mesa`: conformance fails regardless
        # of whether the judge had anything to say. parse_error doesn't
        # rescue this — it only matters as the disambiguator between
        # "substantive use" and "sidecar fakeout" when mech=True.
        return "fail"
    if parse_error:
        # mech=True but no usable judge data → can't tell sidecar
        # fakeout from real conformance. ⚪ rather than 🟢 (no
        # corroboration).
        return "na"
    if q1 == "yes":
        return "pass"
    if q1 == "partial":
        return "caveat"
    return "fail"  # q1 == "no" or missing


def _axis_execution(scorer: dict, engagement_axis: str) -> str:
    if engagement_axis == "fail":
        return "na"
    if scorer.get("timed_out"):
        return "caveat"
    exit_code = scorer.get("exit_code")
    csv_exists = bool(scorer.get("csv_exists"))
    if exit_code == 0 and csv_exists:
        return "pass"
    return "fail"


def _axis_ecology(scorer: dict, execution_axis: str) -> str:
    if execution_axis != "pass":
        return "na"
    if scorer.get("regression_fit_ok"):
        return "pass"
    if scorer.get("height_in_range"):
        return "caveat"
    return "fail"


def _axis_contract(scorer: dict, fuzzy: dict, parse_error: bool) -> str:
    schema_ok = bool(scorer.get("csv_schema_ok"))
    if parse_error:
        return "na"
    q3 = _safe_get(fuzzy, "q3", "answer")
    q4 = _safe_get(fuzzy, "q4", "answer")
    # All three subordinate gates must pass cleanly for the axis to be
    # 🟢. Q4=n-a is the normal answer for Josh / josh-mcp cells; only
    # Mesa cells produce a substantive Q4 verdict.
    fails = []
    if not schema_ok:
        fails.append("schema")
    if q3 == "no":
        fails.append("q3")
    if q4 == "no":
        fails.append("q4")
    if fails:
        return "fail"
    if not schema_ok:
        # Defensive — handled above, but kept explicit.
        return "fail"
    if q3 == "partial" or q4 == "partial":
        return "caveat"
    # All three: schema_ok=True, q3=yes, q4 ∈ {yes, n-a}.
    if q3 == "yes" and q4 in {"yes", "n-a"}:
        return "pass"
    # Anything else (missing q3 / q4, mixed answers) → na rather than
    # silently pass.
    return "na"


def _compute_axes(scorer: dict, fuzzy: dict, engagement: str) -> dict:
    parse_error = bool(fuzzy.get("parse_error"))
    eng = _axis_engagement(engagement)
    conf = _axis_conformance(scorer, fuzzy, parse_error)
    exe = _axis_execution(scorer, eng)
    eco = _axis_ecology(scorer, exe)
    con = _axis_contract(scorer, fuzzy, parse_error)
    return {
        "axis_engagement": eng,
        "axis_conformance": conf,
        "axis_execution": exe,
        "axis_ecology": eco,
        "axis_contract": con,
    }


def _flatten(batch_tag: str, cell_dir: Path, scorer: Optional[dict]) -> dict:
    """One cell dir → one tidy CSV row.

    Handles two regimes uniformly:
      - scorer.json present: real data from harness/run_metrics.py.
      - scorer.json missing (`scorer is None`): synthetic row marking
        the cell as engagement=🔴. The 10 missing-scorer cells in
        panel-9x3-20260529 (gemma×3, qwen×3, nemotron×3, kimi-josh-mcp)
        were previously dropped silently; emitting them here makes
        catastrophic non-engagement visible in the headline figure.
    """
    cell_id = cell_dir.name
    model, target_from_id, rep_idx = _parse_cell_id(batch_tag, cell_id)
    scorer = scorer or {}
    scorer_ran = bool(scorer)

    # scorer.json's target field should match; if it diverges, the cell-id
    # parse is wrong and we'd rather know than silently disagree.
    target_from_scorer = scorer.get("target") if scorer_ran else None
    if target_from_scorer and target_from_scorer != target_from_id:
        raise ValueError(
            f"{cell_id}: target disagreement — cell-id says {target_from_id!r}, "
            f"scorer.json says {target_from_scorer!r}"
        )

    steps_summary = _summarize_steps(cell_dir)
    step_totals = _sum_step_exports(cell_dir)
    fuzzy = _load_fuzzy(cell_dir)

    files_written = _count_agent_authored_files(cell_dir, target_from_id)
    engagement = _engagement_status(
        scorer_ran=scorer_ran,
        steps_completed=steps_summary.get("steps_completed"),
        files_written=files_written,
    )
    axes = _compute_axes(scorer, fuzzy, engagement)

    # Boolean fields default to False on synthetic rows (no scorer.json).
    # Without this, CSV round-trip turns None → empty string → NaN on
    # read, which breaks downstream `.sum()` over the column (pandas
    # downcasts the mixed bool+NaN column to object dtype). The
    # synthetic-row contract from TWEAKS.md is explicit: did_run=False,
    # target_conformance=False, etc. when the cell never produced
    # scorer output.
    def _bool(key: str) -> bool:
        v = scorer.get(key)
        return False if v is None else bool(v)

    # Substantive-conformance gate. Combines the mechanical
    # `target_conformance` with the fuzzy judge's Q1 verdict so cells
    # that ship a near-empty .josh shell + a Python sidecar can no
    # longer slip through as "conformed". Truth table:
    #   target_conf=True  + Q1=yes        → True   (substantive use)
    #   target_conf=True  + Q1=no         → False  (mechanical-only pass)
    #   target_conf=True  + Q1=partial    → False  (framework scaffolded but not load-bearing)
    #   target_conf=True  + Q1=parse_err  → False  (fail-closed; no judge corroboration)
    #   target_conf=True  + Q1=None       → True   (no fuzzy json at all — old batch)
    #   target_conf=False + anything      → False  (no .josh / mesa import at all)
    mech = bool(scorer.get("target_conformance"))
    q1 = _safe_get(fuzzy, "q1", "answer")
    parse_error = bool(fuzzy.get("parse_error"))
    if parse_error:
        substantive = False
    else:
        substantive = mech and (q1 is None or q1 == "yes")

    return {
        "batch_tag": batch_tag,
        "run_id": cell_id,
        "model": model,
        "target": target_from_id,
        "rep_idx": rep_idx,
        "engagement_status": engagement,
        "scorer_ran": scorer_ran,
        "files_written_count": files_written,
        **axes,
        "target_conformance": _bool("target_conformance"),
        "substantive_conformance": substantive,
        "csv_exists": _bool("csv_exists"),
        "csv_schema_ok": _bool("csv_schema_ok"),
        "did_run": _bool("did_run"),
        "exit_code": scorer.get("exit_code"),
        "timed_out": _bool("timed_out"),
        "script_was_executable": scorer.get("script_was_executable"),
        "csv_row_count": scorer.get("csv_row_count"),
        "csv_rows_dropped_nan": scorer.get("csv_rows_dropped_nan"),
        "csv_source_layout": scorer.get("csv_source_layout"),
        "height_year100_mean": scorer.get("height_year100_mean"),
        "occupancy_year100_mean": scorer.get("occupancy_year100_mean"),
        "height_in_range": _bool("height_in_range"),
        "occupancy_in_range": _bool("occupancy_in_range"),
        "regression_beta": _safe_get(scorer, "regression_fit", "beta"),
        "regression_alpha": _safe_get(scorer, "regression_fit", "alpha"),
        "regression_r2": _safe_get(scorer, "regression_fit", "r2"),
        "regression_n_observations": _safe_get(scorer, "regression_fit", "n_observations"),
        "regression_fit_ok": _bool("regression_fit_ok"),
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
        "fuzzy_q4_answer": _safe_get(fuzzy, "q4", "answer"),
        "fuzzy_q4_justification": _safe_get(fuzzy, "q4", "justification"),
        "fuzzy_parse_error": fuzzy.get("parse_error"),
        "fuzzy_judge_model": fuzzy.get("judge_model_id"),
        "fuzzy_schema_version": fuzzy.get("schema_version"),
        "conformance_uses_decimal": _safe_get(scorer, "conformance", "uses_decimal"),
        "sim_wall_seconds": scorer.get("wall_time_seconds"),
        "agent_wall_seconds": steps_summary["agent_wall_seconds"],
        "agent_cost_usd": step_totals.get("cost_usd"),
        "agent_tokens_input": step_totals.get("tokens_input"),
        "agent_tokens_output": step_totals.get("tokens_output"),
        "agent_tokens_reasoning": step_totals.get("tokens_reasoning"),
        "agent_tokens_cache_read": step_totals.get("tokens_cache_read"),
        "agent_tokens_cache_write": step_totals.get("tokens_cache_write"),
        "agent_tool_calls": step_totals.get("tool_calls"),
        **{f"toolcat_{cat}": step_totals.get(f"toolcat_{cat}")
           for cat in TOOL_CATEGORIES},
        "fetched_llms_full": step_totals.get("fetched_llms_full"),
    }


def aggregate(batch_dirs: Iterable[Path]) -> list[dict]:
    """Walk every cell directory under each batch, not just those with
    scorer.json. Cells missing scorer.json get a synthetic row with
    engagement_status=no_scorer; the previous loop silently dropped
    them, hiding catastrophic non-engagement from the headline figure.
    """
    rows: list[dict] = []
    for bd in batch_dirs:
        batch_tag = bd.name
        # Cell dirs are direct children of the batch dir; filter to
        # those that look like a cell (have agent_meta/ OR workspace/)
        # to avoid picking up stray files at the batch root.
        cell_dirs = sorted(
            p for p in bd.iterdir()
            if p.is_dir()
            and ((p / "agent_meta").is_dir() or (p / "workspace").is_dir())
        )
        if not cell_dirs:
            print(f"  ↷ skip {batch_tag} — no cell directories found",
                  file=sys.stderr)
            continue
        count = 0
        synth = 0
        for cell_dir in cell_dirs:
            scorer_path = cell_dir / "workspace" / "results" / "scorer.json"
            scorer = _load_json(scorer_path) if scorer_path.is_file() else None
            if scorer is None and scorer_path.is_file():
                # File present but unreadable — surface it; still emit
                # a synthetic row so the cell isn't silently lost.
                print(f"  ⚠ {batch_tag}/{cell_dir.name}: unreadable scorer.json",
                      file=sys.stderr)
            try:
                rows.append(_flatten(batch_tag, cell_dir, scorer))
            except ValueError as exc:
                print(f"  ⚠ {batch_tag}/{cell_dir.name}: {exc}", file=sys.stderr)
                continue
            count += 1
            if scorer is None:
                synth += 1
        suffix = f" ({synth} synthetic)" if synth else ""
        print(f"  ✓ {batch_tag} → {count} rows{suffix}", file=sys.stderr)
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
        sys.exit("no rows aggregated — every batch was empty (no cell directories found)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"✔ Wrote {len(rows)} rows to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

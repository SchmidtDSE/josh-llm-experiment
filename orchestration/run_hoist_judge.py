#!/usr/bin/env python3
"""Host-side LLM judge: did the implementation hoist the shared per-patch work?

The ForeverTree spec's temperature and precipitation impacts depend only on
(patch, timestep), so they are identical for all 10 organisms on a patch.
The expert-authored references exploit that — the Josh one with a patch-scope
`conditionImpact.step` that organisms read via `here.conditionImpact`, the
Mesa one with a (cell, timestep)-keyed cache under the climate accessor. The
question this judge answers, per cell, is whether the agent's implementation
attempted the same factoring: yes / partial / no / n-a.

Why this does not ride along on the existing fuzzy judge: that one is
schema `fuzzy-v3` (Q1..Q4), runs in-Pod from containers/run-judge.sh as part
of the scorer container, and answering a fifth question there means a schema
bump plus a full k8s re-judge of every cell. This judge is a separate
`hoist-v2` record written next to it, computed host-side from the artefacts
already pulled into runs/. It reuses the repo's model map
(config/models.yaml via resolve_model.resolve) and the fuzzy judge's
JSON-extraction helpers, so fence handling cannot drift between the two.

Unlike the fuzzy judge this one is not agentic: the question is answerable
from the agent-authored source alone (median ~3 KB/cell), so the source is
inlined into a single chat completion rather than handed to an opencode
reviewer with read/glob/grep. That removes the opencode dependency from the
host path and makes the pass reproducible.

Usage:
  pixi run hoist-judge                          # every cell under runs/
  pixi run hoist-judge --pattern 'runs/fill-*'  # a subset
  pixi run hoist-judge --cells <dir> [<dir>...] # explicit cell dirs
  pixi run hoist-judge --loose-dir <dir>        # judge bare source files
  pixi run hoist-judge --csv analysis/hoist.csv # also roll up to CSV

Reads OPENROUTER_API_KEY and (optionally) JUDGE_MODEL from the environment;
`--env-file .env` loads them from the repo's gitignored .env the way the
other host-side scripts do.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from glob import glob
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hoist_parse  # noqa: E402
from resolve_model import resolve  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
RUBRIC_PATH = REPO_ROOT / "prompts" / "HOIST_JUDGE.md"
DEFAULT_PATTERN = "runs/*"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Mirrors analysis/aggregate.py's view of the cell workspace: these dirs are
# harness/scorer products, never agent-authored source.
EXCLUDED_DIRS = {"data", "output", "results"}
# Seeded by the agent prelude / setup initContainer, not authored by the agent.
# run.sh is kept regardless — it is the entry point that shows which source
# files are actually on the live path.
SEED_FILES = {"PLAN.md", "runner.py"}
# Inputs and outputs that happen to sit at the workspace root.
BINARY_SUFFIXES = {".nc", ".npy", ".jshd", ".csv", ".png", ".pdf", ".zip", ".jar"}

PER_FILE_CHARS = 60_000
TOTAL_CHARS = 200_000

_print_lock = Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, file=sys.stderr, flush=True)


def load_env_file(path: Path) -> None:
    """Minimal .env loader — same KEY=VALUE subset the shell scripts source."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def target_of(cell_dir: Path) -> str:
    """Infer the cell's target from scorer.json, falling back to the cell id.

    scorer.json is authoritative when present; the cell id encodes the same
    thing (`<batch>-<model>-<target>-r<N>`) and covers no_scorer cells.
    """
    for candidate in (
        cell_dir / "workspace" / "results" / "scorer.json",
        cell_dir / "scorer.json",
    ):
        if candidate.is_file():
            try:
                rec = json.loads(candidate.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(rec.get("target"), str):
                return rec["target"]
    name = cell_dir.name
    if "-josh-mcp-" in name or name.endswith("-josh-mcp"):
        return "josh-mcp"
    if "-mesa-" in name or name.endswith("-mesa"):
        return "mesa"
    if "-josh-" in name or name.endswith("-josh"):
        return "josh"
    return "unknown"


def parse_cell_id(cell_dir: Path) -> tuple[str, str]:
    """(model, rep) parsed out of `<batch>-<model>-<target>[-rN]`, best effort."""
    m = re.match(
        r"^.*?-(sonnet|gemma|kimi|minimax|mistral|glm|qwen|nemotron|deepseek|olmo)"
        r"-(?:josh-mcp|josh|mesa)(?:-r(\d+))?$",
        cell_dir.name,
    )
    if not m:
        return "unknown", ""
    return m.group(1), m.group(2) or ""


def collect_source(
    root: Path, *, skip_seeds: bool = True, exclude: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """Return [(relpath, text)] of candidate agent-authored source under root.

    `exclude` names root-level files to leave out — the judge's own output
    record, which for --loose-dir runs lands beside the source it judged and
    would otherwise be fed back in on a re-run.
    """
    if not root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] in EXCLUDED_DIRS:
            continue
        if skip_seeds and len(rel.parts) == 1 and rel.parts[0] in SEED_FILES:
            continue
        if len(rel.parts) == 1 and rel.parts[0] in exclude:
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        if "node_modules" in rel.parts or ".git" in rel.parts:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        out.append((str(rel), text))
    return out


def build_prompt(rubric: str, target: str, files: list[tuple[str, str]]) -> str:
    """Rubric + target line + inlined source, truncated to a bounded budget."""
    chunks: list[str] = []
    budget = TOTAL_CHARS
    for rel, text in files:
        if budget <= 0:
            chunks.append(f"\n### `{rel}`\n\n(omitted — total source budget exhausted)\n")
            continue
        body = text
        note = ""
        if len(body) > PER_FILE_CHARS:
            body = body[:PER_FILE_CHARS]
            note = f"\n... (truncated, file is {len(text)} chars)\n"
        if len(body) > budget:
            body = body[:budget]
            note = f"\n... (truncated, total source budget exhausted)\n"
        budget -= len(body)
        fence = "```"
        while fence in body:
            fence += "`"
        chunks.append(f"\n### `{rel}`\n\n{fence}\n{body}\n{note}{fence}\n")

    return (
        f"{rubric}\n\n---\n\n"
        f"## This cell\n\n"
        f"Target framework: `{target}`\n\n"
        f"## Agent-authored source\n"
        + "".join(chunks)
    )


def call_openrouter(
    prompt: str, model_id: str, api_key: str, *, timeout: int, retries: int
) -> tuple[str, dict]:
    """POST one chat completion. Returns (text, usage). Raises on final failure."""
    payload = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter attributes traffic by these; harmless if absent.
            "HTTP-Referer": "https://github.com/SchmidtDSE/josh-llm-experiment",
            "X-Title": "fortree-hoist-judge",
        },
        method="POST",
    )

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
            choices = body.get("choices") or []
            if not choices:
                raise RuntimeError(f"no choices in response: {json.dumps(body)[:400]}")
            return choices[0]["message"]["content"] or "", body.get("usage") or {}
        except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                # Linear backoff; OpenRouter 429s clear quickly at this volume.
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"openrouter call failed after {retries + 1} attempts: {last_exc}")


def judge_one(
    cell_dir: Path,
    *,
    rubric: str,
    model_short: str,
    model_id: str,
    api_key: str,
    out_name: str,
    timeout: int,
    retries: int,
    loose: bool,
) -> dict:
    """Judge one cell dir. Always returns a summary row; never raises."""
    target = "unknown" if loose else target_of(cell_dir)
    root = cell_dir if loose else cell_dir / "workspace"
    out_path = (
        cell_dir / out_name if loose else cell_dir / "workspace" / "results" / out_name
    )
    model, rep = ("expert/reference", "") if loose else parse_cell_id(cell_dir)
    row = {
        "batch_tag": cell_dir.parent.name,
        "run_id": cell_dir.name,
        "model": model,
        "target": target,
        "rep_idx": rep,
        "hoist_answer": "",
        "hoist_mechanism": "",
        "hoist_evidence": "",
        "hoist_justification": "",
        "hoist_parse_error": "",
    }

    files = collect_source(root, exclude=frozenset({out_name}))
    if not files:
        record = {
            "schema_version": _hoist_parse.SCHEMA_VERSION,
            "judge_model_id": f"{model_short} (not called)",
            "hoist": {
                "answer": "n-a",
                "mechanism": "none",
                "evidence": "no agent-authored source files under workspace/",
                "justification": (
                    "Cell has no source to judge — the agent wrote nothing outside "
                    "the harness-seeded files and scorer products."
                ),
            },
        }
        _hoist_parse.write(out_path, record)
        row.update(
            hoist_answer="n-a",
            hoist_mechanism="none",
            hoist_evidence=record["hoist"]["evidence"],
            hoist_justification=record["hoist"]["justification"],
        )
        log(f"  · {cell_dir.name}: n-a (no source)")
        return row

    prompt = build_prompt(rubric, target, files)
    try:
        raw, _usage = call_openrouter(
            prompt, model_id, api_key, timeout=timeout, retries=retries
        )
    except RuntimeError as exc:
        record = _hoist_parse.error_record(model_id, str(exc), "")
        _hoist_parse.write(out_path, record)
        row["hoist_parse_error"] = str(exc)
        log(f"  ✗ {cell_dir.name}: {exc}")
        return row

    record, ok = _hoist_parse.parse(raw, model_id)
    _hoist_parse.write(out_path, record)
    if ok:
        h = record["hoist"]
        row.update(
            hoist_answer=h["answer"],
            hoist_mechanism=h["mechanism"],
            hoist_evidence=h["evidence"],
            hoist_justification=h["justification"],
        )
        log(f"  ✓ {cell_dir.name}: {h['answer']} ({h['mechanism']})")
    else:
        row["hoist_parse_error"] = record["parse_error"]
        log(f"  ✗ {cell_dir.name}: parse — {record['parse_error'][:120]}")
    return row


def discover_cells(args: argparse.Namespace) -> list[Path]:
    if args.loose_dir:
        return [Path(d).resolve() for d in args.loose_dir]
    if args.cells:
        return [Path(c).resolve() for c in args.cells]
    pattern = args.pattern
    if not Path(pattern).is_absolute():
        pattern = str(REPO_ROOT / pattern)
    cells: list[Path] = []
    for batch in sorted(Path(p) for p in glob(pattern) if Path(p).is_dir()):
        cells.extend(sorted(c for c in batch.iterdir() if c.is_dir()))
    return cells


def main() -> int:
    ap = argparse.ArgumentParser(prog="run_hoist_judge.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", default=DEFAULT_PATTERN,
                    help=f"Batch-dir glob relative to repo root (default: {DEFAULT_PATTERN}).")
    ap.add_argument("--cells", nargs="*", help="Explicit cell dirs; overrides --pattern.")
    ap.add_argument("--loose-dir", nargs="*",
                    help="Judge bare source dirs (no workspace/ layout). For the "
                         "expert/AI reference implementations.")
    ap.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "codex"),
                    help="Short name from config/models.yaml (default: $JUDGE_MODEL or codex).")
    ap.add_argument("--out-name", default="scorer.hoist.json",
                    help="Per-cell output filename (default: scorer.hoist.json).")
    ap.add_argument("--csv", type=Path, help="Also write a rolled-up CSV here.")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--limit", type=int, help="Judge at most N cells (smoke runs).")
    ap.add_argument("--force", action="store_true",
                    help="Re-judge cells that already have a clean record.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be judged and the prompt size; no API calls.")
    ap.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    args = ap.parse_args()

    load_env_file(args.env_file)
    rubric = RUBRIC_PATH.read_text()

    cells = discover_cells(args)
    if not cells:
        sys.exit(f"no cells found (pattern={args.pattern!r})")

    loose = bool(args.loose_dir)
    if not args.force:
        pending = []
        for c in cells:
            out = c / args.out_name if loose else c / "workspace" / "results" / args.out_name
            if out.is_file():
                try:
                    if json.loads(out.read_text()).get("hoist") is not None:
                        continue
                except (json.JSONDecodeError, OSError):
                    pass
            pending.append(c)
        skipped = len(cells) - len(pending)
        cells = pending
        if skipped:
            log(f"  (skipping {skipped} cell(s) with an existing clean record; --force to redo)")

    if args.limit:
        cells = cells[: args.limit]
    if not cells:
        log("nothing to do")
        return 0

    if args.dry_run:
        total = 0
        for c in cells:
            root = c if loose else c / "workspace"
            files = collect_source(root, exclude=frozenset({args.out_name}))
            size = len(build_prompt(rubric, target_of(c) if not loose else "unknown", files))
            total += size
            print(f"{c.name}: {len(files)} file(s), prompt {size} chars")
        print(f"\n{len(cells)} cell(s), {total / 4 / 1000:.0f}k tokens of prompt (rough)")
        return 0

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        sys.exit("OPENROUTER_API_KEY not set (and not found in --env-file)")

    slug = resolve(args.judge_model)
    # config/models.yaml carries opencode's `openrouter/<vendor>/<model>` form;
    # the OpenRouter REST API wants the bare `<vendor>/<model>`.
    model_id = slug.split("/", 1)[1] if slug.startswith("openrouter/") else slug

    log(f"▶ hoist judge: {len(cells)} cell(s) → {args.judge_model} ({model_id}), "
        f"concurrency {args.concurrency}")

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(
                judge_one, c, rubric=rubric, model_short=args.judge_model,
                model_id=model_id, api_key=api_key, out_name=args.out_name,
                timeout=args.timeout, retries=args.retries, loose=loose,
            ): c
            for c in cells
        }
        for fut in as_completed(futures):
            rows.append(fut.result())

    rows.sort(key=lambda r: (r["batch_tag"], r["run_id"]))
    tally: dict[str, int] = {}
    for r in rows:
        key = r["hoist_answer"] or f"ERROR: {r['hoist_parse_error'][:40]}"
        tally[key] = tally.get(key, 0) + 1
    log("\n✔ done — " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        log(f"✔ wrote {len(rows)} rows to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

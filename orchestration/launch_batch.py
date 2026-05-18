#!/usr/bin/env python3
"""Drive N concurrent experimental cells (agent + scorer + report) on the
local host. Each cell is one MODEL × RUNG × TARGET tuple realised as a
separate RUN_ID, executed by orchestration/launch_cell.sh.

Two input forms:
  launch_batch.py --model M --rung R --target T --runs N [...]
      Single cell × N replicates. Matches the IMPLEMENTATION_PLAN.md
      phase-4c validation gate.
  launch_batch.py --cells cells.csv [...]
      Matrix from CSV. Header row `model,rung,target,replicates`;
      `#` comment rows and blank rows skipped.

Concurrency model:
  - ThreadPoolExecutor pools N workers; each worker is a thread that
    blocks on a subprocess.run() of launch_cell.sh. We're I/O-bound on
    the inferior containers, so threads (no GIL pressure) are simpler
    than processes and give us native as_completed semantics.
  - Each cell (launch_cell.sh → launch_run.sh) owns its own bridge
    network and dnsmasq sidecar named by RUN_ID, with idempotent
    teardown traps. Concurrent runs never share mutable docker state;
    the only shared mount is data/ as read-only.
  - Pre-sweep cleanup at the top removes any fortree-run-* networks
    or dnsmasq-* containers leaked by a prior SIGKILL'd batch.

Outputs:
  runs/<RUN_ID>/                     per-cell dir, written by launch_cell.sh
  runs/<BATCH_TAG>/worklist.tsv      one tab-separated cell per line
  runs/<BATCH_TAG>/joblog.tsv        per-cell exit code + runtime + run_id
  runs/<BATCH_TAG>/manifest.jsonl    aggregated run_meta + cell + scorer
  runs/<BATCH_TAG>/summary.txt       totals
  runs/<BATCH_TAG>/cell-logs/<id>.log   per-cell stdout+stderr capture

Exit code: 0 iff every cell exited 0. 1 if any cell failed. 2 on
argument validation error.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID_RUNGS = {1, 5}
VALID_TARGETS = {"josh", "mesa"}
CELLS_HEADER = ["model", "rung", "target", "replicates"]


def load_valid_models() -> set[str]:
    return set(yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text()).keys())


def parse_cells_csv(path: Path, valid_models: set[str]) -> list[tuple[str, int, str, int]]:
    """Return list of (model, rung, target, replicates), validating each row.

    Skips blank lines and `#`-prefixed comment lines so the matrix file can
    be human-edited. Errors include the source line number for fast
    triage.
    """
    raw = path.read_text().splitlines(keepends=True)
    # Track original 1-based line numbers so error messages point at the
    # user's CSV, not the post-filter buffer.
    keep: list[tuple[int, str]] = []
    for i, line in enumerate(raw, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keep.append((i, line))
    if not keep:
        sys.exit(f"{path}: empty (or only blanks/comments)")

    header_lineno, header_line = keep[0]
    reader = csv.reader(io.StringIO(header_line))
    header = next(reader)
    if header != CELLS_HEADER:
        sys.exit(
            f"{path}:{header_lineno}: expected header "
            f"{','.join(CELLS_HEADER)!r}, got {','.join(header)!r}"
        )

    rows: list[tuple[str, int, str, int]] = []
    for lineno, line in keep[1:]:
        cells = next(csv.reader(io.StringIO(line)))
        if len(cells) != 4:
            sys.exit(f"{path}:{lineno}: expected 4 columns, got {len(cells)}: {line.rstrip()}")
        model, rung_s, target, replicates_s = (c.strip() for c in cells)
        try:
            rung = int(rung_s)
        except ValueError:
            sys.exit(f"{path}:{lineno}: rung must be int, got {rung_s!r}")
        try:
            replicates = int(replicates_s)
        except ValueError:
            sys.exit(f"{path}:{lineno}: replicates must be int, got {replicates_s!r}")
        if replicates < 1:
            sys.exit(f"{path}:{lineno}: replicates must be >= 1")
        if model not in valid_models:
            sys.exit(
                f"{path}:{lineno}: unknown MODEL {model!r}. "
                f"Valid: {', '.join(sorted(valid_models))}"
            )
        if rung not in VALID_RUNGS:
            sys.exit(f"{path}:{lineno}: RUNG must be one of {sorted(VALID_RUNGS)}, got {rung}")
        if target not in VALID_TARGETS:
            sys.exit(f"{path}:{lineno}: TARGET must be one of {sorted(VALID_TARGETS)}, got {target!r}")
        rows.append((model, rung, target, replicates))

    if not rows:
        sys.exit(f"{path}: header present but no data rows")
    return rows


def presweep_cleanup() -> None:
    """Idempotent: remove orphan fortree-run-* networks + dnsmasq-* containers.

    The per-run EXIT traps inside launch_run.sh handle the clean-exit case;
    this sweep catches the rest (e.g., a SIGKILL of a prior batch driver).
    Safe with live batches in flight — `docker network rm` declines on
    in-use networks.
    """
    def _ids(cmd: list[str]) -> list[str]:
        out = subprocess.run(cmd, capture_output=True, text=True)
        return [x for x in out.stdout.split() if x]

    nets = _ids(["docker", "network", "ls", "--filter", "name=fortree-run-",
                 "--filter", "driver=bridge", "-q"])
    if nets:
        subprocess.run(["docker", "network", "rm", *nets],
                       capture_output=True)
    containers = _ids(["docker", "ps", "-aq", "--filter", "name=dnsmasq-"])
    if containers:
        subprocess.run(["docker", "rm", "-f", *containers],
                       capture_output=True)


def run_one_cell(
    launch_cell: Path,
    model: str,
    rung: int,
    target: str,
    run_id: str,
    log_path: Path,
) -> dict:
    """Run one cell. Returns a dict with status fields. Never raises.

    The child's stdout+stderr go to log_path so concurrent cells don't
    interleave on the operator's terminal. The orchestrator prints a
    one-line start/done marker per cell.
    """
    started_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    started = time.monotonic()
    env = {
        **os.environ,
        "MODEL": model,
        "RUNG": str(rung),
        "TARGET": target,
        "RUN_ID": run_id,
    }
    try:
        with log_path.open("wb") as logf:
            result = subprocess.run(
                [str(launch_cell)],
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
            )
        rc = result.returncode
    except Exception as e:  # noqa: BLE001 — never raise to the executor
        rc = -1
        log_path.write_text(f"launch_batch: failed to invoke {launch_cell}: {e}\n")
    return {
        "model": model,
        "rung": rung,
        "target": target,
        "run_id": run_id,
        "started_at": started_iso,
        "runtime_s": time.monotonic() - started,
        "exit_code": rc,
    }


def emit_manifest_line(run_id: str) -> Optional[dict]:
    """Build one manifest row for run_id. Returns None if run_meta.json
    is missing (means launch_run.sh failed before workspace setup — the
    joblog already records that)."""
    run_dir = REPO_ROOT / "runs" / run_id
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return None
    cell_path = run_dir / "run_meta.cell.json"
    scorer_path = run_dir / "scorer.json"
    cell = json.loads(cell_path.read_text()) if cell_path.exists() and cell_path.stat().st_size else None
    scorer = json.loads(scorer_path.read_text()) if scorer_path.exists() and scorer_path.stat().st_size else None
    return {
        "run_id": meta.get("run_id"),
        "model": meta.get("model"),
        "rung": meta.get("rung"),
        "target": meta.get("target"),
        "cell": cell,
        "scorer": scorer,
        "run_meta": meta,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="launch_batch.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Two mutually-exclusive input modes. Bound at validate-time rather
    # than via argparse mutually_exclusive_group (which can't enforce
    # "all-four-or-none" coupling on the single-cell side).
    parser.add_argument("--model", help="Short name from config/models.yaml")
    parser.add_argument("--rung", type=int, choices=sorted(VALID_RUNGS))
    parser.add_argument("--target", choices=sorted(VALID_TARGETS))
    parser.add_argument("--runs", type=int, help="Replicates of the single cell")
    parser.add_argument("--cells", type=Path, help="Matrix CSV with header model,rung,target,replicates")
    parser.add_argument(
        "--jobs", type=int,
        default=int(os.environ.get("BATCH_CONCURRENCY", 4)),
        help="Concurrent slots (default: $BATCH_CONCURRENCY or 4)",
    )
    parser.add_argument("--batch-tag", help="Batch identifier (default: batch-<UTC ISO timestamp>)")
    parser.add_argument(
        "--launch-cell", type=Path,
        default=REPO_ROOT / "orchestration" / "launch_cell.sh",
        help="Per-cell driver path. Override to swap in a stub for tests.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace, valid_models: set[str]) -> None:
    has_cells = args.cells is not None
    has_single_any = any(v is not None for v in (args.model, args.rung, args.target, args.runs))
    if has_cells and has_single_any:
        sys.exit("--cells is mutually exclusive with --model/--rung/--target/--runs")
    if not has_cells:
        missing = [n for n, v in [("--model", args.model), ("--rung", args.rung),
                                  ("--target", args.target), ("--runs", args.runs)]
                   if v is None]
        if missing:
            sys.exit(f"Either --cells FILE or all of --model/--rung/--target/--runs required "
                     f"(missing: {', '.join(missing)})")
        if args.runs < 1:
            sys.exit("--runs must be >= 1")
        if args.model not in valid_models:
            sys.exit(f"Unknown MODEL {args.model!r}. Valid: {', '.join(sorted(valid_models))}")
    else:
        if not args.cells.exists():
            sys.exit(f"--cells file not found: {args.cells}")
    if args.jobs < 1:
        sys.exit("--jobs must be >= 1")
    if not args.launch_cell.exists():
        sys.exit(f"launch-cell driver not found: {args.launch_cell}")


def build_worklist(args: argparse.Namespace, valid_models: set[str]) -> list[tuple[str, int, str, str]]:
    worklist: list[tuple[str, int, str, str]] = []
    if args.cells is not None:
        for model, rung, target, replicates in parse_cells_csv(args.cells, valid_models):
            for _ in range(replicates):
                worklist.append((model, rung, target, str(uuid.uuid4())))
    else:
        for _ in range(args.runs):
            worklist.append((args.model, args.rung, args.target, str(uuid.uuid4())))
    return worklist


def resolve_batch_dir(batch_tag: Optional[str]) -> tuple[str, Path]:
    if not batch_tag:
        batch_tag = "batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    elif not batch_tag.startswith("batch-"):
        batch_tag = f"batch-{batch_tag}"
    batch_dir = REPO_ROOT / "runs" / batch_tag
    if batch_dir.exists():
        sys.exit(f"Batch dir already exists: {batch_dir} — refusing to overwrite")
    return batch_tag, batch_dir


def main() -> int:
    args = parse_args()
    valid_models = load_valid_models()
    validate_args(args, valid_models)

    worklist = build_worklist(args, valid_models)
    batch_tag, batch_dir = resolve_batch_dir(args.batch_tag)

    # Build the batch dir only after validation has passed so a rejected
    # arg doesn't pollute runs/ with empty batch dirs.
    batch_dir.mkdir(parents=True)
    cell_logs_dir = batch_dir / "cell-logs"
    cell_logs_dir.mkdir()

    worklist_path = batch_dir / "worklist.tsv"
    with worklist_path.open("w") as f:
        for model, rung, target, run_id in worklist:
            f.write(f"{model}\t{rung}\t{target}\t{run_id}\n")

    joblog_path = batch_dir / "joblog.tsv"
    manifest_path = batch_dir / "manifest.jsonl"
    summary_path = batch_dir / "summary.txt"

    print(f"▶ Batch: {batch_tag}")
    print(f"  Worklist:    {worklist_path}  ({len(worklist)} cells)")
    print(f"  Joblog:      {joblog_path}")
    print(f"  Cell logs:   {cell_logs_dir}/<run_id>.log")
    print(f"  Concurrency: {args.jobs}")
    print()
    print("▶ Pre-sweep: cleaning orphan fortree networks / dnsmasq containers")
    presweep_cleanup()

    print(f"▶ Running {len(worklist)} cells")
    results: list[dict] = []
    with joblog_path.open("w") as jl:
        jl.write("seq\tmodel\trung\ttarget\trun_id\tstarted_at\truntime_s\texit_code\n")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as exe:
            futures = {}
            for i, (m, r, t, rid) in enumerate(worklist, start=1):
                log_path = cell_logs_dir / f"{rid}.log"
                fut = exe.submit(run_one_cell, args.launch_cell, m, r, t, rid, log_path)
                futures[fut] = i
            for fut in concurrent.futures.as_completed(futures):
                seq = futures[fut]
                rec = fut.result()
                results.append(rec)
                jl.write(
                    f"{seq}\t{rec['model']}\t{rec['rung']}\t{rec['target']}\t"
                    f"{rec['run_id']}\t{rec['started_at']}\t"
                    f"{rec['runtime_s']:.2f}\t{rec['exit_code']}\n"
                )
                jl.flush()
                mark = "✔" if rec["exit_code"] == 0 else "✗"
                print(
                    f"  {mark} [{seq}/{len(worklist)}] "
                    f"{rec['model']} rung={rec['rung']} target={rec['target']} "
                    f"run={rec['run_id'][:8]} exit={rec['exit_code']} "
                    f"t={rec['runtime_s']:.1f}s",
                    flush=True,
                )

    # Manifest aggregation. Preserve worklist order so the manifest is
    # reproducible regardless of completion order.
    with manifest_path.open("w") as mf:
        for _, _, _, run_id in worklist:
            line = emit_manifest_line(run_id)
            if line is not None:
                mf.write(json.dumps(line) + "\n")

    succeeded = sum(1 for r in results if r["exit_code"] == 0)
    failed = len(results) - succeeded
    summary_path.write_text(
        f"batch_tag={batch_tag}\n"
        f"total={len(results)}\n"
        f"succeeded={succeeded}\n"
        f"failed={failed}\n"
        f"concurrency={args.jobs}\n"
    )

    print()
    print("✔ Batch done")
    print(f"  Total:     {len(results)}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Failed:    {failed}")
    print(f"  Summary:   {summary_path}")
    print(f"  Manifest:  {manifest_path}")
    if failed > 0:
        print()
        print(f"  Failed cells: see {cell_logs_dir}/<run_id>.log for output")
        print(f"  Re-run failed cells: take the run_ids with exit_code != 0 from {joblog_path}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

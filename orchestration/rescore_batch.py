#!/usr/bin/env python3
"""Re-score a completed batch (or a single cell) against its preserved
workspace, using the fortree:scorer image.

Operationalises SCORING.md §Re-analysing-completed-runs: a methodology
revision (acceptance ranges, new consistency metric, etc.) can be
applied to a frozen batch without re-running the agents. The originals
(`scorer.json`, `manifest.jsonl`, `batch_report.md`, per-cell
`report.md`) are never touched — the rescored view writes to suffixed
filenames so frozen evidence and new view live side by side.

Mirrors orchestration/launch_batch.py's structure for the forward path:
  - argparse with the same conventions (--jobs, $BATCH_CONCURRENCY env
    default, validation surface)
  - ThreadPoolExecutor fan-out, one cell per thread
  - Rich Live in-flight panel + per-cell ✔/✗ scroll
  - In non-TTY contexts (CI, redirects, `tee`), Live degrades to clean
    line-oriented output automatically
  - joblog<SUFFIX>.tsv, manifest<SUFFIX>.jsonl, batch_report<SUFFIX>.md
    written to the batch dir
  - On failure, the per-cell stderr tail is dumped inline so the
    operator doesn't have to navigate the run dir

Cells are discovered from disk (every subdir of <batch_dir> with a
`run_meta.json`) rather than from a CSV worklist — the batch dir IS the
worklist for rescoring.

Usage:
  rescore_batch.py runs/batch-foo                            # whole batch
  rescore_batch.py runs/batch-foo --cell abcd1234-...        # single cell
  rescore_batch.py runs/batch-foo --suffix .phase5b-v1       # custom suffix
  rescore_batch.py runs/batch-foo --jobs 8 --skip-existing   # parallel resume
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Sibling-module imports. launch_batch.aggregate_manifest is parameterised
# by `scorer_filename` so the rescore path reuses the same row builder;
# generate_batch_report.build_report takes a manifest_path for the same
# reason. Keeping the manifest/report logic in one place means the
# rescored artefacts have byte-identical structure to the originals.
import generate_batch_report  # noqa: E402 — sibling module
import launch_batch  # noqa: E402 — sibling module

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = "fortree:scorer"

PHASE_PENDING = "PENDING"
PHASE_RESCORING = "RESCORE"
PHASE_DONE = "DONE"

PHASE_STYLES = {
    PHASE_PENDING: "dim",
    PHASE_RESCORING: "yellow",
    PHASE_DONE: "green",
}


# ---------- State model ----------


@dataclass
class CellState:
    seq: int
    run_id: str
    model: str
    rung: int
    target: str
    phase: str = PHASE_PENDING
    started_at_mono: Optional[float] = None
    started_at_iso: Optional[str] = None
    finished_at_mono: Optional[float] = None
    exit_code: Optional[int] = None
    skipped: bool = False

    @property
    def runtime_s(self) -> float:
        if self.started_at_mono is None:
            return 0.0
        end = (
            self.finished_at_mono
            if self.finished_at_mono is not None
            else time.monotonic()
        )
        return end - self.started_at_mono


@dataclass
class BatchState:
    total: int
    in_flight: dict[str, CellState] = field(default_factory=dict)
    completed: list[CellState] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def on_start(self, cell: CellState) -> None:
        with self.lock:
            cell.started_at_mono = time.monotonic()
            cell.started_at_iso = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            cell.phase = PHASE_RESCORING
            self.in_flight[cell.run_id] = cell

    def on_done(self, cell: CellState, exit_code: int) -> None:
        with self.lock:
            cell.exit_code = exit_code
            cell.phase = PHASE_DONE
            cell.finished_at_mono = time.monotonic()
            self.in_flight.pop(cell.run_id, None)
            self.completed.append(cell)


# ---------- Rendering ----------


class LiveRenderer:
    """Renderable handed to rich.live.Live. Re-evaluated each refresh tick
    (default 2 Hz). Reads BatchState under its lock.

    The forward path's launch_batch.LiveRenderer has multi-phase lifecycle
    detection (PENDING → AGENT → SCORE → REPORT → DONE) by polling
    artifact existence. Rescore is single-phase per cell, so the renderer
    is simpler — it just reflects state set by on_start / on_done.
    """

    def __init__(self, state: BatchState) -> None:
        self.state = state
        self._start = time.monotonic()

    def __rich__(self):
        return Group(self._panel(), self._progress_line())

    def _panel(self) -> Panel:
        with self.state.lock:
            cells = list(self.state.in_flight.values())
            done = len(self.state.completed)
        if not cells:
            body = Text("(no cells in flight)", style="dim")
        else:
            t = Table.grid(padding=(0, 2))
            t.add_column(style="dim")
            t.add_column()
            t.add_column()
            t.add_column(justify="right", style="dim")
            for cell in cells:
                phase_text = Text(
                    cell.phase, style=PHASE_STYLES.get(cell.phase, "white")
                )
                cell_repr = (
                    f"{cell.model} rung={cell.rung} {cell.target} "
                    f"run={cell.run_id[:8]}"
                )
                t.add_row(
                    "slot", cell_repr, phase_text, _fmt_elapsed(cell.runtime_s)
                )
            body = t
        title = f"in flight · {done}/{self.state.total} done"
        return Panel(body, title=title, title_align="left", border_style="dim")

    def _progress_line(self) -> Text:
        done = len(self.state.completed)
        total = self.state.total
        elapsed = time.monotonic() - self._start
        # Naive ETA: matches launch_batch.LiveRenderer._progress_line; same
        # caveat about future-cells-take-avg-of-done-cells.
        if done > 0 and done < total:
            est_remaining = (total - done) * (elapsed / done)
            eta = f"  ETA ~{_fmt_elapsed(est_remaining)}"
        else:
            eta = ""
        bar_width = 40
        filled = int(bar_width * done / total) if total else 0
        bar = "█" * filled + "░" * (bar_width - filled)
        return Text(f"  {bar}  {done}/{total}{eta}", style="dim")


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _print_failure_tail(console: Console, log_path: Path, lines: int = 20) -> None:
    """Dump the last `lines` of log_path inline under the ✗ marker. Same
    treatment as launch_batch._print_failure_tail — saves the operator
    from `cat`ing the per-cell stderr to see what blew up."""
    if not log_path.exists():
        return
    try:
        text = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return
    if not text:
        return
    tail = text[-lines:]
    rel = (
        log_path.relative_to(REPO_ROOT)
        if log_path.is_relative_to(REPO_ROOT)
        else log_path
    )
    console.print(f"   [dim]── tail {rel} (last {len(tail)} lines) ──[/]")
    for line in tail:
        # Escape `[...]` in log content so Rich markup tags don't swallow
        # log prefixes like "[INFO]".
        console.print(f"   [dim]│[/] [dim]{rich_escape(line)}[/]")
    console.print("   [dim]──[/]")


# ---------- Discovery ----------


def discover_cells(
    batch_dir: Path, only_run_id: Optional[str] = None
) -> list[CellState]:
    """Discover cells (subdirs with run_meta.json) under batch_dir.

    Returns CellState in stable seq order (model, rung, target, run_id —
    matches launch_batch.py's worklist ordering). If only_run_id is set,
    restricts to that one cell; raises SystemExit if not found.
    """
    found: list[dict] = []
    for d in sorted(p for p in batch_dir.iterdir() if p.is_dir()):
        meta_path = d / "run_meta.json"
        if not meta_path.is_file():
            continue
        if only_run_id and d.name != only_run_id:
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        found.append({
            "run_id": meta["run_id"],
            "model": meta["model"],
            "rung": meta["rung"],
            "target": meta["target"],
        })
    if only_run_id and not found:
        raise SystemExit(f"--cell {only_run_id} not found under {batch_dir}")
    found.sort(key=lambda x: (x["model"], x["rung"], x["target"], x["run_id"]))
    return [
        CellState(
            seq=i,
            run_id=f["run_id"],
            model=f["model"],
            rung=f["rung"],
            target=f["target"],
        )
        for i, f in enumerate(found, start=1)
    ]


# ---------- Worker ----------


def rescore_one(
    state: BatchState,
    console: Console,
    rescore_cell: Path,
    batch_dir: Path,
    cell: CellState,
    suffix: str,
    image: str,
    skip_existing: bool,
) -> CellState:
    """Run one cell's rescore via rescore_cell.sh. Mirrors
    launch_batch.run_one_cell — never raises; the executor sees a clean
    future so other cells keep running on failure."""
    run_dir = batch_dir / cell.run_id
    out_name = f"scorer{suffix}.json"
    err_name = f"scorer{suffix}.stderr"
    output = run_dir / out_name

    if skip_existing and output.is_file() and output.stat().st_size > 0:
        # Synthesise a clean lifecycle so the renderer's done-count and
        # the joblog stay coherent.
        state.on_start(cell)
        cell.skipped = True
        state.on_done(cell, 0)
        console.print(
            f"[dim]⊝[/] [{cell.seq}/{state.total}] {cell.model} "
            f"rung={cell.rung} target={cell.target} "
            f"run={cell.run_id[:8]} (skipped — {out_name} exists)"
        )
        return cell

    state.on_start(cell)
    env = {
        **os.environ,
        "RUN_DIR": str(run_dir),
        "TARGET": cell.target,
        "SCORER_FILENAME": out_name,
        "SCORER_STDERR": err_name,
        "IMAGE": image,
    }
    try:
        result = subprocess.run(
            [str(rescore_cell)],
            env=env,
            capture_output=True,
            text=True,
        )
        rc = result.returncode
    except Exception as e:  # noqa: BLE001 — never raise to the executor
        rc = -1
        (run_dir / err_name).write_text(
            f"rescore_batch: failed to invoke {rescore_cell}: {e}\n"
        )
    state.on_done(cell, rc)

    mark = "[green]✔[/]" if rc == 0 else "[red]✗[/]"
    console.print(
        f"{mark} [{cell.seq}/{state.total}] {cell.model} rung={cell.rung} "
        f"target={cell.target} run={cell.run_id[:8]} "
        f"exit={rc} t={cell.runtime_s:.1f}s"
    )
    if rc != 0:
        _print_failure_tail(console, run_dir / err_name, lines=20)
    return cell


# ---------- Output writers ----------


def write_joblog(joblog_path: Path, cells: list[CellState]) -> None:
    """seq-ordered TSV — same shape as launch_batch.write_joblog so the
    rescore joblog is interchangeable with the forward-path one for
    downstream consumers (post-hoc analysis scripts, etc.)."""
    with joblog_path.open("w") as jl:
        jl.write(
            "seq\tmodel\trung\ttarget\trun_id\tstarted_at\truntime_s\texit_code\n"
        )
        for c in cells:
            jl.write(
                f"{c.seq}\t{c.model}\t{c.rung}\t{c.target}\t{c.run_id}\t"
                f"{c.started_at_iso or ''}\t{c.runtime_s:.2f}\t"
                f"{c.exit_code if c.exit_code is not None else ''}\n"
            )


def print_final_table(console: Console, cells: list[CellState]) -> None:
    t = Table(title="Rescore summary", title_style="bold", show_lines=False)
    t.add_column("seq", justify="right", style="dim")
    t.add_column("model")
    t.add_column("rung", justify="right")
    t.add_column("target")
    t.add_column("run")
    t.add_column("exit", justify="right")
    t.add_column("runtime", justify="right")
    for c in cells:
        if c.skipped:
            exit_str = "skip"
            exit_style = "dim"
        else:
            exit_str = str(c.exit_code) if c.exit_code is not None else "?"
            exit_style = "green" if c.exit_code == 0 else "red"
        t.add_row(
            str(c.seq),
            c.model,
            str(c.rung),
            c.target,
            c.run_id[:8],
            Text(exit_str, style=exit_style),
            f"{c.runtime_s:.1f}s",
        )
    console.print(t)


# ---------- CLI ----------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rescore_batch.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "batch_dir",
        type=Path,
        help="Path to runs/<batch-tag>/ to rescore",
    )
    parser.add_argument(
        "--cell",
        help="Restrict to a single RUN_ID inside the batch dir",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=int(os.environ.get("BATCH_CONCURRENCY", 4)),
        help="Concurrent slots (default: $BATCH_CONCURRENCY or 4)",
    )
    parser.add_argument(
        "--suffix",
        default=".rescored",
        help=(
            "Filename infix for outputs: scorer<SUFFIX>.json per cell, "
            "manifest<SUFFIX>.jsonl + batch_report<SUFFIX>.md + "
            "joblog<SUFFIX>.tsv per batch. Default: .rescored. "
            "Set --suffix='' to overwrite originals (NOT recommended for "
            "the headline batch — frozen runs are evidence)."
        ),
    )
    parser.add_argument(
        "--image",
        default=DEFAULT_IMAGE,
        help=f"Scorer image (default: {DEFAULT_IMAGE})",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip cells where scorer<SUFFIX>.json already exists and is "
            "non-empty. Useful for resuming after a host crash."
        ),
    )
    parser.add_argument(
        "--rescore-cell",
        type=Path,
        default=REPO_ROOT / "orchestration" / "rescore_cell.sh",
        help="Per-cell rescore driver path. Override to swap in a stub for tests.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not args.batch_dir.is_dir():
        sys.exit(f"batch_dir not a directory: {args.batch_dir}")
    if args.jobs < 1:
        sys.exit("--jobs must be >= 1")
    if not args.rescore_cell.exists():
        sys.exit(f"rescore-cell driver not found: {args.rescore_cell}")
    # Sanity-check image + data dir up front. Failing fast saves a lot of
    # cycles vs discovering it per-cell after the executor spins up.
    if subprocess.run(
        ["docker", "image", "inspect", args.image],
        capture_output=True,
    ).returncode != 0:
        sys.exit(
            f"docker image {args.image!r} not found; build with "
            f"`docker build --target scorer -t {args.image} .`"
        )
    if not (REPO_ROOT / "data").is_dir():
        sys.exit(
            f"data dir {REPO_ROOT / 'data'} not found; "
            f"the scorer needs the synthetic-climate netCDFs mounted ro"
        )


# ---------- Main ----------


def main() -> int:
    args = parse_args()
    validate_args(args)
    # docker bind mounts reject relative paths ("invalid characters for a
    # local volume name") — resolve once so every per-cell invocation
    # passes an absolute path to rescore_cell.sh.
    args.batch_dir = args.batch_dir.resolve()

    cells = discover_cells(args.batch_dir, only_run_id=args.cell)
    if not cells:
        sys.exit(
            f"no cells with run_meta.json found under {args.batch_dir} "
            f"— is this an empty or stale batch dir?"
        )

    suffix = args.suffix
    console = Console(
        width=None if sys.stdout.isatty() else 200,
        soft_wrap=not sys.stdout.isatty(),
    )
    mode_note = " (single-cell)" if args.cell else ""
    console.print(f"[bold]▶ Rescore: {args.batch_dir.name}[/]{mode_note}")
    console.print(f"  Cells:        {len(cells)}")
    console.print(f"  Concurrency:  {args.jobs}")
    console.print(f"  Suffix:       '{suffix}'")
    console.print(f"  Image:        {args.image}")
    if args.skip_existing:
        console.print(f"  Skip:         existing scorer{suffix}.json files")
    console.print()
    console.print(f"[bold]▶ Rescoring {len(cells)} cell(s)[/]")

    state = BatchState(total=len(cells))
    renderer = LiveRenderer(state)
    # Live(...) becomes a no-op in non-TTY contexts, so the same code path
    # produces clean line output under CI / `tee` / redirects.
    with Live(renderer, console=console, refresh_per_second=2, transient=True):
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as exe:
            futures = [
                exe.submit(
                    rescore_one,
                    state,
                    console,
                    args.rescore_cell,
                    args.batch_dir,
                    cell,
                    suffix,
                    args.image,
                    args.skip_existing,
                )
                for cell in cells
            ]
            concurrent.futures.wait(futures)

    cells_in_seq = sorted(cells, key=lambda c: c.seq)
    succeeded = sum(1 for c in cells_in_seq if c.exit_code == 0)
    failed = sum(1 for c in cells_in_seq if c.exit_code != 0)

    console.print()
    print_final_table(console, cells_in_seq)
    console.print()
    console.print(f"  Total:     {len(cells_in_seq)}")
    console.print(f"  Succeeded: [green]{succeeded}[/]")
    console.print(
        f"  Failed:    [red]{failed}[/]" if failed else f"  Failed:    {failed}"
    )

    # Single-cell mode: stop here. Manifest + batch_report aggregation
    # only makes sense across the whole batch, and overwriting the
    # rescored manifest based on one cell would be misleading.
    if args.cell:
        cell_path = args.batch_dir / cells_in_seq[0].run_id / f"scorer{suffix}.json"
        console.print(f"  Inspect:   {cell_path}")
        return 1 if failed else 0

    # Batch-level rollups: joblog, manifest, batch_report.
    joblog_path = args.batch_dir / f"joblog{suffix}.tsv"
    manifest_path = args.batch_dir / f"manifest{suffix}.jsonl"
    report_path = args.batch_dir / f"batch_report{suffix}.md"

    write_joblog(joblog_path, cells_in_seq)
    console.print(f"  Joblog:    {joblog_path}")

    worklist = [(c.model, c.rung, c.target, c.run_id) for c in cells_in_seq]
    launch_batch.aggregate_manifest(
        manifest_path,
        worklist,
        args.batch_dir,
        scorer_filename=f"scorer{suffix}.json",
    )
    console.print(f"  Manifest:  {manifest_path}")

    try:
        md = generate_batch_report.build_report(
            args.batch_dir, manifest_path=manifest_path
        )
        report_path.write_text(md, encoding="utf-8")
        console.print(f"  Report:    {report_path}")
    except Exception as exc:
        # Same posture as launch_batch.py — report-render failure is
        # non-fatal; the manifest is the source of truth.
        console.print(f"  [yellow]batch report generation failed:[/] {exc}")

    console.print()
    console.print(
        "  Originals untouched: scorer.json, manifest.jsonl, batch_report.md, "
        "joblog.tsv, per-cell report.md."
    )
    if failed:
        console.print(
            f"  Failed cell stderrs: {args.batch_dir}/<run_id>/scorer{suffix}.stderr"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

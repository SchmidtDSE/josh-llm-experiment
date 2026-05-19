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
  - ThreadPoolExecutor pools N workers; each worker blocks on a
    subprocess.run() of launch_cell.sh. We're I/O-bound on the inferior
    containers, so threads (no GIL pressure) are simpler than processes
    and give us native as_completed semantics.
  - Each cell (launch_cell.sh → launch_run.sh) owns its own bridge
    network and dnsmasq sidecar named by RUN_ID, with idempotent
    teardown traps. Concurrent runs never share mutable docker state;
    the only shared mount is data/ as read-only.
  - Pre-sweep cleanup at the top removes any fortree-run-* networks or
    dnsmasq-* containers leaked by a prior SIGKILL'd batch.

UX:
  - Live in-flight panel (which cells are running + their phase) plus a
    per-cell ✔/✗ scroll above it. Phase is derived by polling each run
    dir for which artifacts exist; no instrumentation needed in
    launch_cell.sh.
  - On failure, the last ~20 lines of cell-logs/<run_id>.log are dumped
    inline so the operator doesn't have to navigate to find the cause.
  - In non-TTY contexts (CI, redirects, `tee`), the Live area becomes a
    no-op automatically and you get clean line-oriented output.

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
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID_RUNGS = {1, 5}
VALID_TARGETS = {"josh", "mesa"}
CELLS_HEADER = ["model", "rung", "target", "replicates"]

# Cell lifecycle phases, derived by polling artifacts in runs/<run_id>/.
# Order matters for compute_phase: later checks win.
PHASE_PENDING = "PENDING"
PHASE_AGENT = "AGENT"
PHASE_SCORE = "SCORE"
PHASE_REPORT = "REPORT"
PHASE_DONE = "DONE"

PHASE_STYLES = {
    PHASE_PENDING: "dim",
    PHASE_AGENT: "cyan",
    PHASE_SCORE: "yellow",
    PHASE_REPORT: "magenta",
    PHASE_DONE: "green",
}


# ---------- Parsing & validation ----------


def load_valid_models() -> set[str]:
    return set(
        yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text()).keys()
    )


def parse_cells_csv(
    path: Path, valid_models: set[str]
) -> list[tuple[str, int, str, int]]:
    """Return list of (model, rung, target, replicates), validating each row.

    Skips blank lines and `#`-prefixed comment lines so the matrix file can
    be human-edited. Errors include the source line number for fast
    triage.
    """
    raw = path.read_text().splitlines(keepends=True)
    keep: list[tuple[int, str]] = []
    for i, line in enumerate(raw, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        keep.append((i, line))
    if not keep:
        sys.exit(f"{path}: empty (or only blanks/comments)")

    header_lineno, header_line = keep[0]
    header = next(csv.reader(io.StringIO(header_line)))
    if header != CELLS_HEADER:
        sys.exit(
            f"{path}:{header_lineno}: expected header "
            f"{','.join(CELLS_HEADER)!r}, got {','.join(header)!r}"
        )

    rows: list[tuple[str, int, str, int]] = []
    for lineno, line in keep[1:]:
        cells = next(csv.reader(io.StringIO(line)))
        if len(cells) != 4:
            sys.exit(
                f"{path}:{lineno}: expected 4 columns, got {len(cells)}: {line.rstrip()}"
            )
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
            sys.exit(
                f"{path}:{lineno}: RUNG must be one of {sorted(VALID_RUNGS)}, got {rung}"
            )
        if target not in VALID_TARGETS:
            sys.exit(
                f"{path}:{lineno}: TARGET must be one of {sorted(VALID_TARGETS)}, got {target!r}"
            )
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

    nets = _ids(
        [
            "docker",
            "network",
            "ls",
            "--filter",
            "name=fortree-run-",
            "--filter",
            "driver=bridge",
            "-q",
        ]
    )
    if nets:
        subprocess.run(["docker", "network", "rm", *nets], capture_output=True)
    containers = _ids(["docker", "ps", "-aq", "--filter", "name=dnsmasq-"])
    if containers:
        subprocess.run(["docker", "rm", "-f", *containers], capture_output=True)


# ---------- State model ----------


@dataclass
class CellState:
    seq: int
    model: str
    rung: int
    target: str
    run_id: str
    started_at_mono: Optional[float] = None
    started_at_iso: Optional[str] = None
    finished_at_mono: Optional[float] = None
    exit_code: Optional[int] = None

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
            self.in_flight[cell.run_id] = cell

    def on_done(self, cell: CellState, exit_code: int) -> None:
        with self.lock:
            cell.exit_code = exit_code
            cell.finished_at_mono = time.monotonic()
            self.in_flight.pop(cell.run_id, None)
            self.completed.append(cell)


# ---------- Phase detection ----------


def compute_phase(run_dir: Path) -> str:
    """Derive the lifecycle phase of a cell from artifacts in its run dir.

    Each artifact in the chain is written by a known step:
      run_meta.json       → launch_run.sh starts → AGENT begins
      run_meta.final.json → launch_run.sh ends   → AGENT done, SCORE begins
      scorer.json         → scorer container ends → REPORT begins
      report.md           → report container ends → cell mechanically done
      run_meta.cell.json  → launch_cell.sh ends   → fully DONE

    File-existence checks can transiently race against the writer (e.g.
    an empty scorer.json from a redirected `>` that hasn't flushed yet),
    so we require nonzero size on the "data" artifacts (scorer.json,
    report.md) before believing the phase moved on.
    """
    if not run_dir.exists():
        return PHASE_PENDING
    if (run_dir / "run_meta.cell.json").exists():
        return PHASE_DONE
    report = run_dir / "report.md"
    if report.exists() and report.stat().st_size > 0:
        return PHASE_REPORT
    scorer = run_dir / "scorer.json"
    if scorer.exists() and scorer.stat().st_size > 0:
        # scorer.json present but report.md missing → mid-REPORT
        return PHASE_REPORT
    if (run_dir / "run_meta.final.json").exists():
        return PHASE_SCORE
    if (run_dir / "run_meta.json").exists():
        return PHASE_AGENT
    return PHASE_PENDING


# ---------- Rendering ----------


class LiveRenderer:
    """Renderable handed to rich.live.Live. Re-evaluated each refresh tick
    (default 2 Hz). Reads BatchState under its lock.

    Layout:
      ┌─ in flight · 2/4 done ─────────────────────┐
      │ slot │ model rung target run=abcd1234 │ AGENT │ 6m12s │
      │ slot │ ...                                            │
      ├─────────────────────────────────────────────┤
      │ ████████████░░░░░░░░░░░░  2/4  ETA ~6m       │
      └─────────────────────────────────────────────┘
    """

    def __init__(self, state: BatchState, batch_dir: Path) -> None:
        self.state = state
        self.batch_dir = batch_dir
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
                phase = compute_phase(self.batch_dir / cell.run_id)
                phase_text = Text(phase, style=PHASE_STYLES.get(phase, "white"))
                cell_repr = (
                    f"{cell.model} rung={cell.rung} {cell.target} run={cell.run_id[:8]}"
                )
                t.add_row("slot", cell_repr, phase_text, _fmt_elapsed(cell.runtime_s))
            body = t

        title = f"in flight · {done}/{self.state.total} done"
        return Panel(body, title=title, title_align="left", border_style="dim")

    def _progress_line(self) -> Text:
        done = len(self.state.completed)
        total = self.state.total
        elapsed = time.monotonic() - self._start
        # Naive ETA: if N done in T seconds, remaining ~= (total-N) * (T/N).
        # Caveat: assumes future cells take avg time of done cells — wrong
        # if early cells are short replicates of a fast model. Good enough
        # for the operator.
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
    """Dump the last `lines` of log_path inline under the ✗ marker. Saves
    the operator from `cat`ing the cell log to see what blew up."""
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
        # Escape `[...]` in log content so prefixes like "[INFO]" aren't
        # silently swallowed as Rich markup tags.
        console.print(f"   [dim]│[/] [dim]{rich_escape(line)}[/]")
    console.print("   [dim]──[/]")


# ---------- Worker ----------


def run_one_cell(
    state: BatchState,
    console: Console,
    launch_cell: Path,
    cell: CellState,
    log_path: Path,
    batch_dir: Path,
) -> CellState:
    """Run one cell. Updates state, prints completion line, dumps failure
    tail on non-zero exit. Never raises; the executor sees a clean future
    so other cells keep running."""
    state.on_start(cell)
    env = {
        **os.environ,
        "MODEL": cell.model,
        "RUNG": str(cell.rung),
        "TARGET": cell.target,
        "RUN_ID": cell.run_id,
        "BATCH_DIR": str(batch_dir),
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
    state.on_done(cell, rc)

    mark = "[green]✔[/]" if rc == 0 else "[red]✗[/]"
    console.print(
        f"{mark} [{cell.seq}/{state.total}] {cell.model} rung={cell.rung} "
        f"target={cell.target} run={cell.run_id[:8]} "
        f"exit={rc} t={cell.runtime_s:.1f}s"
    )
    if rc != 0:
        _print_failure_tail(console, log_path, lines=20)
    return cell


# ---------- Output writers ----------


def write_joblog(joblog_path: Path, cells_in_seq_order: list[CellState]) -> None:
    with joblog_path.open("w") as jl:
        jl.write("seq\tmodel\trung\ttarget\trun_id\tstarted_at\truntime_s\texit_code\n")
        for c in cells_in_seq_order:
            jl.write(
                f"{c.seq}\t{c.model}\t{c.rung}\t{c.target}\t{c.run_id}\t"
                f"{c.started_at_iso or ''}\t{c.runtime_s:.2f}\t"
                f"{c.exit_code if c.exit_code is not None else ''}\n"
            )


def _collect_step_status(run_dir: Path) -> Optional[dict]:
    """Walk agent_artifacts/steps/step_*/step_meta.json into a compact
    summary the batch report can render at a glance. Returns None if the
    run pre-dates the multi-invocation flow (no steps/ dir)."""
    steps_root = run_dir / "agent_artifacts" / "steps"
    if not steps_root.is_dir():
        return None
    per_step = []
    for step_dir in sorted(steps_root.glob("step_*")):
        meta_path = step_dir / "step_meta.json"
        if not (meta_path.exists() and meta_path.stat().st_size):
            continue
        try:
            sm = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        per_step.append({
            "n": sm.get("step_n"),
            "name": sm.get("step_name"),
            "exit_code": sm.get("exit_code"),
            "started_at": sm.get("started_at"),
            "ended_at": sm.get("ended_at"),
        })
    if not per_step:
        return None
    completed = sum(1 for s in per_step if s.get("exit_code") == 0)
    return {
        "step_count": len(per_step),
        "completed_count": completed,
        "per_step": per_step,
    }


def _count_plan_todos(run_dir: Path) -> Optional[dict]:
    """Count `[ ]` / `[x]` checkboxes in workspace/PLAN.md. Returns None if
    PLAN.md doesn't exist (pre-multi-invocation runs)."""
    plan = run_dir / "workspace" / "PLAN.md"
    if not plan.is_file():
        return None
    try:
        text = plan.read_text(errors="replace")
    except OSError:
        return None
    checked = 0
    unchecked = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            checked += 1
        elif stripped.startswith("- [ ]"):
            unchecked += 1
    total = checked + unchecked
    if total == 0:
        return None
    return {"checked": checked, "total": total}


def emit_manifest_line(run_id: str, batch_dir: Path) -> Optional[dict]:
    """Build one manifest row for run_id. Returns None if run_meta.json is
    missing (launch_run.sh failed before workspace setup — joblog records
    that already)."""
    run_dir = batch_dir / run_id
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return None
    cell_path = run_dir / "run_meta.cell.json"
    scorer_path = run_dir / "scorer.json"
    time_path = run_dir / "time_breakdown.json"
    cell = (
        json.loads(cell_path.read_text())
        if cell_path.exists() and cell_path.stat().st_size
        else None
    )
    scorer = (
        json.loads(scorer_path.read_text())
        if scorer_path.exists() and scorer_path.stat().st_size
        else None
    )
    time_breakdown = (
        json.loads(time_path.read_text())
        if time_path.exists() and time_path.stat().st_size
        else None
    )
    return {
        "run_id": meta.get("run_id"),
        "model": meta.get("model"),
        "rung": meta.get("rung"),
        "target": meta.get("target"),
        "cell": cell,
        "scorer": scorer,
        "time_breakdown": time_breakdown,
        "run_meta": meta,
        "steps": _collect_step_status(run_dir),
        "plan_todos": _count_plan_todos(run_dir),
    }


def aggregate_manifest(
    manifest_path: Path,
    worklist: list[tuple[str, int, str, str]],
    batch_dir: Path,
) -> None:
    with manifest_path.open("w") as mf:
        for _, _, _, run_id in worklist:
            line = emit_manifest_line(run_id, batch_dir)
            if line is not None:
                mf.write(json.dumps(line) + "\n")


def write_summary(
    summary_path: Path,
    batch_tag: str,
    total: int,
    succeeded: int,
    failed: int,
    concurrency: int,
) -> None:
    summary_path.write_text(
        f"batch_tag={batch_tag}\n"
        f"total={total}\n"
        f"succeeded={succeeded}\n"
        f"failed={failed}\n"
        f"concurrency={concurrency}\n"
    )


def print_final_table(console: Console, cells_in_seq_order: list[CellState]) -> None:
    """Per-cell wrap-up table. In a TTY it renders with box-drawing; in a
    pipe it degrades to a column-aligned ASCII table automatically."""
    t = Table(title="Batch summary", title_style="bold", show_lines=False)
    t.add_column("seq", justify="right", style="dim")
    t.add_column("model")
    t.add_column("rung", justify="right")
    t.add_column("target")
    t.add_column("run")
    t.add_column("exit", justify="right")
    t.add_column("runtime", justify="right")
    for c in cells_in_seq_order:
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
        prog="launch_batch.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", help="Short name from config/models.yaml")
    parser.add_argument("--rung", type=int, choices=sorted(VALID_RUNGS))
    parser.add_argument("--target", choices=sorted(VALID_TARGETS))
    parser.add_argument("--runs", type=int, help="Replicates of the single cell")
    parser.add_argument(
        "--cells", type=Path, help="Matrix CSV with header model,rung,target,replicates"
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=int(os.environ.get("BATCH_CONCURRENCY", 4)),
        help="Concurrent slots (default: $BATCH_CONCURRENCY or 4)",
    )
    parser.add_argument(
        "--batch-tag", help="Batch identifier (default: batch-<UTC ISO timestamp>)"
    )
    parser.add_argument(
        "--launch-cell",
        type=Path,
        default=REPO_ROOT / "orchestration" / "launch_cell.sh",
        help="Per-cell driver path. Override to swap in a stub for tests.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace, valid_models: set[str]) -> None:
    has_cells = args.cells is not None
    has_single_any = any(
        v is not None for v in (args.model, args.rung, args.target, args.runs)
    )
    if has_cells and has_single_any:
        sys.exit("--cells is mutually exclusive with --model/--rung/--target/--runs")
    if not has_cells:
        missing = [
            n
            for n, v in [
                ("--model", args.model),
                ("--rung", args.rung),
                ("--target", args.target),
                ("--runs", args.runs),
            ]
            if v is None
        ]
        if missing:
            sys.exit(
                f"Either --cells FILE or all of --model/--rung/--target/--runs required "
                f"(missing: {', '.join(missing)})"
            )
        if args.runs < 1:
            sys.exit("--runs must be >= 1")
        if args.model not in valid_models:
            sys.exit(
                f"Unknown MODEL {args.model!r}. Valid: {', '.join(sorted(valid_models))}"
            )
    else:
        if not args.cells.exists():
            sys.exit(f"--cells file not found: {args.cells}")
    if args.jobs < 1:
        sys.exit("--jobs must be >= 1")
    if not args.launch_cell.exists():
        sys.exit(f"launch-cell driver not found: {args.launch_cell}")


def build_worklist(
    args: argparse.Namespace, valid_models: set[str]
) -> list[tuple[str, int, str, str]]:
    worklist: list[tuple[str, int, str, str]] = []
    if args.cells is not None:
        for model, rung, target, replicates in parse_cells_csv(
            args.cells, valid_models
        ):
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


# ---------- Main ----------


def main() -> int:
    args = parse_args()
    valid_models = load_valid_models()
    validate_args(args, valid_models)

    worklist = build_worklist(args, valid_models)
    batch_tag, batch_dir = resolve_batch_dir(args.batch_tag)
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

    # In non-TTY contexts (CI, redirects, `tee`), Rich defaults to width=80
    # which mangles long file paths into ugly multi-line wraps. Widen to
    # 200 cols when not a terminal; in TTY mode Rich auto-sizes correctly.
    console = Console(
        width=None if sys.stdout.isatty() else 200,
        soft_wrap=not sys.stdout.isatty(),
    )
    console.print(f"[bold]▶ Batch: {batch_tag}[/]")
    console.print(f"  Worklist:    {worklist_path}  ({len(worklist)} cells)")
    console.print(f"  Joblog:      {joblog_path}")
    console.print(f"  Cell logs:   {cell_logs_dir}/<run_id>.log")
    console.print(f"  Concurrency: {args.jobs}")
    console.print()
    console.print(
        "[bold]▶ Pre-sweep:[/] cleaning orphan fortree networks / dnsmasq containers"
    )
    presweep_cleanup()
    console.print(f"[bold]▶ Running {len(worklist)} cells[/]")

    state = BatchState(total=len(worklist))
    cells = [
        CellState(seq=i, model=m, rung=r, target=t, run_id=rid)
        for i, (m, r, t, rid) in enumerate(worklist, start=1)
    ]
    cells_by_seq = {c.seq: c for c in cells}

    # Live(...) becomes a no-op when console isn't a TTY, so this same
    # code path emits clean line output under CI / `tee` / redirects.
    renderer = LiveRenderer(state, batch_dir)
    with Live(renderer, console=console, refresh_per_second=2, transient=True):
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as exe:
            futures = []
            for cell in cells:
                log_path = cell_logs_dir / f"{cell.run_id}.log"
                futures.append(
                    exe.submit(
                        run_one_cell,
                        state,
                        console,
                        args.launch_cell,
                        cell,
                        log_path,
                        batch_dir,
                    )
                )
            concurrent.futures.wait(futures)

    # Persist artifacts in seq (= worklist) order, not completion order,
    # so the joblog and manifest are reproducible across reruns.
    cells_in_seq = [cells_by_seq[i] for i in sorted(cells_by_seq)]
    write_joblog(joblog_path, cells_in_seq)
    aggregate_manifest(manifest_path, worklist, batch_dir)

    succeeded = sum(1 for c in cells_in_seq if c.exit_code == 0)
    failed = len(cells_in_seq) - succeeded
    write_summary(
        summary_path, batch_tag, len(cells_in_seq), succeeded, failed, args.jobs
    )

    batch_report_path = batch_dir / "batch_report.md"
    try:
        import generate_batch_report
        md = generate_batch_report.build_report(batch_dir)
        batch_report_path.write_text(md, encoding="utf-8")
    except Exception as exc:
        console.print(f"  [yellow]batch_report.md generation failed:[/] {exc}")
        batch_report_path = None

    console.print()
    print_final_table(console, cells_in_seq)
    console.print()
    console.print(f"  Total:     {len(cells_in_seq)}")
    console.print(f"  Succeeded: [green]{succeeded}[/]")
    console.print(
        f"  Failed:    [red]{failed}[/]" if failed else f"  Failed:    {failed}"
    )
    console.print(f"  Summary:   {summary_path}")
    console.print(f"  Manifest:  {manifest_path}")
    if batch_report_path is not None:
        console.print(f"  Report:    {batch_report_path}")
    if failed > 0:
        console.print(f"  Failed cell logs: {cell_logs_dir}/<run_id>.log")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

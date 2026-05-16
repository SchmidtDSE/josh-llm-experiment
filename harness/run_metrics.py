"""Scorer orchestration: invoke run.sh, validate output, compute metrics.

Always exits 0 unless the harness itself crashes. Run-failure and
range-failure are signaled via JSON fields (`did_run`, `*_in_range`),
which the phase-3+ orchestrator reads instead of the exit code.

JSON record is emitted twice:
- stdout (single line) — what the orchestrator captures.
- /sandbox/results/scorer.json (pretty-printed) — durable artifact for
  post-hoc inspection.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import traceback
from pathlib import Path

import entropy
import loc
import runner
from validators import acceptance, output_schema

SCHEMA_VERSION = "phase2-v1"
DEFAULT_TIMEOUT_S = 600
DEFAULT_ACCEPTANCE_RANGES = Path("/opt/harness/acceptance_ranges.json")


def _ordered_record(
    *,
    target: str,
    target_year: int | None,
    runner_out: dict,
    schema_out: dict,
    accept_out: dict,
    loc_out: dict,
    entropy_out: dict,
    did_run: bool,
    harness_errors: list[str],
) -> dict:
    """Build the JSON record in a stable order for manifest readability."""

    def _finite_or_none(x):
        if x is None:
            return None
        try:
            return x if math.isfinite(float(x)) else None
        except (TypeError, ValueError):
            return None

    return {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "target_year": target_year,
        "did_run": did_run,
        "exit_code": runner_out.get("exit_code"),
        "wall_time_seconds": runner_out.get("wall_time_seconds"),
        "timed_out": runner_out.get("timed_out", False),
        "stdout_tail": runner_out.get("stdout_tail", ""),
        "stderr_tail": runner_out.get("stderr_tail", ""),
        "csv_exists": schema_out.get("csv_exists", False),
        "csv_row_count": schema_out.get("csv_row_count"),
        "csv_schema_ok": schema_out.get("csv_schema_ok", False),
        "csv_schema_errors": schema_out.get("csv_schema_errors", []),
        "height_year10_mean": _finite_or_none(accept_out.get("height_year10_mean")),
        "occupancy_year10_mean": _finite_or_none(accept_out.get("occupancy_year10_mean")),
        "height_in_range": accept_out.get("height_in_range", False),
        "occupancy_in_range": accept_out.get("occupancy_in_range", False),
        "acceptance_ranges_used": accept_out.get("acceptance_ranges_used", {}),
        "relevant_loc": loc_out.get("relevant_loc", 0),
        "loc_files_counted": loc_out.get("loc_files_counted", []),
        "entropy_bits": entropy_out.get("entropy_bits", 0.0),
        "harness_errors": harness_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_metrics")
    parser.add_argument("--target", required=True, choices=["josh", "mesa"])
    parser.add_argument("--workspace", type=Path, default=Path("/sandbox"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument(
        "--acceptance-ranges",
        type=Path,
        default=DEFAULT_ACCEPTANCE_RANGES,
    )
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    harness_errors: list[str] = []

    runner_out: dict = {}
    schema_out: dict = {}
    accept_out: dict = {}
    loc_out: dict = {}
    entropy_out: dict = {}
    target_year: int | None = None

    try:
        runner_out = runner.run(workspace, args.timeout)
    except Exception:
        harness_errors.append(f"runner.run:\n{traceback.format_exc()}")

    try:
        schema_out = output_schema.check(workspace)
    except Exception:
        harness_errors.append(f"output_schema.check:\n{traceback.format_exc()}")

    if schema_out.get("csv_schema_ok"):
        try:
            accept_out = acceptance.check(workspace, args.acceptance_ranges)
            target_year = accept_out.get("acceptance_ranges_used", {}).get("target_year")
        except Exception:
            harness_errors.append(f"acceptance.check:\n{traceback.format_exc()}")
    else:
        try:
            with open(args.acceptance_ranges) as f:
                ranges = json.load(f)
            target_year = int(ranges.get("target_year"))
            accept_out = {
                "height_year10_mean": None,
                "occupancy_year10_mean": None,
                "height_in_range": False,
                "occupancy_in_range": False,
                "acceptance_ranges_used": ranges,
            }
        except Exception:
            harness_errors.append(
                f"acceptance ranges read:\n{traceback.format_exc()}"
            )

    try:
        loc_out = loc.count(workspace, args.target)
    except Exception:
        harness_errors.append(f"loc.count:\n{traceback.format_exc()}")

    try:
        entropy_out = entropy.compute(workspace, args.target)
    except Exception:
        harness_errors.append(f"entropy.compute:\n{traceback.format_exc()}")

    did_run = (
        runner_out.get("exit_code") == 0
        and schema_out.get("csv_exists", False)
        and schema_out.get("csv_schema_ok", False)
    )

    record = _ordered_record(
        target=args.target,
        target_year=target_year,
        runner_out=runner_out,
        schema_out=schema_out,
        accept_out=accept_out,
        loc_out=loc_out,
        entropy_out=entropy_out,
        did_run=did_run,
        harness_errors=harness_errors,
    )

    print(json.dumps(record))

    results_dir = workspace / "results"
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "scorer.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

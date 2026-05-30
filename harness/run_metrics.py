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

import conformance
import conformance_fuzzy
import entropy
import loc
import runner
from validators import acceptance, output_schema

SCHEMA_VERSION = "phase6-v2"
DEFAULT_TIMEOUT_S = 3600
DEFAULT_ACCEPTANCE_RANGES = Path("/opt/harness/acceptance_ranges.json")


def _ordered_record(
    *,
    target: str,
    target_year: int | None,
    runner_out: dict,
    conformance_out: dict,
    conformance_fuzzy_out: dict,
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
        "script_was_executable": runner_out.get("script_was_executable"),
        "stdout_tail": runner_out.get("stdout_tail", ""),
        "stderr_tail": runner_out.get("stderr_tail", ""),
        "target_conformance": conformance_out.get("target_conformance", False),
        "conformance": conformance_out,
        "target_conformance_fuzzy": conformance_fuzzy_out.get("target_conformance_fuzzy"),
        "target_conformance_fuzzy_reason": conformance_fuzzy_out.get(
            "target_conformance_fuzzy_reason"
        ),
        "csv_exists": schema_out.get("csv_exists", False),
        "csv_row_count": schema_out.get("csv_row_count"),
        "csv_rows_dropped_nan": schema_out.get("csv_rows_dropped_nan"),
        "csv_schema_ok": schema_out.get("csv_schema_ok", False),
        "csv_schema_errors": schema_out.get("csv_schema_errors", []),
        "csv_source_layout": schema_out.get("csv_source_layout"),
        "csv_source_files": schema_out.get("csv_source_files"),
        "height_year100_mean": _finite_or_none(accept_out.get("height_year100_mean")),
        "occupancy_year100_mean": _finite_or_none(accept_out.get("occupancy_year100_mean")),
        "height_in_range": accept_out.get("height_in_range", False),
        "occupancy_in_range": accept_out.get("occupancy_in_range", False),
        "regression_fit": accept_out.get("regression_fit", {}),
        "regression_fit_ok": accept_out.get("regression_fit_ok", False),
        "regression_fit_reasons": accept_out.get("regression_fit_reasons", []),
        "acceptance_ranges_used": accept_out.get("acceptance_ranges_used", {}),
        "src_loc": loc_out.get("src_loc", 0),
        "comment_loc": loc_out.get("comment_loc", 0),
        "imports_loc": loc_out.get("imports_loc", 0),
        "loc_files_counted": loc_out.get("loc_files_counted", []),
        "entropy_bits": entropy_out.get("entropy_bits", 0.0),
        "harness_errors": harness_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_metrics")
    parser.add_argument("--target", required=True, choices=["josh", "mesa", "josh-mcp"])
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
    conformance_out: dict = {}
    conformance_fuzzy_out: dict = {}
    schema_out: dict = {}
    accept_out: dict = {}
    loc_out: dict = {}
    entropy_out: dict = {}
    target_year: int | None = None

    # Read target_year up front so it can drive the schema-level year check.
    try:
        with open(args.acceptance_ranges) as f:
            ranges = json.load(f)
        target_year = int(ranges.get("target_year"))
    except Exception:
        harness_errors.append(f"acceptance ranges read:\n{traceback.format_exc()}")
        ranges = {}

    # The setup initContainer seeds /sandbox/run.sh for every target — bash
    # arms get the stub the agent fills in; josh-mcp gets a one-line shim
    # that execs the generic MCP runner (containers/josh-mcp-runner.py.seed,
    # installed at /sandbox/runner.py) against agent-authored
    # /sandbox/mcp_calls.json. The scorer's runner.run() path is uniform.
    try:
        runner_out = runner.run(workspace, args.timeout)
    except Exception:
        harness_errors.append(f"runner.run:\n{traceback.format_exc()}")

    # Conformance runs regardless of whether ./run.sh succeeded — the
    # absence of framework-use is its own data point.
    try:
        conformance_out = conformance.check(workspace, args.target)
    except Exception:
        harness_errors.append(f"conformance.check:\n{traceback.format_exc()}")
    try:
        conformance_fuzzy_out = conformance_fuzzy.check(workspace, args.target)
    except Exception:
        harness_errors.append(f"conformance_fuzzy.check:\n{traceback.format_exc()}")

    try:
        schema_out = output_schema.check_output_schema(workspace, target_year or 0)
    except Exception:
        harness_errors.append(f"output_schema.check_output_schema:\n{traceback.format_exc()}")

    if schema_out.get("csv_schema_ok"):
        try:
            accept_out = acceptance.check_output_acceptable(workspace, args.acceptance_ranges)
        except Exception:
            harness_errors.append(f"acceptance.check_output_acceptable:\n{traceback.format_exc()}")
    else:
        accept_out = {
            "height_year100_mean": None,
            "occupancy_year100_mean": None,
            "height_in_range": False,
            "occupancy_in_range": False,
            "regression_fit": {
                "beta": None, "alpha": None, "r2": None, "n_observations": 0,
                "error": "schema gate failed; regression not computed",
            },
            "regression_fit_ok": False,
            "regression_fit_reasons": ["schema gate failed"],
            "acceptance_ranges_used": ranges,
        }

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

    # Guard against stale-CSV poisoning: if ./run.sh did not complete
    # cleanly under the scorer's invocation but earlier self-test
    # leftovers in workspace/output/ satisfy the schema gate, the
    # regression fit reads those leftovers and emits a clean β≈1
    # against a 2-replicate file. The headline ecology gate should be
    # False unless did_run is True. Fit numbers stay on the record as
    # diagnostic evidence; only the gate flips.
    if not did_run and accept_out.get("regression_fit_ok"):
        accept_out["regression_fit_ok"] = False
        reasons = list(accept_out.get("regression_fit_reasons") or [])
        reasons.append("did_run=False; fit computed against pre-existing CSV — not headline")
        accept_out["regression_fit_reasons"] = reasons

    record = _ordered_record(
        target=args.target,
        target_year=target_year,
        runner_out=runner_out,
        conformance_out=conformance_out,
        conformance_fuzzy_out=conformance_fuzzy_out,
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

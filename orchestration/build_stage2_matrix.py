#!/usr/bin/env python3
"""Build the stage-2 headline matrix from stage-1 results.

Stage 1 runs 5 reps × 9 models × 3 targets = 135 cells against
`orchestration/matrix.csv`. After it completes, this script reads the
aggregated CSV for the stage-1 batch and emits a stage-2 matrix that:

- Carries forward each (model, target) combo with **at least 1 pass**
  in stage 1 (per the substantive headline gate:
  `substantive_conformance ∧ did_run ∧ regression_fit_ok` = full pass).
- Drops combos that got 0/5 — those are the "this (model, target) just
  doesn't work" cells; running 10 more isn't going to change that and
  the 5 cells already on record establish the 0% point estimate.
- For each advancing combo, emits 10 more rows so the cumulative N
  across both batches is 15.

Output goes to `orchestration/matrix-stage2.csv`. Run the headline
under a NEW batch tag (e.g. `headline-stage2-YYYYMMDD`) — the analysis
notebook aggregates multi-batch runs cleanly via the existing
`pixi run aggregate runs/headline-stage1-... runs/headline-stage2-...`
two-arg form.

Usage:
    pixi run python orchestration/build_stage2_matrix.py \\
        --stage1-batch headline-stage1-20260602 \\
        --reps 10 \\
        --out orchestration/matrix-stage2.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage1-batch", required=True, help="Batch tag of the stage-1 run.")
    ap.add_argument("--aggregated", default="analysis/aggregated.csv",
                    help="Path to aggregated.csv (default: analysis/aggregated.csv).")
    ap.add_argument("--reps", type=int, default=10, help="Reps to add per advancing combo (default: 10).")
    ap.add_argument("--pass-threshold", type=int, default=1,
                    help="Minimum stage-1 pass count for a combo to advance (default: 1).")
    ap.add_argument("--out", type=Path, default=Path("orchestration/matrix-stage2.csv"))
    args = ap.parse_args()

    # Defer pandas import until needed so `--help` works without the dep.
    import pandas as pd

    df = pd.read_csv(args.aggregated)
    df = df[df.batch_tag == args.stage1_batch].copy()
    if df.empty:
        sys.exit(f"no rows in {args.aggregated} for batch_tag={args.stage1_batch!r}")

    # Pass criterion: the full headline pass (all five axes green). Equivalent
    # to substantive_conformance ∧ did_run ∧ regression_fit_ok. Using the
    # boolean fields directly so we don't depend on the cascade-level helper.
    df["full_pass"] = (
        df["substantive_conformance"].fillna(False).astype(bool)
        & df["did_run"].fillna(False).astype(bool)
        & df["regression_fit_ok"].fillna(False).astype(bool)
    )
    by_combo = df.groupby(["model", "target"]).agg(
        n=("full_pass", "size"),
        passes=("full_pass", "sum"),
    )

    advancing = by_combo[by_combo["passes"] >= args.pass_threshold].index.tolist()
    dropped = by_combo[by_combo["passes"] < args.pass_threshold].index.tolist()

    print(f"=== stage 1 ({args.stage1_batch}) per-combo pass rate ===", file=sys.stderr)
    print(by_combo.to_string(), file=sys.stderr)
    print(file=sys.stderr)
    print(f"Advancing ({len(advancing)} combos) — get {args.reps} more reps each:", file=sys.stderr)
    for m, t in advancing:
        passes = int(by_combo.loc[(m, t), "passes"])
        n = int(by_combo.loc[(m, t), "n"])
        print(f"  ✓ {m}/{t}  ({passes}/{n} passed stage 1)", file=sys.stderr)
    print(file=sys.stderr)
    print(f"Dropped ({len(dropped)} combos) — 0/N in stage 1:", file=sys.stderr)
    for m, t in dropped:
        n = int(by_combo.loc[(m, t), "n"])
        print(f"  ✗ {m}/{t}  (0/{n})", file=sys.stderr)

    # Write stage-2 matrix
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["model", "target"])
        for m, t in advancing:
            for _ in range(args.reps):
                w.writerow([m, t])

    total = len(advancing) * args.reps
    print(file=sys.stderr)
    print(f"✔ Wrote {total} rows ({len(advancing)} combos × {args.reps} reps) → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

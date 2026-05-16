"""Compare year-10 metrics from results.csv against acceptance ranges.

Reads /opt/harness/acceptance_ranges.json (or an override path for tests).
Only runs after output_schema.check passed — caller's responsibility to
gate on csv_schema_ok.

Per the v0 spec, occupancy is a trivial check: trees do not die, reproduce,
or move, so mean tree count per cell at year 10 is whatever the user
authored their `lo_count`/`hi_count` bounds around. We compute it as
"rows-per-cell at target_year" (trivially 1 under the eleven-rows-per-cell
contract); the v0 range [9.9, 10.1] expects the field to be reused as a
direct tree-count under a future mortality-enabled spec.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def check(workspace: Path, ranges_path: Path) -> dict:
    with open(ranges_path) as f:
        ranges = json.load(f)

    target_year = int(ranges["target_year"])
    height_lo = float(ranges["height_year10"]["lo_m"])
    height_hi = float(ranges["height_year10"]["hi_m"])
    occ_lo = float(ranges["occupancy_year10"]["lo_count"])
    occ_hi = float(ranges["occupancy_year10"]["hi_count"])

    csv_path = workspace.resolve() / "output" / "results.csv"
    df = pd.read_csv(csv_path)
    year_df = df[df["year"] == target_year]

    if year_df.empty:
        return {
            "height_year10_mean": None,
            "occupancy_year10_mean": None,
            "height_in_range": False,
            "occupancy_in_range": False,
            "acceptance_ranges_used": ranges,
        }

    height_mean = float(year_df["meanHeight"].mean())
    occupancy_mean = float(year_df.groupby("cell_id").size().mean())

    return {
        "height_year10_mean": height_mean,
        "occupancy_year10_mean": occupancy_mean,
        "height_in_range": height_lo <= height_mean <= height_hi,
        "occupancy_in_range": occ_lo <= occupancy_mean <= occ_hi,
        "acceptance_ranges_used": ranges,
    }

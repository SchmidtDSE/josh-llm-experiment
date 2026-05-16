"""Compare year-10 metrics from results.csv against acceptance ranges.

Reads /opt/harness/acceptance_ranges.json (or an override path for tests).
Only runs after the schema check passed — caller's responsibility to gate
on csv_schema_ok.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def check_output_acceptable(workspace: Path, ranges_path: Path) -> dict:
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
    occupancy_mean = float(year_df["nTrees"].mean())

    return {
        "height_year10_mean": height_mean,
        "occupancy_year10_mean": occupancy_mean,
        "height_in_range": height_lo <= height_mean <= height_hi,
        "occupancy_in_range": occ_lo <= occupancy_mean <= occ_hi,
        "acceptance_ranges_used": ranges,
    }

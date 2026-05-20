"""Phase-6 acceptance check: observed-vs-predicted regression + secondary mean band.

The headline test is whether the agent's per-cell year-final heights
match the spec's deterministic prediction under a linear-regression
fit. Reads the agent's own reported temperature + precipitation
columns to derive `predicted` — so the agent's grid choice and any
climate interpolation logic affect both sides of the regression
symmetrically. A faithful implementation produces β≈1, α≈0, R²→1.

Only runs after the schema check passed (caller gates on csv_schema_ok).
Means + regression are computed on the NaN-filtered DataFrame from
`output_schema.load_clean_results`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from spec_model import DH_MAX, precip_impact, temp_impact
from .output_schema import load_clean_results


def _fit_observed_vs_predicted(
    df: pd.DataFrame,
    target_year: int,
) -> dict:
    """Fit observed_height ~ predicted_growth across (cell, replicate) pairs.

    For each (cell_id, replicate) group, build:
        predicted = Δh_max · Σ_{y>min_year} %_T(T(y)) · %_P(P(y))
        observed  = meanHeight at year == target_year

    The initial year per group is excluded from the sum — trees init
    at h=0 on year 0 and don't grow on that step, so including it
    would systematically bias predicted by a constant ≈ pct_T·pct_P
    per cell (the bug I caught during diagnostic dev — pinning it
    here so the reference and agent regressions are computed
    identically).
    """
    if "replicate" not in df.columns:
        df = df.assign(replicate=0)
    needed = {"cell_id", "year", "replicate", "meanHeight", "temperature", "precipitation"}
    missing = needed - set(df.columns)
    if missing:
        return {
            "beta": None, "alpha": None, "r2": None,
            "n_observations": 0,
            "error": f"missing columns for regression: {sorted(missing)}",
        }

    df = df.copy()
    df["_pct_TP"] = (
        np.asarray(temp_impact(df["temperature"].to_numpy()))
        * np.asarray(precip_impact(df["precipitation"].to_numpy()))
    )

    # Per-group year rank: 1 = initialisation step, > 1 = growth steps.
    df["_year_rank"] = df.groupby(["cell_id", "replicate"])["year"].rank(method="dense")
    growth_rows = df[df["_year_rank"] > 1]
    predicted = (
        growth_rows.groupby(["cell_id", "replicate"])["_pct_TP"].sum() * DH_MAX
    ).rename("predicted")

    observed = (
        df[df["year"] == target_year]
        .set_index(["cell_id", "replicate"])["meanHeight"]
        .rename("observed")
    )

    joined = pd.concat([predicted, observed], axis=1).dropna()
    if len(joined) < 2:
        return {
            "beta": None, "alpha": None, "r2": None,
            "n_observations": int(len(joined)),
            "error": "insufficient (cell, replicate) pairs after join (need ≥ 2)",
        }

    x = joined["predicted"].to_numpy()
    y = joined["observed"].to_numpy()
    A = np.column_stack([np.ones_like(x), x])
    (alpha, beta), *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ [alpha, beta]
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return {
        "beta": float(beta),
        "alpha": float(alpha),
        "r2": float(r2) if math.isfinite(r2) else None,
        "n_observations": int(len(joined)),
    }


def _regression_band_check(fit: dict, band: dict) -> tuple[bool, list[str]]:
    """Return (passes, reasons) — reasons populated only on failure."""
    if fit.get("beta") is None or fit.get("alpha") is None or fit.get("r2") is None:
        return False, ["regression fit failed: " + (fit.get("error") or "unknown")]
    reasons: list[str] = []
    if not (band["beta_lo"] <= fit["beta"] <= band["beta_hi"]):
        reasons.append(f"β={fit['beta']:.4f} outside [{band['beta_lo']}, {band['beta_hi']}]")
    if not (band["alpha_lo_m"] <= fit["alpha"] <= band["alpha_hi_m"]):
        reasons.append(f"α={fit['alpha']:.4f} outside [{band['alpha_lo_m']}, {band['alpha_hi_m']}]")
    if fit["r2"] < band["r2_min"]:
        reasons.append(f"R²={fit['r2']:.4f} below {band['r2_min']}")
    return (len(reasons) == 0), reasons


def check_output_acceptable(workspace: Path, ranges_path: Path) -> dict:
    with open(ranges_path) as f:
        ranges = json.load(f)

    target_year = int(ranges["target_year"])
    regression_band = ranges["regression_band"]
    mean_band = ranges["height_year100"]
    occ_band = ranges["occupancy_year100"]

    df, _ = load_clean_results(workspace)
    year_df = df[df["year"] == target_year]

    if year_df.empty:
        return {
            "height_year100_mean": None,
            "occupancy_year100_mean": None,
            "height_in_range": False,
            "occupancy_in_range": False,
            "regression_fit": {
                "beta": None, "alpha": None, "r2": None, "n_observations": 0,
                "error": f"no rows at target_year={target_year}",
            },
            "regression_fit_ok": False,
            "regression_fit_reasons": [f"no rows at target_year={target_year}"],
            "acceptance_ranges_used": ranges,
        }

    height_mean = float(year_df["meanHeight"].mean())
    occupancy_mean = float(year_df["nTrees"].mean())
    height_in_range = float(mean_band["lo_m"]) <= height_mean <= float(mean_band["hi_m"])
    occupancy_in_range = float(occ_band["lo_count"]) <= occupancy_mean <= float(occ_band["hi_count"])

    fit = _fit_observed_vs_predicted(df, target_year)
    fit_ok, fit_reasons = _regression_band_check(fit, regression_band)

    return {
        "height_year100_mean": height_mean,
        "occupancy_year100_mean": occupancy_mean,
        "height_in_range": height_in_range,
        "occupancy_in_range": occupancy_in_range,
        "regression_fit": fit,
        "regression_fit_ok": fit_ok,
        "regression_fit_reasons": fit_reasons,
        "acceptance_ranges_used": ranges,
    }

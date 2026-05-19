"""Compute internal-consistency metrics against the agent's own CSV.

These checks ask "is the simulation self-consistent with the spec's
equations and constraints?" — *not* "are the values physically right?".
The climate inputs can be wildly wrong and the model can still be
internally consistent (growth equation obeyed, no mortality, age clock
ticking by 1, etc.).

All metrics are descriptive. None gate `did_run`. The user is being
inquisitive — these are recorded for post-hoc review, not used to flip
pass/fail.

Per (cell, year → year+1) transition we compute:
  dh    = meanHeight[t+1] - meanHeight[t]
  dage  = meanAge[t+1]    - meanAge[t]
  dn    = nTrees[t+1]     - nTrees[t]

Aggregated into the fields described in `compute()`'s docstring.

The growth-rate ceiling 1.15 m/yr is Δh_max × (1 + 3σ) for the spec's
Δh_max = 1.0 m/yr and σ = 0.05 stochastic multiplier — the 3-sigma upper
tail. Anything above this is incompatible with the growth equation
regardless of climate inputs.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

from validators.output_schema import load_clean_results

GROWTH_RATE_CEILING_M = 1.15  # Δh_max × (1 + 3σ) for σ=0.05
TEMPERATURE_OPTIMUM_K = 300.0  # midpoint of spec's [T_min=270, T_max=330] window

_EMPTY_RESULT = {
    "total_cells": 0,
    "years_observed": [],
    "total_transition_obs": 0,
    "growth_rate_min_m": None,
    "growth_rate_max_m": None,
    "growth_rate_mean_m": None,
    "growth_rate_negative_frac": None,
    "growth_rate_above_ceiling_frac": None,
    "age_step_mean": None,
    "age_step_off_one_frac": None,
    "ntrees_change_frac": None,
    "growth_temp_spearman": None,
    "growth_precip_spearman": None,
    "notes": [],
}


def _empty(reason: str) -> dict:
    out = dict(_EMPTY_RESULT)
    out["notes"] = [reason]
    return out


def _aggregate_per_cell_year(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse rows to one per (cell_id, year) by averaging numeric cols.

    Agents sometimes emit one row per (cell, year, replicate, step) — for
    consistency analysis we want per (cell, year) aggregates regardless of
    the agent's internal looping structure.
    """
    numeric_cols = [
        c for c in ("nTrees", "meanAge", "meanHeight", "temperature", "precipitation")
        if c in df.columns
    ]
    if not numeric_cols:
        return df
    grouped = df.groupby(["cell_id", "year"], as_index=False)[numeric_cols].mean()
    return grouped.sort_values(["cell_id", "year"]).reset_index(drop=True)


def _per_cell_spearman(
    per_cell_year: pd.DataFrame, y_col: str, x_col: str
) -> float | None:
    """Average per-cell Spearman corr between Δh (y_col) and a predictor.

    Returns None if not enough cells have ≥ 3 transitions to make Spearman
    well-defined, or if every cell has zero variance in either series.
    """
    rhos: list[float] = []
    for _, cell_df in per_cell_year.groupby("cell_id"):
        if len(cell_df) < 4:  # need ≥ 4 rows → 3 transitions
            continue
        y = cell_df[y_col].to_numpy()
        x = cell_df[x_col].to_numpy()
        if pd.isna(y).any() or pd.isna(x).any():
            continue
        if len(set(y.tolist())) < 2 or len(set(x.tolist())) < 2:
            continue
        rho, _ = spearmanr(x, y)
        if pd.notna(rho):
            rhos.append(float(rho))
    if not rhos:
        return None
    return sum(rhos) / len(rhos)


def compute(workspace: Path) -> dict:
    """Compute internal-consistency metrics for an agent's results CSV.

    Caller is expected to have confirmed `csv_schema_ok=true` first.
    Returns a dict suitable for direct inclusion under the `consistency`
    key in the scorer JSON record.

    Fields:
        total_cells, years_observed, total_transition_obs — descriptive
            sanity numbers.
        growth_rate_{min,max,mean}_m — descriptive stats on Δh across all
            (cell, year→year+1) transitions.
        growth_rate_negative_frac — fraction of transitions with Δh < 0.
            Spec value: 0 (no death → no shrinkage).
        growth_rate_above_ceiling_frac — fraction with Δh > 1.15. Spec
            value: 0 (incompatible with the growth equation).
        age_step_mean — mean(meanAge[t+1] − meanAge[t]). Spec value: 1.0.
        age_step_off_one_frac — fraction of transitions where the age
            increment is not exactly 1.0 (within 1e-6). Spec value: 0.
        ntrees_change_frac — fraction of transitions where nTrees
            changed. Spec value: 0 (no mortality / recruitment).
        growth_temp_spearman — mean per-cell Spearman between Δh and
            −|temperature − 300K| (proxy for closeness to optimum).
            Expected ≥ 0; None if temperature column absent.
        growth_precip_spearman — mean per-cell Spearman between Δh and
            precipitation. Expected ≥ 0; None if precipitation absent.
        notes — free-text caveats when computation was skipped.
    """
    df, _ = load_clean_results(workspace)
    if df.empty:
        return _empty("no rows after NaN filtering")
    required = {"cell_id", "year", "nTrees", "meanAge", "meanHeight"}
    if not required.issubset(df.columns):
        return _empty(f"missing required columns for consistency: {required - set(df.columns)}")

    per = _aggregate_per_cell_year(df)
    years_observed = sorted(per["year"].unique().tolist())
    total_cells = int(per["cell_id"].nunique())

    # Per (cell, year→year+1) transitions
    per_sorted = per.sort_values(["cell_id", "year"])
    grouped = per_sorted.groupby("cell_id")
    diffs = grouped[["nTrees", "meanAge", "meanHeight"]].diff()
    year_diff = grouped["year"].diff()
    # Only keep transitions where the year advanced by exactly 1.
    valid = (year_diff == 1) & diffs.notna().all(axis=1)
    dh = diffs.loc[valid, "meanHeight"]
    dage = diffs.loc[valid, "meanAge"]
    dn = diffs.loc[valid, "nTrees"]

    total_obs = int(valid.sum())
    if total_obs == 0:
        out = _empty("no valid (year→year+1) transitions in CSV")
        out["total_cells"] = total_cells
        out["years_observed"] = [int(y) for y in years_observed]
        return out

    growth_min = float(dh.min())
    growth_max = float(dh.max())
    growth_mean = float(dh.mean())
    growth_neg_frac = float((dh < 0).mean())
    growth_over_frac = float((dh > GROWTH_RATE_CEILING_M).mean())
    age_step_mean = float(dage.mean())
    age_off_one_frac = float((dage.sub(1.0).abs() > 1e-6).mean())
    ntrees_change_frac = float((dn != 0).mean())

    # Climate-response correlations: attach Δh to the "source" row's
    # temperature and precipitation (the climate the tree saw before
    # growing).
    per_sorted = per_sorted.copy()
    per_sorted["_dh_next"] = grouped["meanHeight"].diff().shift(-1)
    per_sorted["_yeardiff_next"] = grouped["year"].diff().shift(-1)
    per_sorted = per_sorted[per_sorted["_yeardiff_next"] == 1]

    growth_temp_spearman: float | None = None
    growth_precip_spearman: float | None = None
    if "temperature" in per_sorted.columns and not per_sorted.empty:
        per_sorted["_t_offoptimum"] = -(per_sorted["temperature"] - TEMPERATURE_OPTIMUM_K).abs()
        growth_temp_spearman = _per_cell_spearman(per_sorted, "_dh_next", "_t_offoptimum")
    if "precipitation" in per_sorted.columns and not per_sorted.empty:
        growth_precip_spearman = _per_cell_spearman(per_sorted, "_dh_next", "precipitation")

    return {
        "total_cells": total_cells,
        "years_observed": [int(y) for y in years_observed],
        "total_transition_obs": total_obs,
        "growth_rate_min_m": growth_min,
        "growth_rate_max_m": growth_max,
        "growth_rate_mean_m": growth_mean,
        "growth_rate_negative_frac": growth_neg_frac,
        "growth_rate_above_ceiling_frac": growth_over_frac,
        "age_step_mean": age_step_mean,
        "age_step_off_one_frac": age_off_one_frac,
        "ntrees_change_frac": ntrees_change_frac,
        "growth_temp_spearman": growth_temp_spearman,
        "growth_precip_spearman": growth_precip_spearman,
        "notes": [],
    }

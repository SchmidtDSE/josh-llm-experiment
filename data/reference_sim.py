#!/usr/bin/env python3
"""Spec-faithful Python reference simulator for ForeverTree.

Implements the growth equation from [prompts/BASE_PROMPT.md](../prompts/BASE_PROMPT.md)
against the synthetic climate netCDFs in this directory and persists the
empirical acceptance bands derived from it to
[harness/acceptance_ranges.json](../harness/acceptance_ranges.json).

The headline acceptance gate under phase-6 is a regression test:

    observed_height(cell, replicate) ~ predicted_growth(cell) + ε

where `predicted_growth(cell) = Σ_y Δh_max · %_T(T(y,cell)) · %_P(P(y,cell))`
is the deterministic spec prediction using the cell's actual climate
trajectory (including the warming trend — it's already in the sum).
Under a faithful implementation:

    β (slope)     → 1.0   modulo per-replicate noise from O ~ N(1, 0.05)
    α (intercept) → 0.0
    R²            → 1.0   limited only by the noise term

This script measures β/α/R² on the reference and persists the
acceptance tolerances around them. Deviations from β=1 indicate the
agent's effective growth-rate constant is off; deviations from R²≈1
indicate the agent's response *shape* is wrong somewhere.

**phase6-v2 precision change.** Starting with the phase6-v2 scorer,
the growth-equation accumulation in `simulate()` runs under
`decimal.Decimal` (28-digit context) rather than `np.float64`. This
matches (a) Josh's default `BigDecimal` runtime arithmetic and (b)
the Numerical-precision directive in `prompts/targets/mesa.md` that
steers Mesa agents onto `decimal.Decimal` for the dynamics. The
purpose is purely methodological: a Mesa-Decimal agent should land
on bands that were derived under the same precision regime, so any
remaining β/α drift cannot be attributed to a precision mismatch
between the agent and the reference. Climate input is still loaded
as `np.float64` from the netCDFs (xarray-native) and cast to
Decimal at the per-(year, cell) precompute step; the stochastic
noise is still drawn from numpy (Decimal has no native Gaussian)
and cast to Decimal per-element before it multiplies into the
growth. The OLS fit at the end (`scipy.stats.linregress`) is numpy
float — Decimal is for the accumulation, not the regression. Runtime
for the full 100-replicate × 1550-cell × 101-year grid is ≈ 25 min
on a single core (vs ≈ 5 s under the previous float path); this is a
one-time generation per spec/precision change.

Regeneration chain — runs deterministically against the committed
synthetic climate netCDFs:

    docker run --rm -v $(pwd):/repo -w /repo fortree:scorer \\
        python data/generate_synthetic_climate.py
    docker run --rm -v $(pwd):/repo -w /repo fortree:scorer \\
        python data/reference_sim.py
    docker run --rm -v $(pwd):/repo -w /repo fortree:scorer \\
        python reference/regenerate_fixtures.py
    docker build --target scorer -t fortree:scorer .

The first step is stable unless the climate generator changes. Steps
2 and 3 are coupled: both regenerate together when the spec or
climate moves. Step 4 rebuilds the scorer image with the new
acceptance bands + fixtures baked in.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import xarray as xr
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
HARNESS_DIR = REPO_ROOT / "harness"

# Use the same spec primitives the scorer uses, so derivation and
# enforcement cannot drift. The `_dec` variants are scalar-Decimal
# (used here for accumulation); the unsuffixed variants are
# numpy-vectorised float64 (used by harness/validators/acceptance.py).
sys.path.insert(0, str(HARNESS_DIR))
from spec_model import (  # noqa: E402 — sys.path edit must precede import
    NOISE_MEAN,
    NOISE_SIGMA,
    NOISE_MEAN_DEC,
    NOISE_SIGMA_DEC,
    SECONDS_PER_YEAR,
    TREES_PER_PATCH,
    predicted_growth_per_year_dec,
)

_DECIMAL_ZERO = Decimal("0")


def load_climate(start_year: int, end_year: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load tasmax + pr from the synthetic netCDFs for the inclusive year range.

    Returns (T_kelvin, P_mm_yr, lats, lons) with T and P shape
    (n_years, n_lat, n_lon). P is converted from raw flux to mm/year.
    Values are `np.float64` — the per-(year, cell) cast to Decimal
    happens at the precompute step inside `simulate`.
    """
    with xr.open_dataset(DATA_DIR / "maxtemp_synthetic.nc") as ds_t, \
         xr.open_dataset(DATA_DIR / "precip_synthetic.nc") as ds_p:
        years_slice = slice(start_year, end_year)
        T = ds_t["tasmax"].sel(calendar_year=years_slice).values
        P_flux = ds_p["pr"].sel(calendar_year=years_slice).values
        lats = ds_t["lat"].values
        lons = ds_t["lon"].values
    return T, P_flux * SECONDS_PER_YEAR, lats, lons


def _float_to_decimal(value: float) -> Decimal:
    """Cast a Python / numpy float to Decimal via its string form.

    Going through `str()` avoids re-imprinting binary-float
    representation error onto the Decimal; the resulting Decimal
    matches the float's printed shortest-round-trip representation
    (typically 15–17 digits — well below the 28-digit context).
    """
    return Decimal(str(float(value)))


def _precompute_per_year_predicted_dec(T_flat: np.ndarray, P_flat: np.ndarray) -> np.ndarray:
    """Deterministic per-(year, cell) growth Δh_max · %_T · %_P, as Decimal.

    Returns an `object`-dtype numpy array of shape (n_years, n_cells) so
    downstream per-element loops can still address by `[t, c]`.
    """
    n_years, n_cells = T_flat.shape
    out = np.empty((n_years, n_cells), dtype=object)
    for t in range(n_years):
        for c in range(n_cells):
            T = _float_to_decimal(T_flat[t, c])
            P = _float_to_decimal(P_flat[t, c])
            out[t, c] = predicted_growth_per_year_dec(T, P)
    return out


def simulate(
    *,
    start_year: int = 2024,
    end_year: int = 2123,
    n_replicates: int = 100,
    seed: int = 0,
    cell_mask: np.ndarray | None = None,
    collect_rows: bool = False,
) -> dict:
    """Run the reference simulation against the synthetic climate.

    Args:
        start_year, end_year: Inclusive year range. Must lie inside the
            committed synthetic-climate netCDF coverage.
        n_replicates: Independent replicates per cell. Each gets its
            own 10-tree population with independent O draws.
        seed: Master seed for reproducibility.
        cell_mask: Optional (n_lat, n_lon) boolean mask to restrict
            the simulation to a subset of cells. Defaults to the full grid.
        collect_rows: If True, build a CSV-ready row list per
            (cell, year, replicate) — used by fixture generation on a
            small subgrid. Skip for band-derivation runs over the full
            grid (building 15M+ dicts is slow and unused).

    Returns dict with:
        - 'height_year_final': (n_cells, n_replicates) `np.float64` —
          per-cell, per-replicate meanHeight at the final year. Always
          populated. Cast to float at the return boundary so the
          downstream OLS (scipy.stats.linregress) sees numpy floats.
        - 'predicted_growth_per_cell': (n_cells,) `np.float64` —
          deterministic spec-faithful prediction per cell.
        - 'lat_per_cell', 'lon_per_cell': (n_cells,) coordinate arrays.
        - 'rows': only populated if collect_rows=True. Rows carry
          `meanHeight` as Python `float` (cast from Decimal at emit).
    """
    T_full, P_full, lats, lons = load_climate(start_year, end_year)
    n_years, n_lat, n_lon = T_full.shape

    if cell_mask is None:
        cell_mask = np.ones((n_lat, n_lon), dtype=bool)
    flat_mask = cell_mask.ravel()
    n_cells = int(flat_mask.sum())

    T_flat = T_full.reshape(n_years, -1)[:, flat_mask]   # (n_years, n_cells) float64
    P_flat = P_full.reshape(n_years, -1)[:, flat_mask]   # (n_years, n_cells) float64

    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    lat_flat = lat_grid.ravel()[flat_mask]
    lon_flat = lon_grid.ravel()[flat_mask]
    cell_idx = np.arange(n_lat * n_lon).reshape(n_lat, n_lon).ravel()[flat_mask]

    # Deterministic spec prediction (no noise), per-(year, cell) Decimal.
    # Every year is a growth step (trees init at h=0 *before* the sim,
    # that init state is not a row); the sum therefore runs over every
    # year in the range.
    per_year_predicted = _precompute_per_year_predicted_dec(T_flat, P_flat)  # (n_years, n_cells) Decimal
    predicted_growth = per_year_predicted.sum(axis=0)                        # (n_cells,) Decimal

    # Stochastic simulation: 10 trees per (cell, replicate), independent
    # O per (cell, replicate, year, tree). Heights stored as Decimal in
    # an object array; updates iterate per-element since Decimal does
    # not vectorise through numpy ufuncs.
    rng = np.random.default_rng(seed)
    heights = np.full(
        (n_cells, n_replicates, TREES_PER_PATCH),
        _DECIMAL_ZERO,
        dtype=object,
    )

    rows: list[dict] = []

    def _emit_year(year: int, age: float, mean_h: np.ndarray) -> None:
        if not collect_rows:
            return
        for c in range(n_cells):
            T_val = float(T_flat[year - start_year, c])
            P_val = float(P_flat[year - start_year, c])
            cell_id = f"c{int(cell_idx[c])}"
            lat = float(lat_flat[c])
            lon = float(lon_flat[c])
            for r in range(n_replicates):
                rows.append({
                    "cell_id": cell_id,
                    "lat": lat,
                    "lon": lon,
                    "year": year,
                    "replicate": r,
                    "nTrees": TREES_PER_PATCH,
                    "meanAge": age,
                    "meanHeight": float(mean_h[c, r]),
                    "temperature": T_val,
                    "precipitation": P_val,
                })

    # Every year is a growth step. Trees enter the loop at h=0 (pre-sim
    # initial state, not emitted as a row). For each year t in 0..n-1,
    # apply that year's climate to grow once, then emit the post-growth
    # state as the CSV row for year (start_year + t).
    # per_year_predicted[t, c] is already Δh_max · %_T(t,c) · %_P(t,c)
    # as a Decimal. Per-step growth is that times the stochastic offset
    # O ~ N(NOISE_MEAN, NOISE_SIGMA) (cast per-draw to Decimal).
    noise_mean_f = float(NOISE_MEAN)
    noise_sigma_f = float(NOISE_SIGMA)
    for t in range(n_years):
        O = rng.normal(noise_mean_f, noise_sigma_f, size=heights.shape)
        # Per-element update — both operands must be Decimal for object
        # arithmetic. Casting via `str()` keeps binary-float bias from
        # leaking in (only the float64 shortest representation enters
        # the Decimal computation).
        for c in range(n_cells):
            growth_y = per_year_predicted[t, c]
            for r in range(n_replicates):
                for k in range(TREES_PER_PATCH):
                    heights[c, r, k] += growth_y * _float_to_decimal(O[c, r, k])

        # Per-year mean over trees, cast to float for the CSV row.
        # mean computed in Decimal then divided by trees-per-patch.
        if collect_rows:
            mean_h_dec = heights.sum(axis=2) / Decimal(TREES_PER_PATCH)
            mean_h_float = np.array(
                [[float(mean_h_dec[c, r]) for r in range(n_replicates)] for c in range(n_cells)],
                dtype=np.float64,
            )
            _emit_year(start_year + t, float(t + 1), mean_h_float)

    # Final per-(cell, replicate) meanHeight, cast to float at the
    # boundary. OLS (scipy.stats.linregress) expects float arrays.
    height_year_final = np.array(
        [
            [
                float(sum(heights[c, r, k] for k in range(TREES_PER_PATCH)) / Decimal(TREES_PER_PATCH))
                for r in range(n_replicates)
            ]
            for c in range(n_cells)
        ],
        dtype=np.float64,
    )
    predicted_growth_float = np.array(
        [float(predicted_growth[c]) for c in range(n_cells)],
        dtype=np.float64,
    )

    return {
        "height_year_final": height_year_final,             # (n_cells, n_replicates) float64
        "predicted_growth_per_cell": predicted_growth_float, # (n_cells,) float64
        "lat_per_cell": lat_flat,
        "lon_per_cell": lon_flat,
        "rows": rows,
    }


def fit_regression(observed_per_cell_rep: np.ndarray, predicted_per_cell: np.ndarray) -> dict:
    """OLS fit of observed ~ predicted, pooling across (cell, replicate).

    Args:
        observed_per_cell_rep: (n_cells, n_replicates) — agent's or
            reference's meanHeight at the target year.
        predicted_per_cell: (n_cells,) — deterministic spec prediction
            per cell.

    Returns dict with beta (slope), alpha (intercept), r2, n_observations.
    """
    _, n_replicates = observed_per_cell_rep.shape
    y = observed_per_cell_rep.ravel()
    x = np.repeat(predicted_per_cell, n_replicates)
    fit = stats.linregress(x, y)
    return {
        "beta": float(fit.slope),
        "alpha": float(fit.intercept),
        "r2": float(fit.rvalue ** 2),
        "n_observations": int(y.size),
    }


def write_acceptance_ranges(
    *,
    start_year: int,
    end_year: int,
    n_replicates: int,
    reference_fit: dict,
    height_mean: float,
    height_std: float,
) -> None:
    """Persist the acceptance gate to harness/acceptance_ranges.json.

    Two thresholds:

    1. **Regression band** (headline gate). β tolerance ±5% of the
       spec value (1.0), R² floor 0.95, α tolerance ±0.5 m. These are
       hand-picked to catch wrong-dynamics implementations without
       false-positiving on slight implementation differences
       (interpolation, RNG seed, output rounding). The reference's
       *observed* β/α/R² are recorded alongside as documentation.

    2. **Mean band** (secondary). Loose 3σ band on year-final
       meanHeight, useful only as a sanity check on totally-broken
       runs (e.g., the agent never wrote a non-zero CSV).
    """
    payload = {
        "schema_version": "phase6-v2",
        "_generated_by": "data/reference_sim.py",
        "_climate_inputs": [
            "data/maxtemp_synthetic.nc",
            "data/precip_synthetic.nc",
        ],
        "_note": (
            "Generated artefact — do NOT hand-edit. Bands derived from a "
            "spec-faithful reference simulation against the committed "
            "synthetic climate netCDFs. The headline gate is the "
            "observed-vs-predicted regression (β/α/R²); the mean band "
            "is a coarse secondary sanity check. As of phase6-v2 the "
            "reference accumulation runs under decimal.Decimal (28-digit "
            "context) to match Josh's BigDecimal default and the Mesa "
            "Numerical-precision directive. Re-run data/reference_sim.py "
            "and commit both to update."
        ),
        "start_year": start_year,
        "target_year": end_year,
        "n_replicates_reference": n_replicates,

        "regression_band": {
            "_metric": (
                "Fit observed_year_target ~ predicted_growth across all "
                "(cell, replicate) pairs, where predicted_growth is the "
                "spec's deterministic time-integral Σ_t Δh_max · %_T(T(t,cell)) "
                "· %_P(P(t,cell)) using the agent's reported climate values."
            ),
            "beta_lo": 0.95,
            "beta_hi": 1.05,
            "alpha_lo_m": -0.5,
            "alpha_hi_m": 0.5,
            "r2_min": 0.95,
            "_reference_observed": {
                "beta": round(reference_fit["beta"], 6),
                "alpha": round(reference_fit["alpha"], 6),
                "r2": round(reference_fit["r2"], 6),
                "n_observations": reference_fit["n_observations"],
            },
        },

        "height_year100": {
            "_metric": "Mean meanHeight across all cells × replicates at year == target_year. Coarse secondary sanity check.",
            "lo_m": round(max(0.0, height_mean - 3.0 * height_std), 4),
            "hi_m": round(height_mean + 3.0 * height_std, 4),
            "_observed_mean": round(height_mean, 4),
            "_observed_std": round(height_std, 4),
            "_sigma_mult": 3.0,
        },

        "occupancy_year100": {
            "_metric": "Mean nTrees per cell at year == target_year. Spec value is exactly 10 — no death, reproduction, migration. Narrow tolerance is for floating-point representation, not biological variability.",
            "lo_count": 9.999,
            "hi_count": 10.001,
        },
    }

    out_path = HARNESS_DIR / "acceptance_ranges.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    rb = payload["regression_band"]
    print(f"  regression gate: β ∈ [{rb['beta_lo']}, {rb['beta_hi']}], "
          f"α ∈ [{rb['alpha_lo_m']}, {rb['alpha_hi_m']}], R² > {rb['r2_min']}")
    print(f"  reference observed: β={rb['_reference_observed']['beta']:.6f}, "
          f"α={rb['_reference_observed']['alpha']:.6f}, "
          f"R²={rb['_reference_observed']['r2']:.6f}")
    hb = payload["height_year100"]
    print(f"  mean band (secondary): [{hb['lo_m']:.2f}, {hb['hi_m']:.2f}] m "
          f"(mean={hb['_observed_mean']:.2f}, σ={hb['_observed_std']:.2f})")


def main() -> None:
    start_year = 2024
    end_year = 2123
    n_replicates = 100

    print(f"▶ Reference simulation: {start_year}–{end_year}, {n_replicates} replicates × full climate grid")
    print("  (Decimal arithmetic — expect ≈ 25 min on a single core)")
    result = simulate(
        start_year=start_year,
        end_year=end_year,
        n_replicates=n_replicates,
        seed=0,
    )

    reference_fit = fit_regression(
        result["height_year_final"],
        result["predicted_growth_per_cell"],
    )
    flat_heights = result["height_year_final"].ravel()
    write_acceptance_ranges(
        start_year=start_year,
        end_year=end_year,
        n_replicates=n_replicates,
        reference_fit=reference_fit,
        height_mean=float(flat_heights.mean()),
        height_std=float(flat_heights.std(ddof=1)),
    )


if __name__ == "__main__":
    main()

"""Spec-faithful growth-equation primitives, shared by reference sim and scorer.

Single source of truth for the dynamics described in
[prompts/BASE_PROMPT.md](../prompts/BASE_PROMPT.md). Lives under
`harness/` so it gets baked into the scorer image; `data/reference_sim.py`
imports from here too via `sys.path` so band derivation and acceptance
checking use byte-identical functions.

Constants and equations are pulled directly from BASE_PROMPT.md §Growth Model:

    Δh = Δh_max · %_T · %_P · O
    %_T = 4 · x_T · (1 − x_T)            with x_T = (T − T_min)/(T_max − T_min), clamped to [0,1]
    %_P = 1 / (1 + exp(−k · (x_P − 0.5))) with x_P = (P − P_low)/(P_high − P_low)
    O ~ N(1, 0.05)                        independent per tree, per year

The `predicted_growth` helper computes the deterministic time-integral
of Δh_max · %_T · %_P over a year range, using the cell's actual climate
trajectory. Under a faithful implementation, the agent's per-cell
observed year-final height should be close to this value (modulo the
O noise term, which has mean 1 → no bias). That equivalence is what
the scorer's regression test exploits.
"""

from __future__ import annotations

import numpy as np

# Defaults from prompts/BASE_PROMPT.md §Growth Model.
DH_MAX = 1.0
T_MIN, T_MAX = 270.0, 330.0
P_LOW, P_HIGH = 300.0, 500.0
P_K = 12.0
TREES_PER_PATCH = 10
NOISE_MEAN = 1.0
NOISE_SIGMA = 0.05

# netCDF flux → mm/year conversion. Used when reading the synthetic
# precipitation file (units: kg m⁻² s⁻¹).
SECONDS_PER_YEAR = 31_536_000


def temp_impact(T_kelvin: np.ndarray | float) -> np.ndarray | float:
    """Quadratic temperature impact, clamped to [T_min, T_max]."""
    T = np.clip(T_kelvin, T_MIN, T_MAX)
    x = (T - T_MIN) / (T_MAX - T_MIN)
    return 4.0 * x * (1.0 - x)


def precip_impact(P_mm_yr: np.ndarray | float) -> np.ndarray | float:
    """Logistic precipitation impact centred on midpoint of [P_low, P_high]."""
    x = (P_mm_yr - P_LOW) / (P_HIGH - P_LOW)
    return 1.0 / (1.0 + np.exp(-P_K * (x - 0.5)))


def predicted_growth_per_year(T_kelvin: np.ndarray, P_mm_yr: np.ndarray) -> np.ndarray:
    """Deterministic per-step growth Δh = Δh_max · %_T · %_P (no noise).

    Element-wise; T and P must broadcast to the same shape.
    """
    return DH_MAX * temp_impact(T_kelvin) * precip_impact(P_mm_yr)


def predicted_total_growth(
    T_kelvin: np.ndarray,
    P_mm_yr: np.ndarray,
) -> np.ndarray:
    """Sum of per-year predicted growth over the leading axis.

    Every year contributes exactly one growth event — matches the spec's
    "every year is a growth step" convention. Trees are initialised at
    h=0 *before* the simulation begins; that initial state is not part
    of the year-axis. An N-year simulation produces N growth events.

    Args:
        T_kelvin, P_mm_yr: arrays of shape (n_years, ...) — same shape as
            the climate netCDFs' year-axis arrays restricted to the
            simulation's year range.

    Returns the integrated growth per cell — shape `T_kelvin.shape[1:]`.
    """
    return predicted_growth_per_year(T_kelvin, P_mm_yr).sum(axis=0)

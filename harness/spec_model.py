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

Two parallel API surfaces:

- **Numpy / float64** (`temp_impact`, `precip_impact`, `DH_MAX`, …) — the
  scoring path. `harness/validators/acceptance.py` vectorises across
  the agent's CSV at scoring time; precision is bounded by the agent's
  float-precision CSV anyway, so float64 here is faithful.

- **Decimal scalar** (`temp_impact_dec`, `precip_impact_dec`,
  `DH_MAX_DEC`, …) — the reference-simulator path (phase6-v2 onwards).
  Matches Josh's default `BigDecimal` arithmetic so the empirical
  acceptance bands in `harness/acceptance_ranges.json` are derived
  under matching precision to a Mesa agent that followed the
  Numerical-precision directive in `prompts/targets/mesa.md`. Decimal
  arithmetic does not vectorise through numpy ufuncs — callers are
  expected to loop per-(year, cell) at the Python level.

Both paths encode the same equations; the duplication is intentional.
A change to the math must be applied to both.
"""

from __future__ import annotations

from decimal import Decimal, getcontext

import numpy as np

# Pin the global Decimal precision. 28 digits is Python's default;
# pinning it explicitly means `import spec_model` is sufficient to
# guarantee the precision the reference bands were derived under, so
# `acceptance_ranges.json` is reproducible from the committed source.
getcontext().prec = 28


# Numpy / float64 path — used by harness/validators/acceptance.py.
# Defaults from prompts/BASE_PROMPT.md §Growth Model.
DH_MAX = 1.0
T_MIN, T_MAX = 270.0, 330.0
P_LOW, P_HIGH = 300.0, 500.0
P_K = 12.0
TREES_PER_PATCH = 10
NOISE_MEAN = 1.0
NOISE_SIGMA = 0.05


# Decimal path — used by data/reference_sim.py for band derivation.
# Same physical values; instantiated from string literals so binary-float
# representation error doesn't leak in at construction.
DH_MAX_DEC = Decimal("1.0")
T_MIN_DEC = Decimal("270.0")
T_MAX_DEC = Decimal("330.0")
P_LOW_DEC = Decimal("300.0")
P_HIGH_DEC = Decimal("500.0")
P_K_DEC = Decimal("12.0")
NOISE_MEAN_DEC = Decimal("1.0")
NOISE_SIGMA_DEC = Decimal("0.05")

# Closed-form helpers reused inside the scalar Decimal impact functions.
_ZERO = Decimal("0")
_ONE = Decimal("1")
_FOUR = Decimal("4")
_HALF = Decimal("0.5")
_T_SPAN_DEC = T_MAX_DEC - T_MIN_DEC
_P_SPAN_DEC = P_HIGH_DEC - P_LOW_DEC


# netCDF flux → mm/year conversion. Used when reading the synthetic
# precipitation file (units: kg m⁻² s⁻¹).
SECONDS_PER_YEAR = 31_536_000


def temp_impact(T_kelvin: np.ndarray | float) -> np.ndarray | float:
    """Quadratic temperature impact, clamped to [T_min, T_max]. Numpy / float64."""
    T = np.clip(T_kelvin, T_MIN, T_MAX)
    x = (T - T_MIN) / (T_MAX - T_MIN)
    return 4.0 * x * (1.0 - x)


def precip_impact(P_mm_yr: np.ndarray | float) -> np.ndarray | float:
    """Logistic precipitation impact centred on midpoint of [P_low, P_high]. Numpy / float64."""
    x = (P_mm_yr - P_LOW) / (P_HIGH - P_LOW)
    return 1.0 / (1.0 + np.exp(-P_K * (x - 0.5)))


def predicted_growth_per_year(T_kelvin: np.ndarray, P_mm_yr: np.ndarray) -> np.ndarray:
    """Deterministic per-step growth Δh = Δh_max · %_T · %_P (no noise). Numpy / float64.

    Element-wise; T and P must broadcast to the same shape.
    """
    return DH_MAX * temp_impact(T_kelvin) * precip_impact(P_mm_yr)


def predicted_total_growth(
    T_kelvin: np.ndarray,
    P_mm_yr: np.ndarray,
) -> np.ndarray:
    """Sum of per-year predicted growth over the leading axis. Numpy / float64.

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


def temp_impact_dec(T: Decimal) -> Decimal:
    """Quadratic temperature impact, clamped to [T_min, T_max]. Decimal scalar."""
    T_c = T
    if T_c < T_MIN_DEC:
        T_c = T_MIN_DEC
    elif T_c > T_MAX_DEC:
        T_c = T_MAX_DEC
    x = (T_c - T_MIN_DEC) / _T_SPAN_DEC
    return _FOUR * x * (_ONE - x)


def precip_impact_dec(P: Decimal) -> Decimal:
    """Logistic precipitation impact centred on midpoint of [P_low, P_high]. Decimal scalar.

    Decimal exposes `.exp()` natively (handles negative arguments), so
    the logistic curve is expressible without falling back to math.exp.
    """
    x = (P - P_LOW_DEC) / _P_SPAN_DEC
    return _ONE / (_ONE + (-P_K_DEC * (x - _HALF)).exp())


def predicted_growth_per_year_dec(T: Decimal, P: Decimal) -> Decimal:
    """Deterministic per-step growth Δh = Δh_max · %_T · %_P (no noise). Decimal scalar.

    Callers handle vectorisation (typically a per-(year, cell) Python
    loop), since Decimal arithmetic does not vectorise through numpy's
    ufuncs.
    """
    return DH_MAX_DEC * temp_impact_dec(T) * precip_impact_dec(P)

#!/usr/bin/env python3
"""Generate the synthetic ForeverTree climate dataset.

Writes `data/maxtemp_synthetic.nc` and `data/precip_synthetic.nc` with
shape (calendar_year, lat, lon) and a deterministic gradient calibrated
so the spec's growth equation operates in its *gradient* regime rather
than its plateau regime — every cell grows nonzero, no cell saturates.

Phase-6 ranges:

- **Temperature** varies linearly with latitude (315K south → 285K
  north at year 0) and warms +0.05 K/yr (≈ +5 K over 100 years,
  RCP4.5-scale). Year-100 range is [290, 320] K — symmetric around
  the spec's optimal 300K and entirely inside [T_min=270, T_max=330]
  so no cell is clamped. The parabolic temperature response sees
  both sides of the peak.

- **Precipitation** varies linearly with longitude (550 mm/yr west →
  250 mm/yr east) as a physically-honest precipitation flux in
  `kg m⁻² s⁻¹`. After the standard `× 31_536_000` (seconds per year)
  conversion, values span 250–550 mm/yr. The spec's sigmoid
  (`P_low=300, P_high=500`, k=12) sees the active gradient region
  without saturating completely at either end. No long-term P trend.

The two gradients are orthogonal (T along lat, P along lon), so the
year-100 mean tree height is *separable* in (lat, lon) under faithful
implementation. This is what makes the `observed ~ predicted`
regression in data/reference_sim.py a meaningful headline check —
the predicted total growth per cell is well-conditioned and the
deviation from β=1 has a direct physical interpretation.

Both build_* functions read from fixed seeds; running this script
twice produces byte-identical netCDFs. Re-generate inside the
fortree image:

    docker run --rm -v $(pwd):/repo -w /repo fortree:scorer \\
        python data/generate_synthetic_climate.py

No NaN cells; no edge masking. The whole grid is valid.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

# Bbox kept identical to the prior Tulare dataset so prompts that reference
# coordinate ranges (BASE_PROMPT.md's bbox table) don't need rewriting.
LAT_MIN, LAT_MAX = 35.80, 36.73
LON_MIN, LON_MAX = -119.52, -117.98
N_LAT = 31
N_LON = 50

# Year range covers the phase-6 100-year experiment (2024–2123 inclusive)
# plus 1 yr of headroom. Bumped from the phase-5 range of 2024–2054.
YEARS = list(range(2024, 2125))  # 101 years

SEED = 42


def build_temperature(years: list[int], lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Latitude-driven temperature with a small warming trend.

    Phase-6 design:
    - Spatial gradient softened from [285, 315] K to [285, 315] K at
      year 0, kept symmetric around the spec's optimum 300K.
    - Warming trend reduced from +0.15 K/yr to +0.05 K/yr (= +5 K over
      100 years, roughly RCP4.5-scale).
    - Year-100 range becomes [290, 320] K — still inside the spec's
      tolerated [T_min=270, T_max=330] window with margin, so no cell
      gets clamped at either end. Keeps the parabolic temperature
      response in its gradient region, not its plateau region.
    """
    rng = np.random.default_rng(SEED)
    lat_grid, _ = np.meshgrid(lats, lons, indexing="ij")
    # Fraction along latitude axis: 0 at the northern edge, 1 at the southern.
    lat_frac = (LAT_MAX - lat_grid) / (LAT_MAX - LAT_MIN)

    out = np.empty((len(years), N_LAT, N_LON), dtype=np.float64)
    for i, year in enumerate(years):
        base = 285.0 + 30.0 * lat_frac          # 285K north, 315K south at year 0
        warming = 0.05 * (year - years[0])      # +5K over 100 years
        noise = rng.normal(0.0, 0.4, (N_LAT, N_LON))
        out[i] = base + warming + noise
    return out


SECONDS_PER_YEAR = 31_536_000  # 365 × 86_400 (no leap-day handling)


def build_precipitation_raw(years: list[int], lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Longitude-driven precipitation as a true flux in `kg m⁻² s⁻¹`.

    Internally constructed in mm/year then divided by seconds-per-year,
    so the raw netCDF values are a physical mass-flux density (no
    misleading unit labels, no pipeline-history quirks). Standard
    `× 31_536_000` conversion recovers mm/year.

    Phase-6 design: target mm/year range is 250–550, putting the
    sigmoid threshold band (P_low=300 → P_high=500) inside the gradient
    region rather than spanning to the saturated extremes. Endpoints
    sit ~50 mm/yr outside the [P_low, P_high] window so the sigmoid
    sees both shoulders (~5% at the east edge, ~95% at the west edge)
    without saturating completely. No long-term P trend.
    """
    rng = np.random.default_rng(SEED + 1)
    _, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    # Fraction along longitude axis: 1 at the western edge, 0 at the eastern.
    lon_frac = (LON_MAX - lon_grid) / (LON_MAX - LON_MIN)

    out = np.empty((len(years), N_LAT, N_LON), dtype=np.float64)
    for i, year in enumerate(years):
        # Build in mm/year, then convert to kg/m²/s by dividing by seconds-per-year.
        base_mm_yr = 250.0 + 300.0 * lon_frac   # 250mm east → 550mm west
        interannual = rng.normal(0.0, 40.0, (N_LAT, N_LON))
        mm_yr = np.clip(base_mm_yr + interannual, 150.0, 650.0)
        out[i] = mm_yr / SECONDS_PER_YEAR
    return out


def write_netcdf(out_path: Path, variable: str, data: np.ndarray, attrs: dict,
                 years: list[int], lats: np.ndarray, lons: np.ndarray,
                 title: str) -> None:
    ds = xr.Dataset(
        data_vars={
            variable: (
                ["calendar_year", "lat", "lon"],
                data,
                attrs,
            ),
        },
        coords={
            # calendar_year is an integer year index, not a CF time coordinate
            # (no `axis: "T"`). Keeping it as a plain dimension coordinate
            # avoids requiring CF time-units encoding (e.g. "years since …"),
            # which would force xarray to decode to datetime64 and break the
            # agent's `.sel(calendar_year=2024)` convention.
            "calendar_year": (
                "calendar_year",
                np.asarray(years, dtype=np.int32),
                {
                    "long_name": "Calendar year (integer year index)",
                },
            ),
            "lat": (
                "lat",
                lats.astype(np.float32),
                {
                    "standard_name": "latitude",
                    "long_name": "Latitude",
                    "units": "degrees_north",
                    "axis": "Y",
                },
            ),
            "lon": (
                "lon",
                lons.astype(np.float32),
                {
                    "standard_name": "longitude",
                    "long_name": "Longitude",
                    "units": "degrees_east",
                    "axis": "X",
                },
            ),
        },
        attrs={
            "Conventions": "CF-1.8",
            "title": title,
            "summary": "Synthetic ForeverTree climate forcing — reproducible from data/generate_synthetic_climate.py.",
            "history": "Generated by data/generate_synthetic_climate.py (seed=42).",
            "institution": "SchmidtDSE/josh-llm-experiment",
            "source": "synthetic — data/generate_synthetic_climate.py",
            "seed": SEED,
            "regeneration_cmd": "docker run --rm -v $(pwd):/repo -w /repo fortree:agent python data/generate_synthetic_climate.py",
        },
    )
    # CF doesn't want _FillValue on coordinate variables.
    encoding = {name: {"_FillValue": None} for name in ("lat", "lon", "calendar_year")}
    ds.to_netcdf(out_path, encoding=encoding)


def main() -> None:
    here = Path(__file__).resolve().parent
    lats = np.linspace(LAT_MIN, LAT_MAX, N_LAT)
    lons = np.linspace(LON_MIN, LON_MAX, N_LON)

    temp = build_temperature(YEARS, lats, lons)
    precip = build_precipitation_raw(YEARS, lats, lons)

    write_netcdf(
        out_path=here / "maxtemp_synthetic.nc",
        variable="tasmax",
        data=temp,
        title="Synthetic annual-maximum air temperature at 2 metres",
        attrs={
            "standard_name": "air_temperature",
            "long_name": "Synthetic annual-maximum-equivalent air temperature at 2 m",
            "units": "K",
            "cell_methods": "calendar_year: maximum",
            "variable_id": "tasmax",
            "extended_description": (
                "Synthetic. Linear north-to-south latitude gradient (285K → 315K) "
                "at year 0, plus a +0.05 K/year warming trend (=+5K over 100 yr) "
                "and ±0.4 K Gaussian interannual noise. Year-100 range is "
                "[290, 320] K, entirely inside the spec's [T_min=270, T_max=330] "
                "tolerated window. No NaN cells."
            ),
        },
        years=YEARS, lats=lats, lons=lons,
    )
    write_netcdf(
        out_path=here / "precip_synthetic.nc",
        variable="pr",
        data=precip,
        title="Synthetic precipitation flux (sum-of-daily-rates form)",
        attrs={
            "standard_name": "precipitation_flux",
            "long_name": "Synthetic precipitation flux, sum-of-daily-rates form",
            "units": "kg m-2 s-1",
            "cell_methods": "calendar_year: sum",
            "variable_id": "pr",
            "extended_description": (
                "Synthetic. Linear west-to-east longitude gradient (~550 mm/yr west, "
                "~250 mm/yr east) with ±40 mm/yr interannual noise; no long-term "
                "trend. Values are a true precipitation flux in `kg m⁻² s⁻¹`; "
                "multiplying by 31_536_000 (seconds per year) yields mm/year. "
                "Range straddles the spec's sigmoid window [P_low=300, P_high=500] "
                "without saturating at either end."
            ),
        },
        years=YEARS, lats=lats, lons=lons,
    )

    print(f"Wrote {here / 'maxtemp_synthetic.nc'}")
    print(f"  temperature K: min={temp.min():.2f} max={temp.max():.2f} mean={temp.mean():.2f}")
    print(f"Wrote {here / 'precip_synthetic.nc'}")
    print(f"  precip raw:    min={precip.min():.3e} max={precip.max():.3e} mean={precip.mean():.3e}")
    print(f"  precip mm/yr:  min={precip.min()*SECONDS_PER_YEAR:.1f} max={precip.max()*SECONDS_PER_YEAR:.1f} mean={precip.mean()*SECONDS_PER_YEAR:.1f}")


if __name__ == "__main__":
    main()

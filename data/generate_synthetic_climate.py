#!/usr/bin/env python3
"""Generate the synthetic ForeverTree climate dataset.

Writes `data/maxtemp_synthetic.nc` and `data/precip_synthetic.nc` with
shape (calendar_year, lat, lon) and a deterministic gradient designed to
exercise the spec's growth equation cleanly:

- **Temperature** varies linearly with latitude (warmer south → cooler
  north) and adds a small year-over-year warming trend, so the parabolic
  temperature impact reaches its peak somewhere in the middle of the
  grid and falls off symmetrically. Range: ~285K to ~320K, centred on
  the spec's optimal 300K.

- **Precipitation** varies linearly with longitude (wetter west → drier
  east), as a physically-honest precipitation flux in `kg m⁻² s⁻¹`.
  After the standard `× 31_536_000` (seconds per year) conversion,
  values span roughly 100–700 mm/year — so the spec's sigmoid
  (`P_low=300, P_high=500`) is active across the grid: some cells
  well below threshold, some well above.

The two gradients are orthogonal (T along lat, P along lon), so a
correctly-implemented model should produce a clean diagonal pattern in
mean tree height at year 10: tallest where T≈300K AND P≫P_high (centre
to upper-west), shortest where T is at the extremes AND P<P_low
(southern and northern edges of the dry east).

The script is deterministic (fixed seed) — running it twice produces
byte-identical netCDFs. Re-generate inside the fortree image:

    docker run --rm -v $(pwd):/repo -w /repo fortree:agent \\
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

# Year range matches the experiment's 2024–2034 window plus headroom.
YEARS = list(range(2024, 2055))  # 31 years, same length as the Tulare file

SEED = 42


def build_temperature(years: list[int], lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Latitude-driven temperature with a small warming trend.

    Range: ~285K (cold north) to ~320K (warm south), drifting ~+5K from
    2024 to 2054.
    """
    rng = np.random.default_rng(SEED)
    lat_grid, _ = np.meshgrid(lats, lons, indexing="ij")
    # Fraction along latitude axis: 0 at the northern edge, 1 at the southern.
    lat_frac = (LAT_MAX - lat_grid) / (LAT_MAX - LAT_MIN)

    out = np.empty((len(years), N_LAT, N_LON), dtype=np.float64)
    for i, year in enumerate(years):
        base = 285.0 + 30.0 * lat_frac          # 285K north, 315K south
        warming = 0.15 * (year - years[0])      # +4.5K over 30 years
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

    Target mm/year range is 100–700, putting the sigmoid threshold band
    (P_low=300 → P_high=500) right in the middle of the grid.
    """
    rng = np.random.default_rng(SEED + 1)
    _, lon_grid = np.meshgrid(lats, lons, indexing="ij")
    # Fraction along longitude axis: 1 at the western edge, 0 at the eastern.
    lon_frac = (LON_MAX - lon_grid) / (LON_MAX - LON_MIN)

    out = np.empty((len(years), N_LAT, N_LON), dtype=np.float64)
    for i, year in enumerate(years):
        # Build in mm/year, then convert to kg/m²/s by dividing by seconds-per-year.
        base_mm_yr = 100.0 + 600.0 * lon_frac   # 100mm east → 700mm west
        interannual = rng.normal(0.0, 40.0, (N_LAT, N_LON))
        mm_yr = np.clip(base_mm_yr + interannual, 20.0, 850.0)
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
                "Synthetic. Linear south-to-north latitude gradient (285K → 315K) "
                "plus a small +0.15 K/year warming trend and ±0.4 K Gaussian noise. "
                "No NaN cells."
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
                "Synthetic. Linear west-to-east longitude gradient (~700 mm/yr west, "
                "~100 mm/yr east) with ±40 mm/yr interannual noise. Values are a true "
                "precipitation flux in `kg m⁻² s⁻¹`; multiplying by 31_536_000 "
                "(seconds per year) yields mm/year."
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

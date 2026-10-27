#!/usr/bin/env python3
"""Generate the synthetic climate forcing for the fire/births-deaths ForeverTree model.

This is an **independent** dataset for a different consumer than this repo's existing
LLM-coding-benchmark harness. It has nothing to do with `data/generate_synthetic_climate.py`
(phase-6 growth-equation benchmark inputs, scored by `data/reference_sim.py` /
`containers/entrypoint-scorer.sh`) — that generator, its outputs, and the benchmark pipeline are
untouched by this script. This dataset is built for an external `joshpy`/`sbi` project's second
ForeverTree model (births, deaths, recruitment, fuel, fire) and is meant to be copied into that
project once generated.

Writes four NetCDF files under `data/fire_climate/`:

- `historical.nc`  — 30 years, stationary present (no trend).
- `normal.nc`      — 40 years, continues historical's statistics (no trend).
- `hot_dry.nc`     — 40 years, ramps linearly to -30% precipitation / +2 K by year 40.
- `cool_wet.nc`    — 40 years, ramps linearly to +20% precipitation / -1 K by year 40.

Grid: the same bbox as the existing generator (top-left 36.73 N, -119.52 E; bottom-right
35.80 N, -117.98 E), at 10 lon x 8 lat cells — the literal baseline resolution the consuming
project's spec calls for. All four files share the identical spatial grid and baseline gradient,
so patch identity (which cells are wet/dry) is comparable across scenarios; only the temporal
statistics (ramps) differ.

Variables, each `(time, lat, lon)`:

- `temperature`: annual mean, Kelvin. Spatial baseline is a *mild* latitude gradient centered
  near the model's growth optimum (294 K north -> 306 K south at year 0), not a wide gradient --
  this model's growth response is quadratic over 270-330 K and peaks at 300 K, so the climate
  itself stays close to that peak. Local interannual noise only (sd 0.4 K); no regional
  component (none was specified for temperature).
- `precipitation`: annual total, **millimeters directly** (no kg m-2 s-1 flux conversion -- the
  consuming model reads precipitation in mm). Spatial baseline is a linear west-wet/east-dry
  gradient, 550 mm (west) -> 240 mm (east), reusing the orientation of the existing generator.
  This straddles the model's 300-500 mm "dryness" band (d = clip((500-P)/200, 0, 1)) with margin
  on both sides, so roughly half the grid can support trees.

Year-to-year variation (precipitation) is split into a *regional* component -- one shared
Gaussian draw per year, applied identically to every cell, representing landscape-wide wet/dry
years that drive recruitment pulses -- plus a smaller *local* per-cell, per-year Gaussian draw.
Magnitudes (regional sd 30 mm, local sd 25 mm) are calibrated so the combined per-cell sd across
years is ~39 mm (~10%), matching the reference magnitude in the consuming project's spec; the
regional/local split itself is this script's own calibrated assumption (documented here since no
split was specified upstream).

Ramps (`hot_dry`/`cool_wet` only) multiply the precipitation baseline+noise by a linearly
increasing factor (0.7x or 1.2x by year 40) and add a linearly increasing temperature offset
(+2 K or -1 K by year 40), both starting at year 0 (no change) -- applied on top of the same
spatial baseline and noise process as `historical`/`normal`.

**Format**: written with `engine="scipy"` (NETCDF3_CLASSIC), since the consuming project reads
and writes these files with `scipy.io.netcdf_file`, which does not support the HDF5-backed
NetCDF4 format `xarray.to_netcdf` writes by default.

Seeded (`BASE_SEED = 100`, one offset per scenario) and documented above so the magnitudes and
formulas used can be cited directly when this dataset is described elsewhere.

Regenerate inside the fortree image (host Python has no numpy/xarray/scipy):

    docker run --rm -v $(pwd):/repo -w /repo fortree-agent:latest \\
        python data/generate_fire_climate.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

# Same bbox as data/generate_synthetic_climate.py (same ForeverTree demo location).
LAT_MIN, LAT_MAX = 35.80, 36.73
LON_MIN, LON_MAX = -119.52, -117.98

# Literal baseline resolution from the consuming project's spec: 10 lon x 8 lat.
N_LON = 10
N_LAT = 8

HISTORICAL_YEARS = 30
FUTURE_YEARS = 40

BASE_SEED = 100

PRECIP_REGIONAL_SD = 30.0  # mm/yr, shared across the whole grid each year
PRECIP_LOCAL_SD = 25.0     # mm/yr, independent per cell each year
PRECIP_CLIP = (50.0, 900.0)

TEMP_LOCAL_SD = 0.4        # K, independent per cell each year

SCENARIOS = {
    "historical": dict(n_years=HISTORICAL_YEARS, precip_ramp=0.0, temp_ramp=0.0, seed_offset=0),
    "normal": dict(n_years=FUTURE_YEARS, precip_ramp=0.0, temp_ramp=0.0, seed_offset=1),
    "hot_dry": dict(n_years=FUTURE_YEARS, precip_ramp=-0.30, temp_ramp=2.0, seed_offset=2),
    "cool_wet": dict(n_years=FUTURE_YEARS, precip_ramp=0.20, temp_ramp=-1.0, seed_offset=3),
}


def _spatial_baselines(lats: np.ndarray, lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (precip_base, temp_base), each shape (N_LAT, N_LON)."""
    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")

    # 0 at the northern edge, 1 at the southern edge.
    lat_frac = (LAT_MAX - lat_grid) / (LAT_MAX - LAT_MIN)
    # 1 at the western edge, 0 at the eastern edge.
    lon_frac = (LON_MAX - lon_grid) / (LON_MAX - LON_MIN)

    precip_base = 240.0 + 310.0 * lon_frac   # 240mm east -> 550mm west
    temp_base = 294.0 + 12.0 * lat_frac       # 294K north -> 306K south (mild, near 300K optimum)
    return precip_base, temp_base


def build_scenario(
    n_years: int,
    precip_ramp: float,
    temp_ramp: float,
    seed_offset: int,
    precip_base: np.ndarray,
    temp_base: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (precip_mm[n_years, N_LAT, N_LON], temp_k[n_years, N_LAT, N_LON]).

    `precip_ramp`/`temp_ramp` are the total fractional/additive change reached at the last
    year (year index n_years - 1); 0.0 means no ramp (historical/normal).
    """
    rng = np.random.default_rng(BASE_SEED + seed_offset)

    precip = np.empty((n_years, N_LAT, N_LON), dtype=np.float64)
    temp = np.empty((n_years, N_LAT, N_LON), dtype=np.float64)

    last_index = max(n_years - 1, 1)
    for year in range(n_years):
        frac = year / last_index if precip_ramp or temp_ramp else 0.0
        precip_scale = 1.0 + precip_ramp * frac
        temp_shift = temp_ramp * frac

        regional = rng.normal(0.0, PRECIP_REGIONAL_SD)
        local = rng.normal(0.0, PRECIP_LOCAL_SD, (N_LAT, N_LON))
        year_precip = (precip_base + regional + local) * precip_scale
        precip[year] = np.clip(year_precip, *PRECIP_CLIP)

        temp_noise = rng.normal(0.0, TEMP_LOCAL_SD, (N_LAT, N_LON))
        temp[year] = temp_base + temp_noise + temp_shift

    return precip, temp


def write_netcdf(out_path: Path, precip: np.ndarray, temp: np.ndarray,
                  lats: np.ndarray, lons: np.ndarray, scenario: str) -> None:
    n_years = precip.shape[0]
    ds = xr.Dataset(
        data_vars={
            "precipitation": (
                ["time", "lat", "lon"],
                precip.astype(np.float32),
                {
                    "long_name": "Annual total precipitation",
                    "units": "mm",
                    "cell_methods": "time: sum",
                },
            ),
            "temperature": (
                ["time", "lat", "lon"],
                temp.astype(np.float32),
                {
                    "long_name": "Annual mean air temperature",
                    "units": "K",
                    "cell_methods": "time: mean",
                },
            ),
        },
        coords={
            "time": (
                "time",
                np.arange(n_years, dtype=np.int32),
                {"long_name": "Year index (0-based, within this scenario file)"},
            ),
            "lat": (
                "lat",
                lats.astype(np.float32),
                {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"},
            ),
            "lon": (
                "lon",
                lons.astype(np.float32),
                {"standard_name": "longitude", "units": "degrees_east", "axis": "X"},
            ),
        },
        attrs={
            "title": f"Synthetic fire-model ForeverTree climate forcing: {scenario}",
            "summary": (
                "Synthetic climate forcing for the fire/births-deaths ForeverTree model. "
                "Reproducible from data/generate_fire_climate.py."
            ),
            "scenario": scenario,
            "source": "synthetic -- data/generate_fire_climate.py",
            "seed": BASE_SEED + SCENARIOS[scenario]["seed_offset"],
            "regeneration_cmd": (
                "docker run --rm -v $(pwd):/repo -w /repo fortree-agent:latest "
                "python data/generate_fire_climate.py"
            ),
        },
    )
    encoding = {name: {"_FillValue": None} for name in ("lat", "lon", "time")}
    ds.to_netcdf(out_path, engine="scipy", format="NETCDF3_CLASSIC", encoding=encoding)


def _check_scipy_readable(path: Path) -> None:
    from scipy.io import netcdf_file

    with netcdf_file(path, "r", mmap=False) as nc:
        assert "precipitation" in nc.variables, f"{path}: missing precipitation"
        assert "temperature" in nc.variables, f"{path}: missing temperature"


def main() -> None:
    here = Path(__file__).resolve().parent
    out_dir = here / "fire_climate"
    out_dir.mkdir(exist_ok=True)

    lats = np.linspace(LAT_MIN, LAT_MAX, N_LAT)
    lons = np.linspace(LON_MIN, LON_MAX, N_LON)
    precip_base, temp_base = _spatial_baselines(lats, lons)

    in_band_fracs = []

    for scenario, cfg in SCENARIOS.items():
        precip, temp = build_scenario(
            n_years=cfg["n_years"],
            precip_ramp=cfg["precip_ramp"],
            temp_ramp=cfg["temp_ramp"],
            seed_offset=cfg["seed_offset"],
            precip_base=precip_base,
            temp_base=temp_base,
        )

        assert np.isfinite(precip).all(), f"{scenario}: non-finite precipitation"
        assert np.isfinite(temp).all(), f"{scenario}: non-finite temperature"
        assert precip.min() >= PRECIP_CLIP[0] and precip.max() <= PRECIP_CLIP[1], (
            f"{scenario}: precipitation out of clip range"
        )
        assert 250.0 <= temp.min() and temp.max() <= 350.0, (
            f"{scenario}: temperature out of sane range ({temp.min():.1f}-{temp.max():.1f} K)"
        )

        if cfg["precip_ramp"] or cfg["temp_ramp"]:
            year0_precip_mean = precip[0].mean()
            yearN_precip_mean = precip[-1].mean()
            observed_precip_ratio = yearN_precip_mean / year0_precip_mean
            target_precip_ratio = 1.0 + cfg["precip_ramp"]
            assert abs(observed_precip_ratio - target_precip_ratio) < 0.15, (
                f"{scenario}: precip ramp endpoint {observed_precip_ratio:.3f} far from "
                f"target {target_precip_ratio:.3f}"
            )

            year0_temp_mean = temp[0].mean()
            yearN_temp_mean = temp[-1].mean()
            observed_temp_shift = yearN_temp_mean - year0_temp_mean
            assert abs(observed_temp_shift - cfg["temp_ramp"]) < 1.0, (
                f"{scenario}: temp ramp endpoint shift {observed_temp_shift:.2f} far from "
                f"target {cfg['temp_ramp']:.2f}"
            )

        in_band = ((precip_base >= 300.0) & (precip_base <= 500.0)).mean()
        in_band_fracs.append(in_band)

        out_path = out_dir / f"{scenario}.nc"
        write_netcdf(out_path, precip, temp, lats, lons, scenario)
        _check_scipy_readable(out_path)

        print(
            f"Wrote {out_path} ({cfg['n_years']} yr): "
            f"precip mm/yr min={precip.min():.1f} max={precip.max():.1f} mean={precip.mean():.1f}; "
            f"temp K min={temp.min():.1f} max={temp.max():.1f} mean={temp.mean():.1f}"
        )

    band_frac = in_band_fracs[0]
    assert 0.2 <= band_frac <= 0.8, (
        f"baseline dryness band contains {band_frac:.0%} of cells (expected roughly half)"
    )
    print(f"Baseline cells within the 300-500mm dryness band: {band_frac:.0%}")


if __name__ == "__main__":
    main()

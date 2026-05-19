#!/usr/bin/env python3
"""Validate the synthetic ForeverTree climate netCDFs.

Run after `data/generate_synthetic_climate.py` (or any time you want to
confirm the committed data files match the generator's contract).
Exits 0 if every check passes, 1 on the first failed assertion.

Checks:
- Both files exist where the generator wrote them.
- sha256 matches the snapshots below — a tripwire that catches silent
  drift (e.g. generator edited without re-snapping, or a numpy/xarray
  version change altering the netCDF output even when array values
  are unchanged).
- Shape, coordinate ranges, variable names, and units match the
  generator's documented contract.
- Gradients run in the documented directions: south warmer than north
  (T), west wetter than east (P), warming trend across years (T).
- No NaN/inf cells; values fall in physically sensible bands.
- Spec-growth implication: the dataset would produce a non-degenerate
  Δh distribution under the spec's growth equation.
- **CF-1.8 conformance via IOOS `compliance-checker`** — asserts the
  netCDFs meet the Climate and Forecast Conventions (standard_name,
  units, axis attributes on coordinates, Conventions global, etc.).
  `compliance-checker` is pinned in `config/requirements.txt` and
  baked into `fortree:agent`, so this check runs automatically. If the
  package is somehow missing (e.g. running against a stripped Python
  env outside the image), the CF section prints a clearly-marked ⚠
  skip and the structural checks still complete.

The CF check is the "off-the-shelf geospatial validation" — it
guarantees the netCDFs would load cleanly into any CF-aware tool
(QGIS, Panoply, cdo, ncks, downstream xarray with decode_cf=True).

Run:

    docker run --rm -v $(pwd):/repo -w /repo fortree:agent \\
        python data/validate_synthetic_climate.py

Re-snap the sha256s when the generator changes intentionally:

    docker run --rm -v $(pwd):/repo -w /repo fortree:agent \\
        python data/generate_synthetic_climate.py
    sha256sum data/maxtemp_synthetic.nc data/precip_synthetic.nc
    # paste new hashes into EXPECTED below
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import xarray as xr

HERE = Path(__file__).resolve().parent

EXPECTED = {
    "maxtemp": {
        "path": HERE / "maxtemp_synthetic.nc",
        "variable": "tasmax",
        "units": "K",
        "sha256": "5408cee966ca4dab5fd73e898d333460774ad91a7528329e3e24f971cb4ded5b",
        "value_min": 280.0,           # K
        "value_max": 325.0,           # K
        "mean_in": (300.0, 305.0),    # K
    },
    "precip": {
        "path": HERE / "precip_synthetic.nc",
        "variable": "pr",
        "units": "kg m-2 s-1",
        "sha256": "414b4d33ca8f4ca498eea71fd30d12e43c50f9c45543aa939de341eaf672333c",
        # Precipitation checks are in mm/year (raw × 86_400) for legibility.
        "value_min": 10.0,            # mm/yr
        "value_max": 900.0,           # mm/yr
        "mean_in": (350.0, 450.0),    # mm/yr
    },
}

EXPECTED_DIMS = {"calendar_year": 31, "lat": 31, "lon": 50}
LAT_BBOX = (35.80, 36.73)
LON_BBOX = (-119.52, -117.98)
YEAR_RANGE = (2024, 2054)


class CheckFailed(Exception):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  ✓ {label}")
    else:
        raise CheckFailed(label)


def validate_file_existence_and_sha(spec: dict) -> None:
    print(f"\n=== {spec['path'].name} ===")
    check(spec["path"].is_file(), f"file present at {spec['path'].relative_to(HERE.parent)}")
    actual = _sha256(spec["path"])
    check(
        actual == spec["sha256"],
        f"sha256 {actual[:12]}… matches snapshot",
    )


def validate_dims_and_coords(ds: xr.Dataset, label: str) -> None:
    for dim, n in EXPECTED_DIMS.items():
        check(ds.sizes.get(dim) == n, f"{label} dim {dim} == {n}")
    check(
        abs(float(ds.lat.min()) - LAT_BBOX[0]) < 0.01,
        f"{label} lat starts ≈ {LAT_BBOX[0]} (got {float(ds.lat.min()):.3f})",
    )
    check(
        abs(float(ds.lat.max()) - LAT_BBOX[1]) < 0.01,
        f"{label} lat ends ≈ {LAT_BBOX[1]} (got {float(ds.lat.max()):.3f})",
    )
    check(
        abs(float(ds.lon.min()) - LON_BBOX[0]) < 0.01,
        f"{label} lon starts ≈ {LON_BBOX[0]} (got {float(ds.lon.min()):.3f})",
    )
    check(
        abs(float(ds.lon.max()) - LON_BBOX[1]) < 0.01,
        f"{label} lon ends ≈ {LON_BBOX[1]} (got {float(ds.lon.max()):.3f})",
    )
    check(
        int(ds.calendar_year.min()) == YEAR_RANGE[0],
        f"{label} calendar_year starts at {YEAR_RANGE[0]}",
    )
    check(
        int(ds.calendar_year.max()) == YEAR_RANGE[1],
        f"{label} calendar_year ends at {YEAR_RANGE[1]}",
    )


def validate_temperature() -> None:
    spec = EXPECTED["maxtemp"]
    validate_file_existence_and_sha(spec)
    ds = xr.open_dataset(spec["path"])
    validate_dims_and_coords(ds, label=spec["path"].name)
    check(spec["variable"] in ds, f"variable {spec['variable']!r} present")
    arr = ds[spec["variable"]]
    check(arr.attrs.get("units") == spec["units"], f"units == {spec['units']!r}")
    values = arr.values
    check(np.isfinite(values).all(), "all values finite (no NaN/inf)")
    check(
        values.min() >= spec["value_min"],
        f"min {values.min():.2f} K ≥ {spec['value_min']}",
    )
    check(
        values.max() <= spec["value_max"],
        f"max {values.max():.2f} K ≤ {spec['value_max']}",
    )
    mlo, mhi = spec["mean_in"]
    check(
        mlo <= values.mean() <= mhi,
        f"mean {values.mean():.2f} K in [{mlo}, {mhi}]",
    )
    # Generator orients lat ascending (south → north), so lat-index 0 is south.
    south_mean = float(arr.isel(lat=0).mean())
    north_mean = float(arr.isel(lat=-1).mean())
    check(
        south_mean > north_mean + 10.0,
        f"south warmer than north by ≥ 10 K (south={south_mean:.1f}, north={north_mean:.1f})",
    )
    early_mean = float(arr.isel(calendar_year=0).mean())
    late_mean = float(arr.isel(calendar_year=-1).mean())
    check(
        late_mean > early_mean,
        f"warming trend present (year[0]={early_mean:.1f}, year[-1]={late_mean:.1f}, Δ={late_mean - early_mean:+.1f} K)",
    )


def validate_precipitation() -> None:
    spec = EXPECTED["precip"]
    validate_file_existence_and_sha(spec)
    ds = xr.open_dataset(spec["path"])
    validate_dims_and_coords(ds, label=spec["path"].name)
    check(spec["variable"] in ds, f"variable {spec['variable']!r} present")
    arr = ds[spec["variable"]]
    check(arr.attrs.get("units") == spec["units"], f"units == {spec['units']!r}")
    values = arr.values
    check(np.isfinite(values).all(), "all values finite (no NaN/inf)")
    mm_yr = values * 86_400  # raw kg/m²/s → mm/year
    check(
        mm_yr.min() >= spec["value_min"],
        f"min {mm_yr.min():.1f} mm/yr ≥ {spec['value_min']}",
    )
    check(
        mm_yr.max() <= spec["value_max"],
        f"max {mm_yr.max():.1f} mm/yr ≤ {spec['value_max']}",
    )
    mlo, mhi = spec["mean_in"]
    check(
        mlo <= mm_yr.mean() <= mhi,
        f"mean {mm_yr.mean():.1f} mm/yr in [{mlo}, {mhi}]",
    )
    # Generator orients lon ascending (west → east), so lon-index 0 is west.
    west_mean = float(arr.isel(lon=0).mean()) * 86_400
    east_mean = float(arr.isel(lon=-1).mean()) * 86_400
    check(
        west_mean > east_mean + 200.0,
        f"west wetter than east by ≥ 200 mm/yr (west={west_mean:.0f}, east={east_mean:.0f})",
    )


def validate_growth_implication() -> None:
    """Sanity-check that the spec's growth equation produces a non-degenerate
    distribution over this grid — i.e. some cells grow, some don't, and the
    range spans roughly [0, Δh_max]. If this check fails, the dataset has
    drifted out of the active sigmoid / parabolic band and the experiment
    won't probe LLM behaviour meaningfully.
    """
    print(f"\n=== spec-growth implication ===")
    t = xr.open_dataset(EXPECTED["maxtemp"]["path"])[
        EXPECTED["maxtemp"]["variable"]
    ].isel(calendar_year=0).values
    p_raw = xr.open_dataset(EXPECTED["precip"]["path"])[
        EXPECTED["precip"]["variable"]
    ].isel(calendar_year=0).values
    p = p_raw * 86_400

    x_T = np.clip((t - 270.0) / (330.0 - 270.0), 0.0, 1.0)
    pct_T = 4 * x_T * (1 - x_T)
    x_P = (p - 300.0) / (500.0 - 300.0)
    pct_P = 1.0 / (1.0 + np.exp(-12.0 * (x_P - 0.5)))
    dh_year1 = pct_T * pct_P  # Δh_max = 1, stochasticity omitted

    check(
        0.0 <= dh_year1.min() < 0.05,
        f"some cells unfavourable (year-1 Δh min = {dh_year1.min():.4f})",
    )
    check(
        dh_year1.max() > 0.5,
        f"some cells highly favourable (year-1 Δh max = {dh_year1.max():.4f})",
    )
    check(
        0.2 < dh_year1.mean() < 0.7,
        f"mean year-1 Δh = {dh_year1.mean():.3f} ∈ (0.2, 0.7) — gradient is informative",
    )
    frac_active = ((dh_year1 > 0.05) & (dh_year1 < 0.95)).mean()
    check(
        frac_active > 0.15,
        f"≥ 15% of cells fall in the active growth band [0.05, 0.95]: {frac_active*100:.1f}%",
    )


def validate_cf_conformance() -> None:
    """Run the IOOS CF-1.8 compliance checker against both netCDFs.

    Skipped (with a clearly-marked note) if `compliance_checker` isn't
    installed, so the structural checks above still execute on a bare
    fortree:agent. Treats CF errors as hard failures; CF warnings as
    informational.
    """
    print(f"\n=== CF-1.8 compliance (IOOS compliance-checker) ===")
    try:
        from compliance_checker.runner import CheckSuite, ComplianceChecker
    except ImportError:
        print(
            "  ⚠ compliance_checker not installed — skipping CF check.\n"
            "    Install with: pip install 'compliance-checker>=5.0'"
        )
        return

    import os
    import tempfile

    suite = CheckSuite()
    suite.load_all_available_checkers()
    for spec in EXPECTED.values():
        path = str(spec["path"])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            return_value, errors = ComplianceChecker.run_checker(
                ds_loc=path,
                checker_names=["cf:1.8"],
                verbose=0,
                criteria="normal",
                output_format="json",
                output_filename=tmp_path,
            )
        finally:
            os.unlink(tmp_path)
        # return_value is True if all tests pass at the given criteria level.
        check(return_value and not errors,
              f"{spec['path'].name} passes cf:1.8 (criteria=normal)")


def main() -> int:
    try:
        validate_temperature()
        validate_precipitation()
        validate_growth_implication()
        validate_cf_conformance()
    except CheckFailed as exc:
        print(f"\n✗ FAILED: {exc}", file=sys.stderr)
        return 1
    print("\n✓ All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

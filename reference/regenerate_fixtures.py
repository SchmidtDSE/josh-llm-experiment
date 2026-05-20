"""Regenerate the deterministic scorer-fixture CSVs under reference/.

Each fixture exercises a specific scorer outcome — see
.github/scripts/smoke-fixtures.sh for the expectations table. Every
CSV is derived from a single run of the spec-faithful Python reference
simulator [data/reference_sim.py](../data/reference_sim.py), so the
golden fixtures are real-physics simulations against the committed
synthetic climate, not hand-crafted curves. Broken fixtures are
programmatic perturbations of the golden.

Run from the repo root (needs the netCDFs from
`data/generate_synthetic_climate.py` already in place):

    docker run --rm -v $(pwd):/repo -w /repo fortree:scorer \\
        python reference/regenerate_fixtures.py

9 cells × 100 years × 1 replicate = 900 rows per healthy fixture. The
9 cells are sampled across the climate gradient (3 lat strata × 3 lon
strata picked from the 31×50 full grid) so the regression test in
[harness/validators/acceptance.py](../harness/validators/acceptance.py)
has real spatial variation to fit against.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "data"))

from reference_sim import simulate  # noqa: E402

REFERENCE = REPO_ROOT / "reference"

START_YEAR = 2024
END_YEAR = 2123
N_REPLICATES = 1
SEED = 7  # different from data/reference_sim.py's 0, so fixture noise is independent of the bands' reference.

# 3 lat strata × 3 lon strata picked from the 31×50 climate grid so the
# 9 fixture cells span the full gradient — N-S in lat, W-E in lon.
LAT_INDICES = [3, 15, 27]
LON_INDICES = [5, 24, 44]

GOLDEN_COLS = [
    "cell_id", "lat", "lon", "year", "nTrees",
    "meanAge", "meanHeight", "temperature", "precipitation",
]
JOSH_NATIVE_COLS = [
    "meanAge", "position.longitude", "precipitation", "position.x", "position.y",
    "year", "position.latitude", "temperature", "nTrees", "meanHeight",
    "step", "replicate",
]


def _build_cell_mask(n_lat: int = 31, n_lon: int = 50) -> np.ndarray:
    mask = np.zeros((n_lat, n_lon), dtype=bool)
    for i in LAT_INDICES:
        for j in LON_INDICES:
            mask[i, j] = True
    return mask


def _format_row(r: dict) -> dict:
    """Convert numeric values to compact string representations for CSV stability."""
    return {
        "cell_id": r["cell_id"],
        "lat": f"{r['lat']:.4f}",
        "lon": f"{r['lon']:.4f}",
        "year": r["year"],
        "nTrees": r["nTrees"],
        "meanAge": f"{r['meanAge']:.1f}",
        "meanHeight": f"{r['meanHeight']:.4f}",
        "temperature": f"{r['temperature']:.4f}",
        "precipitation": f"{r['precipitation']:.4f}",
    }


def _format_row_josh(r: dict, step: int) -> dict:
    """Josh's native column shape: position.x/y (1-indexed), step + replicate."""
    return {
        "meanAge": f"{r['meanAge']:.1f}",
        "position.longitude": f"{r['lon']:.4f}",
        "precipitation": f"{r['precipitation']:.4f}",
        "position.x": f"{(r['lon'] - (-119.52)) / (119.52 - 117.98):.4f}",
        "position.y": f"{(36.73 - r['lat']) / (36.73 - 35.80):.4f}",
        "year": r["year"],
        "position.latitude": f"{r['lat']:.4f}",
        "temperature": f"{r['temperature']:.4f}",
        "nTrees": r["nTrees"],
        "meanHeight": f"{r['meanHeight']:.4f}",
        "step": step,
        "replicate": r["replicate"],
    }


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row[c]) for c in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def regenerate() -> None:
    print(f"▶ Running reference sim on 9-cell subgrid, {N_REPLICATES} replicate, "
          f"years {START_YEAR}–{END_YEAR}")
    result = simulate(
        start_year=START_YEAR,
        end_year=END_YEAR,
        n_replicates=N_REPLICATES,
        seed=SEED,
        cell_mask=_build_cell_mask(),
        collect_rows=True,
    )
    raw_rows = result["rows"]
    print(f"  {len(raw_rows)} rows produced")

    # 1. golden — standard schema.
    golden = [_format_row(r) for r in raw_rows]
    _write_csv(REFERENCE / "golden" / "results.csv", golden, GOLDEN_COLS)

    # 2. golden-josh-defaults — Josh's native column shape.
    josh_rows = [
        _format_row_josh(r, step=r["year"] - START_YEAR)
        for r in raw_rows
    ]
    _write_csv(
        REFERENCE / "golden-josh-defaults" / "results.csv",
        josh_rows,
        JOSH_NATIVE_COLS,
    )

    n_rows = len(golden)

    # 3. broken/nan-heights — 15 NaN entries scattered through meanHeight.
    nan_heights = [dict(r) for r in golden]
    nan_idxs = np.linspace(20, n_rows - 20, 15, dtype=int).tolist()
    for k in nan_idxs:
        nan_heights[k]["meanHeight"] = "NaN"
    _write_csv(
        REFERENCE / "broken" / "nan-heights" / "results.csv",
        nan_heights,
        GOLDEN_COLS,
    )

    # 4. broken/nan-precip — 9 NaN entries scattered through precipitation.
    nan_precip = [dict(r) for r in golden]
    nan_idxs2 = np.linspace(50, n_rows - 50, 9, dtype=int).tolist()
    for k in nan_idxs2:
        nan_precip[k]["precipitation"] = "NaN"
    _write_csv(
        REFERENCE / "broken" / "nan-precip" / "results.csv",
        nan_precip,
        GOLDEN_COLS,
    )

    # 5. broken/schema — meanHeight column renamed to height.
    schema_cols = [c if c != "meanHeight" else "height" for c in GOLDEN_COLS]
    schema_rows = [
        {("height" if k == "meanHeight" else k): v for k, v in row.items()}
        for row in golden
    ]
    _write_csv(
        REFERENCE / "broken" / "schema" / "results.csv",
        schema_rows,
        schema_cols,
    )

    # 6. broken/missing-year — final year (END_YEAR) omitted entirely.
    missing_year = [r for r in golden if int(r["year"]) != END_YEAR]
    _write_csv(
        REFERENCE / "broken" / "missing-year" / "results.csv",
        missing_year,
        GOLDEN_COLS,
    )

    print(f"  golden:               {n_rows} rows")
    print(f"  golden-josh-defaults: {len(josh_rows)} rows")
    print(f"  broken/nan-heights:   {n_rows} rows (15 NaN meanHeight)")
    print(f"  broken/nan-precip:    {n_rows} rows (9 NaN precipitation)")
    print(f"  broken/schema:        {n_rows} rows (meanHeight → height)")
    print(f"  broken/missing-year:  {len(missing_year)} rows (year {END_YEAR} omitted)")


if __name__ == "__main__":
    regenerate()

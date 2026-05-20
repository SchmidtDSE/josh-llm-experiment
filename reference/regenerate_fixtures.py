"""Regenerate the deterministic scorer-fixture CSVs under reference/.

Each fixture exercises a specific scorer outcome — see
.github/scripts/smoke-fixtures.sh for the expectations table. This script
materialises every CSV from a single growth model so they are
reproducible from source (and so a future calibration tweak is one
script edit + re-run, not six hand-edited CSVs).

Run from the repo root:

    uv run reference/regenerate_fixtures.py

Layout produced:

    reference/golden/results.csv                  9-cell × 100-year, healthy
    reference/golden-josh-defaults/results.csv    Josh-native columns
    reference/broken/nan-heights/results.csv      15 NaN meanHeight rows
    reference/broken/nan-precip/results.csv       9 NaN precipitation rows
    reference/broken/schema/results.csv           meanHeight column renamed
    reference/broken/missing-year/results.csv     year 2123 row absent

Growth model: logistic, asymptote K = 10 m, midpoint t_mid = 30 yr.
Picked so year-100 mean lands around ~10 m for the spec's stated
~10 m peak under faithful dynamics. The current acceptance band in
acceptance_ranges.json is deliberately loose; tightening it is Open Q #1
in K8S_REFACTOR.md.
"""

from __future__ import annotations

import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE = REPO_ROOT / "reference"

START_YEAR = 2024
N_YEARS = 100  # inclusive of START_YEAR; final year = START_YEAR + N_YEARS - 1 = 2123
GRID_N = 3  # GRID_N × GRID_N = 9 cells
TREES_PER_CELL = 10
TEMP_K = 295.0
PRECIP = 400.0

# Logistic growth: K / (1 + exp(-r * (t - t_mid))).
GROWTH_K = 10.0
GROWTH_R = 0.1
GROWTH_T_MID = 30.0


def _height_at(year_index: int) -> float:
    return GROWTH_K / (1.0 + math.exp(-GROWTH_R * (year_index - GROWTH_T_MID)))


def _cell_anchor(i: int, j: int) -> tuple[float, float, str]:
    """Return (lat, lon, cell_id) for a 3×3 grid centred near Tulare County."""
    lat = 35.95 - 0.01 * i
    lon = -119.26 + 0.01 * j
    return lat, lon, f"{i}_{j}"


def _golden_rows() -> list[dict]:
    rows: list[dict] = []
    for i in range(GRID_N):
        for j in range(GRID_N):
            lat, lon, cell_id = _cell_anchor(i, j)
            for t in range(N_YEARS):
                rows.append({
                    "cell_id": cell_id,
                    "lat": f"{lat:.4f}",
                    "lon": f"{lon:.4f}",
                    "year": START_YEAR + t,
                    "nTrees": TREES_PER_CELL,
                    "meanAge": f"{float(t):.1f}",
                    "meanHeight": f"{_height_at(t):.2f}",
                    "temperature": f"{TEMP_K:.1f}",
                    "precipitation": f"{PRECIP:.1f}",
                })
    return rows


def _josh_native_rows() -> list[dict]:
    """Josh's default export shape — position.x/y instead of cell_id, plus step/replicate."""
    rows: list[dict] = []
    for i in range(GRID_N):
        for j in range(GRID_N):
            pos_x = 0.5 + j
            pos_y = 0.5 + i
            lat, lon, _ = _cell_anchor(i, j)
            for t in range(N_YEARS):
                rows.append({
                    "meanAge": f"{float(t):.1f}",
                    "position.longitude": f"{lon:.4f}",
                    "precipitation": f"{PRECIP:.1f}",
                    "position.x": f"{pos_x:.1f}",
                    "position.y": f"{pos_y:.1f}",
                    "year": START_YEAR + t,
                    "position.latitude": f"{lat:.4f}",
                    "temperature": f"{TEMP_K:.1f}",
                    "nTrees": TREES_PER_CELL,
                    "meanHeight": f"{_height_at(t):.2f}",
                    "step": t,
                    "replicate": 0,
                })
    return rows


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = [",".join(columns)]
    for row in rows:
        out.append(",".join(str(row[c]) for c in columns))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


GOLDEN_COLS = [
    "cell_id", "lat", "lon", "year", "nTrees",
    "meanAge", "meanHeight", "temperature", "precipitation",
]
JOSH_NATIVE_COLS = [
    "meanAge", "position.longitude", "precipitation", "position.x", "position.y",
    "year", "position.latitude", "temperature", "nTrees", "meanHeight",
    "step", "replicate",
]


def regenerate() -> None:
    golden = _golden_rows()
    josh_native = _josh_native_rows()

    # 1. golden — healthy fixture.
    _write_csv(REFERENCE / "golden" / "results.csv", golden, GOLDEN_COLS)

    # 2. golden-josh-defaults — Josh's native column shape.
    _write_csv(
        REFERENCE / "golden-josh-defaults" / "results.csv",
        josh_native,
        JOSH_NATIVE_COLS,
    )

    # 3. broken/nan-heights — 15 NaN entries scattered through meanHeight.
    nan_heights = [dict(r) for r in golden]
    # Pick 15 rows distributed across cells / years.
    nan_idxs = [37, 81, 144, 199, 256, 311, 368, 422, 477, 531, 588, 643, 699, 754, 810]
    for k in nan_idxs:
        nan_heights[k]["meanHeight"] = "NaN"
    _write_csv(
        REFERENCE / "broken" / "nan-heights" / "results.csv",
        nan_heights,
        GOLDEN_COLS,
    )

    # 4. broken/nan-precip — 9 NaN entries scattered through precipitation.
    nan_precip = [dict(r) for r in golden]
    nan_idxs2 = [50, 150, 250, 350, 450, 550, 650, 750, 850]
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

    # 6. broken/missing-year — final year (2123) omitted entirely.
    final_year = START_YEAR + N_YEARS - 1
    missing_year = [r for r in golden if int(r["year"]) != final_year]
    _write_csv(
        REFERENCE / "broken" / "missing-year" / "results.csv",
        missing_year,
        GOLDEN_COLS,
    )

    print(f"Wrote 6 fixtures under {REFERENCE}/")
    print(f"  rows per healthy fixture: {len(golden)}")
    print(f"  missing-year row count:   {len(missing_year)}")


if __name__ == "__main__":
    regenerate()

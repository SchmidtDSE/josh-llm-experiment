#!/usr/bin/env bash
# Golden fixture: emits a schema-valid CSV with hand-picked values inside
# the v0 height_year10 acceptance range. Used by 2b-fixtures to exercise
# the scorer end-to-end without an LLM in the loop.
#
# Grid: 3×3, years 2024–2034 → 99 rows. Heights grow linearly 0 → 6 m
# (well within [0, 11] m). Temperature and precipitation are constants
# picked from the middle of the impact-curve plateau (T_min=270, T_max=330;
# P_low=300, P_high=500 per BASE_PROMPT.md).
set -euo pipefail
mkdir -p output

python3 <<'PY' > output/results.csv
print("cell_id,lat,lon,year,meanAge,meanHeight,temperature,precipitation")
lats = [35.95, 36.26, 36.58]
lons = [-119.26, -118.75, -118.24]
for i, lat in enumerate(lats):
    for j, lon in enumerate(lons):
        for year in range(2024, 2035):
            age = year - 2024
            height = age * 0.6
            print(f"{i}_{j},{lat:.4f},{lon:.4f},{year},{age:.1f},{height:.2f},295.0,400.0")
PY

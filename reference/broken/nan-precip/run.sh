#!/usr/bin/env bash
# Broken fixture: NaN in precipitation for a handful of rows. Same
# NaN-as-schema-violation path as nan-heights, but on a different numeric
# column — confirms the check fires column-agnostically.
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
            precip_str = "nan" if (j == 2 and year >= 2032) else "400.0"
            print(f"{i}_{j},{lat:.4f},{lon:.4f},{year},{age:.1f},{height:.2f},295.0,{precip_str}")
PY

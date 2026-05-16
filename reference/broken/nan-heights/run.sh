#!/usr/bin/env bash
# Broken fixture: NaN in meanHeight for a handful of rows. Per the
# NaN-as-schema-violation rule, this flips csv_schema_ok=false.
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
            height_str = "nan" if (i == 1 and year >= 2030) else f"{age * 0.6:.2f}"
            print(f"{i}_{j},{lat:.4f},{lon:.4f},{year},{age:.1f},{height_str},295.0,400.0")
PY

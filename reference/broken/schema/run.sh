#!/usr/bin/env bash
# Broken fixture: schema mismatch. Renames `meanHeight` → `height`.
# Expected scorer outcome: csv_schema_ok=false with a "columns mismatch"
# entry in csv_schema_errors.
set -euo pipefail
mkdir -p output

python3 <<'PY' > output/results.csv
print("cell_id,lat,lon,year,meanAge,height,temperature,precipitation")
lats = [35.95, 36.26, 36.58]
lons = [-119.26, -118.75, -118.24]
for i, lat in enumerate(lats):
    for j, lon in enumerate(lons):
        for year in range(2024, 2035):
            age = year - 2024
            height = age * 0.6
            print(f"{i}_{j},{lat:.4f},{lon:.4f},{year},{age:.1f},{height:.2f},295.0,400.0")
PY

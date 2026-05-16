#!/usr/bin/env bash
# Broken fixture: omits the target year (2034) entirely. Row count drops
# to 9 × 10 = 90, which mismatches the schema's n_cells × 11 = 99 rule and
# flips csv_schema_ok=false via the row-count check.
set -euo pipefail
mkdir -p output

python3 <<'PY' > output/results.csv
print("cell_id,lat,lon,year,meanAge,meanHeight,temperature,precipitation")
lats = [35.95, 36.26, 36.58]
lons = [-119.26, -118.75, -118.24]
for i, lat in enumerate(lats):
    for j, lon in enumerate(lons):
        for year in range(2024, 2034):  # 2034 excluded
            age = year - 2024
            height = age * 0.6
            print(f"{i}_{j},{lat:.4f},{lon:.4f},{year},{age:.1f},{height:.2f},295.0,400.0")
PY

#!/usr/bin/env bash
# Broken fixture: 9 NaN values in precipitation (cells in col j=2, years 2032+).
# Expected: csv_schema_ok=false via the NaN-as-schema-violation rule on a
# different numeric column than nan-heights.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

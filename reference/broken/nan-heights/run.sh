#!/usr/bin/env bash
# Broken fixture: 15 NaN values in meanHeight (cells in row i=1, years 2030+).
# Expected: csv_schema_ok=false via the NaN-as-schema-violation rule.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

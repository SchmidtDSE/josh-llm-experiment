#!/usr/bin/env bash
# Broken fixture: 2034 rows omitted (9 cells × 10 years = 90 rows total).
# Expected: csv_schema_ok=false via the row-count check.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

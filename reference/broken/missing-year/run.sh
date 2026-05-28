#!/usr/bin/env bash
# Broken fixture: year 2123 (the acceptance target year) is omitted from
# the CSV entirely (891 rows = 9 cells × 99 years). Expected:
# csv_schema_ok=false with a "target year 2123 not in CSV" error.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

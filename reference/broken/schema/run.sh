#!/usr/bin/env bash
# Broken fixture: header renames meanHeight → height.
# Expected scorer outcome: csv_schema_ok=false with a "columns mismatch" entry.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

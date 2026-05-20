#!/usr/bin/env bash
# Broken fixture: 9 NaN values scattered through precipitation across the
# 900-row CSV — a different numeric column from nan-heights. Same
# NaN-tolerance rule: expected csv_schema_ok=true with csv_rows_dropped_nan=9.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

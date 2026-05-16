#!/usr/bin/env bash
# Golden fixture: copies a committed valid CSV into place. The CSV is a
# 3×3 grid × 11 years (99 data rows) with heights inside the v0 acceptance
# range for height_year10.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

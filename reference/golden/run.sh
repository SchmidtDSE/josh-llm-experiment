#!/usr/bin/env bash
# Golden fixture: copies a committed valid CSV into place. The CSV is a
# 3×3 grid × 100 years (900 data rows, 2024..2123) with logistic-growth
# meanHeight values (K=10, r=0.1, t_mid=30) so the year-100 mean lands
# inside the acceptance band for height_year100.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

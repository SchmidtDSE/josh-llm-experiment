#!/usr/bin/env bash
# Golden fixture exercising the Josh-defaults schema acceptance path.
# Same data as reference/golden/ (3×3 grid × 100 years, logistic-growth
# meanHeight saturating near 10 m, occupancy 10/cell) but with Josh's
# native column names: position.x + position.y instead of cell_id,
# position.latitude/longitude instead of lat/lon, plus extra step +
# replicate columns the scorer ignores.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

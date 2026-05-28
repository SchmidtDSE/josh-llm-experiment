#!/usr/bin/env bash
# Broken fixture: 15 NaN values scattered through meanHeight across the
# 900-row CSV. NaN is *not* a schema-gate failure under the phase6 scorer;
# the rows are filtered out and counted in csv_rows_dropped_nan. Expected
# outcome: csv_schema_ok=true with csv_rows_dropped_nan=15.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")/results.csv" output/results.csv

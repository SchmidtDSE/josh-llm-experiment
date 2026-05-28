#!/usr/bin/env bash
# Run fortree:scorer against every fixture under reference/ and assert the
# expected csv_schema_ok value, a substring match against csv_schema_errors[0],
# and (optionally) an exact csv_rows_dropped_nan value. Exits non-zero on the
# first fixture whose actual output disagrees with the expected outcome.
#
# Inputs:
#   $1 (optional)   path to a prebuilt scorer image tag (default: fortree:scorer)
#
# The fixture table below is the source of truth; smoke.yml just sources
# this script. To add a fixture, drop a results.csv + run.sh under
# reference/broken/<name>/ and append a row to FIXTURES.
#
# Under the phase6 schema validator:
# - Schema validation is a gate, not a substantive check. NaN rows are
#   filtered (counted via csv_rows_dropped_nan), not failed. Missing
#   required columns and missing-target-year still fail the gate.
# - Target year is 2123 (year 100 of a 2024-start, 100-year simulation),
#   matching harness/acceptance_ranges.json.
set -euo pipefail

SCORER_IMAGE="${1:-fortree:scorer}"

# entry: <fixture-dir>|<expected csv_schema_ok>|<errors[0] substring or empty>|<expected csv_rows_dropped_nan or empty>
FIXTURES=(
  "reference/golden|true||0"
  "reference/golden-josh-defaults|true||0"
  "reference/golden-per-replicate|true||0"
  "reference/broken/nan-heights|true||15"
  "reference/broken/nan-precip|true||9"
  "reference/broken/schema|false|missing required columns|"
  "reference/broken/missing-year|false|target year 2123|"
)

FAIL=0
for entry in "${FIXTURES[@]}"; do
  IFS='|' read -r FIXTURE EXPECTED_OK EXPECTED_SUBSTR EXPECTED_DROPPED <<< "$entry"

  WORKDIR="$(mktemp -d)"
  cp -r "$FIXTURE"/. "$WORKDIR/"

  echo "::group::$FIXTURE"
  SCORER_JSON="$(docker run --rm --network=none \
    -v "$WORKDIR":/sandbox \
    "$SCORER_IMAGE" /opt/entrypoint-scorer.sh --target mesa)"
  echo "$SCORER_JSON"
  echo "::endgroup::"

  ACTUAL_OK=$(echo "$SCORER_JSON" | jq -r '.csv_schema_ok')
  ACTUAL_ERR0=$(echo "$SCORER_JSON" | jq -r '.csv_schema_errors[0] // ""')
  ACTUAL_DROPPED=$(echo "$SCORER_JSON" | jq -r '.csv_rows_dropped_nan // "null"')

  if [ "$ACTUAL_OK" != "$EXPECTED_OK" ]; then
    echo "✗ $FIXTURE: csv_schema_ok expected=$EXPECTED_OK got=$ACTUAL_OK" >&2
    FAIL=1
    continue
  fi

  if [ -n "$EXPECTED_SUBSTR" ] && [[ "$ACTUAL_ERR0" != *"$EXPECTED_SUBSTR"* ]]; then
    echo "✗ $FIXTURE: csv_schema_errors[0] missing substring" >&2
    echo "    expected substring: $EXPECTED_SUBSTR" >&2
    echo "    actual: $ACTUAL_ERR0" >&2
    FAIL=1
    continue
  fi

  if [ -n "$EXPECTED_DROPPED" ] && [ "$ACTUAL_DROPPED" != "$EXPECTED_DROPPED" ]; then
    echo "✗ $FIXTURE: csv_rows_dropped_nan expected=$EXPECTED_DROPPED got=$ACTUAL_DROPPED" >&2
    FAIL=1
    continue
  fi

  echo "✓ $FIXTURE"
done

exit $FAIL

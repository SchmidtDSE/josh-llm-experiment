#!/usr/bin/env bash
# Run fortree:scorer against every fixture under reference/ and assert the
# expected csv_schema_ok value + a substring match against csv_schema_errors[0].
# Exits non-zero on the first fixture whose actual output disagrees with
# the expected outcome.
#
# Inputs:
#   $1 (optional)   path to a prebuilt scorer image tag (default: fortree:scorer)
#
# The fixture table below is the source of truth; smoke.yml just sources
# this script. To add a fixture, drop a results.csv + run.sh under
# reference/broken/<name>/ and append a row to FIXTURES.
set -euo pipefail

SCORER_IMAGE="${1:-fortree:scorer}"

# entry: <fixture-dir>|<expected csv_schema_ok>|<errors[0] substring or empty for ok>
FIXTURES=(
  "reference/golden|true|"
  "reference/broken/nan-heights|false|meanHeight: 15 NaN values"
  "reference/broken/nan-precip|false|precipitation: 9 NaN values"
  "reference/broken/schema|false|columns mismatch"
  "reference/broken/missing-year|false|row count: expected 99"
)

FAIL=0
for entry in "${FIXTURES[@]}"; do
  IFS='|' read -r FIXTURE EXPECTED_OK EXPECTED_SUBSTR <<< "$entry"

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

  echo "✓ $FIXTURE"
done

exit $FAIL

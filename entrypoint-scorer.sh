#!/usr/bin/env bash
# Scorer-mode entrypoint. Invoked when the image runs with --network=none and
# a read-only workspace mount:
#   docker run --rm --network=none \
#     -v <workspace>:/sandbox -v <results>:/results \
#     fortree:<tag> /opt/entrypoint-scorer.sh --target <josh|mesa> [...]
#
# Phase 2 wires this to harness/run_metrics.py; until then it errors clearly.

set -euo pipefail

if [ ! -f /opt/harness/run_metrics.py ]; then
  echo "entrypoint-scorer: /opt/harness/run_metrics.py not present (phase 2 not complete)" >&2
  exit 64
fi

exec python /opt/harness/run_metrics.py "$@"

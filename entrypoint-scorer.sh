#!/usr/bin/env bash
set -euo pipefail

# Scorer container entrypoint. The orchestrator invokes:
#   docker run --rm --network=none -v <workspace>:/sandbox -v <results>:/results fortree-scorer /opt/entrypoint-scorer.sh
# Phase 2 fills in harness/run_metrics.py and wires it here.

if [ ! -f /opt/harness/run_metrics.py ]; then
  echo "entrypoint-scorer: /opt/harness/run_metrics.py not present (phase 2 not complete)" >&2
  exit 64
fi

exec python /opt/harness/run_metrics.py "$@"

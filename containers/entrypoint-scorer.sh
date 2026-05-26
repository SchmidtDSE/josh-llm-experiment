#!/usr/bin/env bash
# Scorer-mode entrypoint. Lives only in `fortree:scorer` (added by the
# scorer Dockerfile stage). The agent image (`fortree:agent`) does not
# carry this file.
#
# Invocation:
#   docker run --rm --network=none \
#     -v <workspace>:/sandbox \
#     fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa>
#
# All argument parsing happens in run_metrics.py.

set -euo pipefail
exec python /opt/harness/run_metrics.py "$@"

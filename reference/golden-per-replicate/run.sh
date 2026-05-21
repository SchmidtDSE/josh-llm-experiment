#!/usr/bin/env bash
# Golden fixture exercising the per-replicate CSV layout (Josh's
# canonical pattern: `exportFiles.patch =
# "file:///sandbox/output/results_{replicate}.csv"` + `--replicates N`).
# Three committed CSVs (one per replicate, no in-file `replicate`
# column — filename's `{N}` is authoritative); run.sh copies them
# into output/ so the scorer's glob path picks them up. Same growth
# dynamics as reference/golden/, just multi-replicate.
set -euo pipefail
mkdir -p output
cp "$(dirname "$0")"/results_*.csv output/

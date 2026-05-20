#!/usr/bin/env bash
# Run the overnight headline panel: 5 models × 2 targets × N iterations.
# Each iteration is a separate `launch_batch.py --cells ... --upload`
# invocation with a distinct batch tag, so partial failure preserves
# results: if iteration 3 crashes, iterations 1–2 are already on disk
# and mirrored to GCS.
#
# Usage (defaults shown):
#   ITERATIONS=5 JOBS=10 \
#   CELLS=overnight_cells.csv \
#   TAG_PREFIX=overnight-$(date -u +%Y%m%d) \
#     ./scripts/run_overnight.sh
#
# Continues across iteration failures (`|| true` after each
# launch_batch invocation). Inspect runs/<TAG_PREFIX>-i<N>/summary.txt
# afterwards for per-iteration success counts.
#
# DRY_RUN: set DRY_RUN=1 to print each launch_batch invocation without
# executing it. Use this when smoke-testing the script — running it
# under `bash -x` does NOT make it a dry run; the launch_batch calls
# fire for real.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CELLS="${CELLS:-$REPO_ROOT/overnight_cells.csv}"
ITERATIONS="${ITERATIONS:-5}"
JOBS="${JOBS:-10}"
TAG_PREFIX="${TAG_PREFIX:-overnight-$(date -u +%Y%m%d)}"

if [ ! -f "$CELLS" ]; then
  echo "missing cells CSV: $CELLS" >&2
  exit 2
fi

echo "▶ Overnight panel"
echo "  Cells:        $CELLS"
echo "  Iterations:   $ITERATIONS"
echo "  Concurrency:  $JOBS"
echo "  Tag prefix:   $TAG_PREFIX"
echo ""

START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

DRY_RUN="${DRY_RUN:-0}"
if [ "$DRY_RUN" = "1" ]; then
  echo "  (DRY_RUN=1: printing invocations only, not executing)"
  echo ""
fi

for i in $(seq 1 "$ITERATIONS"); do
  batch_tag="${TAG_PREFIX}-i${i}"
  echo ""
  echo "================ Iteration ${i}/${ITERATIONS}: ${batch_tag} ================"
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry] uv run $REPO_ROOT/orchestration/launch_batch.py \\"
    echo "          --cells $CELLS --batch-tag $batch_tag \\"
    echo "          --jobs $JOBS --upload"
    continue
  fi
  uv run "$REPO_ROOT/orchestration/launch_batch.py" \
    --cells "$CELLS" \
    --batch-tag "$batch_tag" \
    --jobs "$JOBS" \
    --upload \
    || echo "  ⚠ Iteration ${i} ended with non-zero exit; continuing"
done

echo ""
echo "✔ All ${ITERATIONS} iterations attempted."
echo "  Started:  $START_TS"
echo "  Finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Per-iteration dirs: runs/${TAG_PREFIX}-i{1..${ITERATIONS}}/"

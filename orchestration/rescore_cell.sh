#!/usr/bin/env bash
# Re-score one cell's preserved workspace against the fortree:scorer
# image. Analog of orchestration/launch_cell.sh Step 2 (the scorer pass)
# in isolation — agent and report rendering are intentionally skipped, so
# the workspace is the only mutable surface.
#
# Called per-cell by orchestration/rescore_batch.py (one invocation per
# cell, parallelised by ThreadPoolExecutor at the Python level).
#
# Required env vars:
#   RUN_DIR           absolute path to runs/<batch-tag>/<run-id>/
#   TARGET            josh | mesa
# Optional:
#   SCORER_FILENAME   output filename inside RUN_DIR
#                     (default: scorer.rescored.json)
#   SCORER_STDERR     stderr filename inside RUN_DIR
#                     (default: scorer.rescored.stderr)
#   IMAGE             scorer image (default: fortree:scorer)
#
# Writes <RUN_DIR>/<SCORER_FILENAME> (the canonical JSON record) and
# <RUN_DIR>/<SCORER_STDERR> (any scorer-process stderr). Originals
# (scorer.json, etc.) are never touched.
#
# Exit codes:
#   0   scorer wrote a non-empty JSON record
#   2   bad invocation (missing env, no workspace/)
#   3   scorer wrote an empty record (docker likely failed mid-run)
#   *   passes through the scorer container's exit code
set -euo pipefail

: "${RUN_DIR:?RUN_DIR not set}"
: "${TARGET:?TARGET not set; must be josh or mesa}"
SCORER_FILENAME="${SCORER_FILENAME:-scorer.rescored.json}"
SCORER_STDERR="${SCORER_STDERR:-scorer.rescored.stderr}"
IMAGE="${IMAGE:-fortree:scorer}"

case "$TARGET" in
  josh|mesa) ;;
  *) echo "TARGET must be josh or mesa, got: $TARGET" >&2; exit 2 ;;
esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -d "$RUN_DIR/workspace" ]; then
  echo "no workspace/ under $RUN_DIR" >&2
  exit 2
fi

# Workspace mounts rw because harness/runner.py re-executes ./run.sh,
# which writes ./output/results.csv. The frozen evidence is the original
# scorer.json next to this output, not the workspace's results dir.
docker run --rm --network=none \
  -v "$RUN_DIR/workspace":/sandbox \
  -v "$REPO_ROOT/data":/sandbox/data:ro \
  "$IMAGE" /opt/entrypoint-scorer.sh --target "$TARGET" \
  > "$RUN_DIR/$SCORER_FILENAME" 2> "$RUN_DIR/$SCORER_STDERR"

if [ ! -s "$RUN_DIR/$SCORER_FILENAME" ]; then
  echo "scorer produced empty output; see $RUN_DIR/$SCORER_STDERR" >&2
  exit 3
fi

#!/usr/bin/env bash
# Run one full experimental cell end-to-end: agent → scorer → report.
#
# This is the unit of work that both CI and the local batch driver
# (orchestration/launch_batch.sh) call. It factors the agent/scorer/
# report sequence that .github/workflows/integration.yml used to inline,
# so what CI validates is the same code path that runs in production.
#
# Each step is tolerant of the previous step's failure: a crashing agent
# must not block scoring whatever workspace it left behind, and a
# scorer failure must not block report rendering (the report is the
# human-readable view that surfaces both the agent's output and the
# scorer's diagnostics).
#
# Required env vars: MODEL, RUNG, TARGET, RUN_ID. Same contract as
# orchestration/launch_run.sh, which this script invokes.
#
# Writes to runs/<RUN_ID>/:
#   - everything launch_run.sh writes (workspace/, dns.log,
#     trajectory.jsonl, agent_artifacts/, ...)
#   - scorer.json       — scorer output, even if the agent failed
#   - report.md         — rendered Markdown report
#   - run_meta.cell.json — per-step status (agent / scorer / report)
#
# Exit codes:
#   0  all three steps succeeded
#   10 agent failed (launch_run.sh exit != 0)
#   11 scorer failed (still emits report.md)
#   12 report failed
# Higher-numbered codes win when multiple steps fail; this lets
# `parallel --joblog` surface the latest failing step. Use the per-step
# fields in run_meta.cell.json for the full picture.
set -euo pipefail

: "${MODEL:?MODEL not set}"
: "${RUNG:?RUNG not set}"
: "${TARGET:?TARGET not set}"
: "${RUN_ID:?RUN_ID not set; use \$(uuidgen)}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$REPO_ROOT/runs/$RUN_ID"

echo "▶ launch_cell.sh: $MODEL rung=$RUNG target=$TARGET run=$RUN_ID"

# --- Step 1: agent ---
AGENT_STATUS="ok"
AGENT_EXIT=0
set +e
MODEL="$MODEL" RUNG="$RUNG" TARGET="$TARGET" RUN_ID="$RUN_ID" \
  "$REPO_ROOT/orchestration/launch_run.sh"
AGENT_EXIT=$?
set -e
if [ "$AGENT_EXIT" -ne 0 ]; then
  AGENT_STATUS="failed"
  echo "⚠ agent step exited $AGENT_EXIT — continuing to scorer with whatever workspace exists"
fi

# launch_run.sh creates RUN_DIR; if it failed before that point, bail.
if [ ! -d "$RUN_DIR" ]; then
  echo "✗ run dir $RUN_DIR does not exist — launch_run.sh failed before workspace setup" >&2
  exit 10
fi

# --- Step 2: scorer ---
# The scorer runs offline (--network=none) against the workspace the
# agent produced, with the read-only data/ dir mounted in. Writes to
# scorer.json directly; on docker failure the file may be empty but
# still exists for the report renderer to find.
SCORER_STATUS="ok"
SCORER_EXIT=0
set +e
docker run --rm --network=none \
  -v "$RUN_DIR/workspace":/sandbox \
  -v "$REPO_ROOT/data":/sandbox/data:ro \
  fortree:scorer /opt/entrypoint-scorer.sh --target "$TARGET" \
  > "$RUN_DIR/scorer.json"
SCORER_EXIT=$?
set -e
if [ "$SCORER_EXIT" -ne 0 ]; then
  SCORER_STATUS="failed"
  echo "⚠ scorer exited $SCORER_EXIT — continuing to report render"
fi

# --- Step 3: report ---
# Runs inside fortree:scorer because that image carries the report's
# pip deps (jinja2 via config/requirements.txt). The host typically
# doesn't have them.
REPORT_STATUS="ok"
REPORT_EXIT=0
set +e
docker run --rm --network=none \
  -v "$RUN_DIR":/run \
  -v "$REPO_ROOT/orchestration":/opt/orchestration:ro \
  fortree:scorer \
  python3 /opt/orchestration/generate_run_report.py /run \
  > "$RUN_DIR/report.md"
REPORT_EXIT=$?
set -e
if [ "$REPORT_EXIT" -ne 0 ]; then
  REPORT_STATUS="failed"
  echo "⚠ report render exited $REPORT_EXIT"
fi

# --- Per-cell status record ---
cat > "$RUN_DIR/run_meta.cell.json" <<META
{
  "run_id": "$RUN_ID",
  "model": "$MODEL",
  "rung": $RUNG,
  "target": "$TARGET",
  "agent": {"status": "$AGENT_STATUS", "exit_code": $AGENT_EXIT},
  "scorer": {"status": "$SCORER_STATUS", "exit_code": $SCORER_EXIT},
  "report": {"status": "$REPORT_STATUS", "exit_code": $REPORT_EXIT}
}
META

echo ""
echo "✔ cell done: agent=$AGENT_STATUS scorer=$SCORER_STATUS report=$REPORT_STATUS"
echo "  Run dir: $RUN_DIR"

# Highest-numbered failing step wins; 0 means all three succeeded.
if [ "$REPORT_EXIT" -ne 0 ]; then exit 12; fi
if [ "$SCORER_EXIT" -ne 0 ]; then exit 11; fi
if [ "$AGENT_EXIT"  -ne 0 ]; then exit 10; fi
exit 0

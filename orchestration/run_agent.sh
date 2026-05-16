#!/usr/bin/env bash
# Run the agent container against a prepared run dir.
#
# Usage: run_agent.sh <RUN_DIR>
#
# Expects the layout produced by launch_run.sh:
#   <RUN_DIR>/workspace/             — bind-mounted at /sandbox (writable)
#   <RUN_DIR>/.opencode/opencode.json — bind-mounted at /root/.config/opencode
#   <RUN_DIR>/prompt.md              — bind-mounted at /opt/prompt.md (ro)
#
# Reads $REPO_ROOT (auto-derived if unset) for `.env` and `data/`,
# $WALL_CLOCK_BACKSTOP_SEC (default 1800) for the agent backstop, and
# $IDLE_THRESHOLD_SEC (default 120) for the trajectory-idle heartbeat.
#
# Writes <RUN_DIR>/trajectory.jsonl (stdout) and <RUN_DIR>/agent_stderr.log
# (stderr). Exits with the agent container's exit code; 124 if the
# wall-clock backstop killed it; 137 if the idle heartbeat killed it.
#
# Two backstops layered:
# - --kill-after=30: SIGKILL fires 30s after SIGTERM in case `docker run`
#   wedges propagating signals to a hung container.
# - Idle watcher: an in-script poller checks <RUN_DIR>/trajectory.jsonl
#   every 10s; if its size hasn't grown for $IDLE_THRESHOLD_SEC, the
#   watcher writes <RUN_DIR>/stream_stalled.flag and `docker kill`s the
#   container. The wall-clock backstop is for total budget; the idle
#   heartbeat catches silent stream stalls (LLM streams that wedge without
#   producing more events but also don't error). See prior incident where
#   claude/rung5/josh sat 24 min mid-stream with no trajectory growth.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <RUN_DIR>" >&2
  exit 2
fi

RUN_DIR="$(realpath "$1")"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
WALL_CLOCK_BACKSTOP_SEC="${WALL_CLOCK_BACKSTOP_SEC:-1800}"
IDLE_THRESHOLD_SEC="${IDLE_THRESHOLD_SEC:-120}"
RUN_ID="$(basename "$RUN_DIR")"
CONTAINER_NAME="fortree-agent-${RUN_ID}"

# Launch the agent in the background so the watcher can run concurrently.
(
  timeout --kill-after=30 "$WALL_CLOCK_BACKSTOP_SEC" docker run --rm \
    --name "$CONTAINER_NAME" \
    --env-file "$REPO_ROOT/.env" \
    -v "$RUN_DIR/workspace":/sandbox \
    -v "$REPO_ROOT/data":/sandbox/data:ro \
    -v "$RUN_DIR/.opencode":/root/.config/opencode \
    -v "$RUN_DIR/prompt.md":/opt/prompt.md:ro \
    fortree:agent \
    bash -c 'opencode run --dir /sandbox --agent coder --format json --print-logs "$(cat /opt/prompt.md)"' \
    > "$RUN_DIR/trajectory.jsonl" \
    2> "$RUN_DIR/agent_stderr.log"
) &
AGENT_PID=$!

# Idle watcher: polls trajectory.jsonl size; if no growth for IDLE_THRESHOLD,
# kills the container. Distinct from the wall-clock backstop — this fires
# fast when the LLM stream wedges silently.
(
  last_size=0
  last_change_ts=$(date +%s)
  while kill -0 "$AGENT_PID" 2>/dev/null; do
    sleep 10
    current_size=$(stat -c %s "$RUN_DIR/trajectory.jsonl" 2>/dev/null || echo 0)
    if [ "$current_size" -ne "$last_size" ]; then
      last_size=$current_size
      last_change_ts=$(date +%s)
    fi
    idle_for=$(( $(date +%s) - last_change_ts ))
    if [ "$idle_for" -ge "$IDLE_THRESHOLD_SEC" ]; then
      printf '[heartbeat] trajectory idle for %ds, killing %s\n' "$idle_for" "$CONTAINER_NAME" >&2
      printf '{"idle_seconds": %d, "killed_at": "%s"}\n' \
        "$idle_for" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$RUN_DIR/stream_stalled.flag"
      docker kill --signal=SIGKILL "$CONTAINER_NAME" 2>/dev/null || true
      exit 0
    fi
  done
) &
WATCHER_PID=$!

# Wait for the agent to finish (either naturally, by backstop, or by watcher kill).
set +e
wait "$AGENT_PID"
AGENT_EXIT=$?
set -e

# Clean up the watcher.
kill "$WATCHER_PID" 2>/dev/null || true
wait "$WATCHER_PID" 2>/dev/null || true

exit "$AGENT_EXIT"

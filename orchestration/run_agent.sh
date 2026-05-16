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
# Reads $REPO_ROOT (auto-derived if unset) for `.env` and `data/`, and
# $WALL_CLOCK_BACKSTOP_SEC (default 1800) for the agent backstop.
#
# Writes <RUN_DIR>/trajectory.jsonl (stdout) and <RUN_DIR>/agent_stderr.log
# (stderr). Exits with the agent container's exit code; 124 if the
# wall-clock backstop killed it.
#
# --kill-after=30: SIGKILL fires 30s after SIGTERM in case `docker run`
# wedges propagating signals to a hung container.
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <RUN_DIR>" >&2
  exit 2
fi

RUN_DIR="$(realpath "$1")"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
WALL_CLOCK_BACKSTOP_SEC="${WALL_CLOCK_BACKSTOP_SEC:-1800}"

set +e
timeout --kill-after=30 "$WALL_CLOCK_BACKSTOP_SEC" docker run --rm \
  --env-file "$REPO_ROOT/.env" \
  -v "$RUN_DIR/workspace":/sandbox \
  -v "$REPO_ROOT/data":/sandbox/data:ro \
  -v "$RUN_DIR/.opencode":/root/.config/opencode \
  -v "$RUN_DIR/prompt.md":/opt/prompt.md:ro \
  fortree:agent \
  bash -c 'opencode run --dir /sandbox --agent coder --format json --print-logs "$(cat /opt/prompt.md)"' \
  > "$RUN_DIR/trajectory.jsonl" \
  2> "$RUN_DIR/agent_stderr.log"
AGENT_EXIT=$?
set -e

exit $AGENT_EXIT

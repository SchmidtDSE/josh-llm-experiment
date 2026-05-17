#!/usr/bin/env bash
# Agent-mode entrypoint. Lives only in `fortree:agent` (added by the agent
# Dockerfile stage). The scorer image does not carry this file.
#
# Invocation (from orchestration/run_agent.sh):
#   docker run --rm --env-file .env \
#     -v <RUN_DIR>/workspace:/sandbox \
#     -v <RUN_DIR>/.opencode:/root/.config/opencode \
#     -v <RUN_DIR>/prompt.md:/opt/prompt.md:ro \
#     -v <RUN_DIR>/agent_artifacts:/opt/agent_meta \
#     fortree:agent /opt/agent-entrypoint.sh
#
# Sequence on a clean run:
#   1. opencode run — streams JSON events to stdout (captured by the
#      caller into trajectory.jsonl); stderr → agent_stderr.log.
#   2. opencode export — dumps the just-finished session as a single JSON
#      blob to /opt/agent_meta/session_export.json. With no session ID
#      argument the latest session is used, which in this fresh --rm
#      container is the one we just ran. TUI noise is on stderr; we
#      redirect that to /dev/null so the file contains JSON only.
#
# Sequence on SIGTERM (sent by the idle watcher or wall-clock backstop):
#   1. Kill the opencode run child cleanly so it stops writing to the DB.
#   2. Run opencode export just like the clean-exit path — the DB is
#      already committed by the time opencode returns.
#   3. Exit 143 (128 + SIGTERM).
#
# Without this trap, a SIGKILL from the watcher would tear the container
# down before export could run, and session_export.json would be empty.
set -uo pipefail

OPENCODE_PID=""
EXPORT_PATH="/opt/agent_meta/session_export.json"

export_session() {
  if [ ! -d /opt/agent_meta ]; then
    return 0
  fi
  # Find the session ID. With a session in the DB, `opencode export` without
  # an explicit ID drops into an interactive picker that hangs forever in a
  # non-TTY context. Parse the listing instead (first column on the data row,
  # which starts with the literal "ses_" prefix).
  local sid
  sid="$(opencode session list 2>/dev/null | awk '/^ses_/{print $1; exit}')"
  if [ -z "$sid" ]; then
    echo "[entrypoint] no session found in DB; skipping export" >&2
    : > "$EXPORT_PATH"
    return 0
  fi
  opencode export "$sid" 2> /opt/agent_meta/export_stderr.log > "$EXPORT_PATH" || true
}

on_term() {
  echo "[entrypoint] SIGTERM received; terminating opencode pid=${OPENCODE_PID:-(none)}" >&2
  if [ -n "$OPENCODE_PID" ] && kill -0 "$OPENCODE_PID" 2>/dev/null; then
    kill -TERM "$OPENCODE_PID" 2>/dev/null || true
    # Block until opencode exits — the outer watcher's grace window
    # caps total wall-time so we don't need our own timeout here.
    wait "$OPENCODE_PID" 2>/dev/null || true
  fi
  echo "[entrypoint] opencode dead; running opencode export" >&2
  export_session
  echo "[entrypoint] export complete ($(stat -c %s "$EXPORT_PATH" 2>/dev/null || echo 0) bytes); exiting 143" >&2
  exit 143
}

trap on_term TERM

opencode run --dir /sandbox --agent coder --format json --print-logs \
  "$(cat /opt/prompt.md)" &
OPENCODE_PID=$!
wait "$OPENCODE_PID"
RUN_EXIT=$?

export_session
exit "$RUN_EXIT"

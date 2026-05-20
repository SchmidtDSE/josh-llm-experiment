#!/usr/bin/env bash
# Agent-mode entrypoint. Lives only in `fortree:agent` (added by the agent
# Dockerfile stage). The scorer image does not carry this file.
#
# Invocation (from orchestration/run_agent.sh):
#   docker run --rm --env-file .env \
#     -e FAIL_FAST_ON_STEP_ERROR=false \
#     -v <RUN_DIR>/workspace:/sandbox \
#     -v <RUN_DIR>/.opencode:/root/.config/opencode \
#     -v <RUN_DIR>/prompt_body.md:/opt/prompt_body.md:ro \
#     -v <REPO_ROOT>/prompts/steps:/opt/steps:ro \
#     -v <RUN_DIR>/agent_artifacts:/opt/agent_meta \
#     fortree:agent /opt/agent-entrypoint.sh
#
# Multi-invocation flow: 8 fresh-session `opencode run` calls in a row,
# one per todo, against the same /sandbox workspace. State carries across
# steps via /sandbox/PLAN.md (seeded by launch_run.sh from
# prompts/PLAN_TEMPLATE.md) and any code the previous step left behind.
# Each call's prompt is the shared body (/opt/prompt_body.md) plus that
# step's static injection (/opt/steps/step_NN_*.md).
#
# Failure semantics:
#   FAIL_FAST_ON_STEP_ERROR=true  → first non-zero opencode exit aborts
#                                   the loop (test/CI mode).
#   FAIL_FAST_ON_STEP_ERROR=false → continue to step N+1 regardless
#                                   (production mode; partial completion
#                                   is data, not failure).
#
# Sequence per step (clean exit):
#   1. opencode run — streams JSON events to per-step trajectory.jsonl;
#      stderr → per-step agent_stderr.log.
#   2. Replay per-step trajectory.jsonl to entrypoint stdout, and stderr
#      log to entrypoint stderr, so run_agent.sh's > trajectory.jsonl
#      redirect collects an in-order rollup of all 8 step trajectories.
#   3. opencode export <sid> → per-step session_export.json. Pick the
#      latest session in the shared DB (the one this step just created).
#   4. Refresh the legacy /opt/agent_meta/session_export.json to point at
#      the most recent step's export (extract_transcript.py et al read
#      this single-file path).
#
# Sequence on SIGTERM (sent by the idle watcher or wall-clock backstop):
#   1. Kill the in-flight opencode child cleanly so it stops writing to
#      the DB.
#   2. Run opencode export for the in-flight step.
#   3. Exit 143 (128 + SIGTERM).
#
# Without this trap, a SIGKILL from the watcher would tear the container
# down before export could run, and session_export.json would be empty.
set -uo pipefail

OPENCODE_PID=""
LATEST_STEP_DIR=""
LEGACY_EXPORT_PATH="/opt/agent_meta/session_export.json"
FAIL_FAST="${FAIL_FAST_ON_STEP_ERROR:-false}"

# The agent shares the dnsmasq sidecar's network namespace via
# --network=container:dnsmasq-<id>, and docker bind-mounts the sidecar's
# /etc/hosts AND /etc/resolv.conf into this container at the same paths
# (same underlying inode). So everything network-identity-shaped is
# already configured by sidecar-init.sh:
#   - /etc/resolv.conf → "nameserver 127.0.0.1" (dnsmasq listening on
#     loopback in the shared netns; populates the ipset allowlist).
#   - /etc/hosts → host.docker.internal pointing at the bridge gateway
#     (from the sidecar's --add-host=host.docker.internal:host-gateway).
# dns_sidecar.sh blocks on the sidecar's HEALTHCHECK before returning,
# so by the time we get here the sidecar's writes are guaranteed visible.

export_session_to() {
  # Dump the latest session in the shared DB to the given path. Across
  # the 8 sequential `opencode run` invocations the DB accumulates one
  # session per step, listed newest-first by `opencode session list`, so
  # the first row is always the step we just finished.
  local out="$1"
  if [ ! -d /opt/agent_meta ]; then
    return 0
  fi
  local sid
  sid="$(opencode session list 2>/dev/null | awk '/^ses_/{print $1; exit}')"
  if [ -z "$sid" ]; then
    echo "[entrypoint] no session found in DB; skipping export to $out" >&2
    : > "$out"
    return 0
  fi
  opencode export "$sid" 2>> /opt/agent_meta/export_stderr.log > "$out" || true
}

on_term() {
  echo "[entrypoint] SIGTERM received; terminating opencode pid=${OPENCODE_PID:-(none)}" >&2
  if [ -n "$OPENCODE_PID" ] && kill -0 "$OPENCODE_PID" 2>/dev/null; then
    kill -TERM "$OPENCODE_PID" 2>/dev/null || true
    # Block until opencode exits — the outer watcher's grace window caps
    # total wall-time so we don't need our own timeout here.
    wait "$OPENCODE_PID" 2>/dev/null || true
  fi
  if [ -n "$LATEST_STEP_DIR" ]; then
    echo "[entrypoint] exporting in-flight session for ${LATEST_STEP_DIR}" >&2
    export_session_to "$LATEST_STEP_DIR/session_export.json"
    cp -f "$LATEST_STEP_DIR/session_export.json" "$LEGACY_EXPORT_PATH" 2>/dev/null || true
  else
    # SIGTERM before any step started — export whatever's in the DB so
    # we don't leave the legacy path empty (downstream tools tolerate
    # empty files but not missing ones).
    export_session_to "$LEGACY_EXPORT_PATH"
  fi
  echo "[entrypoint] export complete; exiting 143" >&2
  exit 143
}

trap on_term TERM

mkdir -p /opt/agent_meta/steps
LAST_EXIT=0

# Iterate the repo-committed per-step injections in name order. The
# `step_NN_<slug>.md` naming sorts numerically by NN.
for STEP_FILE in /opt/steps/step_*.md; do
  STEP_NAME="$(basename "$STEP_FILE" .md)"          # e.g. step_01_make_plan_section
  STEP_N="${STEP_NAME#step_}"
  STEP_N="${STEP_N%%_*}"                            # e.g. 01
  STEP_DIR="/opt/agent_meta/steps/step_${STEP_N}"
  LATEST_STEP_DIR="$STEP_DIR"
  mkdir -p "$STEP_DIR"

  STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[entrypoint] >>> step ${STEP_N} (${STEP_NAME}) start" >&2

  PROMPT="$(cat /opt/prompt_body.md "$STEP_FILE")"

  opencode run --dir /sandbox --agent coder --format json --print-logs \
    "$PROMPT" \
    > "$STEP_DIR/trajectory.jsonl" \
    2> "$STEP_DIR/agent_stderr.log" &
  OPENCODE_PID=$!
  wait "$OPENCODE_PID"
  STEP_EXIT=$?
  LAST_EXIT=$STEP_EXIT
  ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # Replay per-step streams to the entrypoint's stdout/stderr so
  # run_agent.sh's `> trajectory.jsonl` redirect captures an in-order
  # rollup of all 8 steps.
  cat "$STEP_DIR/trajectory.jsonl"
  cat "$STEP_DIR/agent_stderr.log" >&2

  export_session_to "$STEP_DIR/session_export.json"
  cp -f "$STEP_DIR/session_export.json" "$LEGACY_EXPORT_PATH" 2>/dev/null || true

  cat > "$STEP_DIR/step_meta.json" <<META
{
  "step_n": "$STEP_N",
  "step_name": "$STEP_NAME",
  "started_at": "$STARTED_AT",
  "ended_at": "$ENDED_AT",
  "exit_code": $STEP_EXIT
}
META

  echo "[entrypoint] <<< step ${STEP_N} exit=$STEP_EXIT" >&2

  if [ "$STEP_EXIT" -ne 0 ] && [ "$FAIL_FAST" = "true" ]; then
    echo "[entrypoint] FAIL_FAST_ON_STEP_ERROR=true and step ${STEP_N} exited ${STEP_EXIT}; aborting loop" >&2
    exit "$STEP_EXIT"
  fi
done

# Lenient-mode cell exit: 0 if at least one step exited 0; else the last
# step's exit code. Fail-fast mode would have exited inside the loop.
SUCCESS_COUNT=0
for d in /opt/agent_meta/steps/step_*/; do
  if [ -f "$d/step_meta.json" ] && grep -q '"exit_code": 0' "$d/step_meta.json"; then
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
  fi
done
if [ "$SUCCESS_COUNT" -gt 0 ]; then
  exit 0
fi
exit "$LAST_EXIT"

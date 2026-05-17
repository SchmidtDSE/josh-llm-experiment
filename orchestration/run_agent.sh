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
# $WALL_CLOCK_BACKSTOP_SEC (default 1800) for the agent backstop,
# $IDLE_THRESHOLD_SEC (default 120) for the trajectory-idle heartbeat,
# and (optional) $AGENT_NETWORK + $AGENT_DNS to wire the agent through a
# per-run dnsmasq sidecar (set by launch_run.sh from phase 4b onward).
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

# Network mode injection (phase 4b: agent shares the dnsmasq sidecar's
# network namespace via `--network=container:dnsmasq-<id>`, so every
# packet the agent emits is filtered by the sidecar's iptables rules).
# Empty AGENT_NETMODE → docker default bridge, no egress filter — only
# used by tests or by callers that intentionally bypass dns_sidecar.sh.
NETWORK_FLAGS=()
if [ -n "${AGENT_NETMODE:-}" ]; then
  NETWORK_FLAGS+=(--network "$AGENT_NETMODE")
fi
# `--network=container:` is incompatible with `--add-host`, `--dns`,
# `--hostname`, and similar — the agent inherits all of that from the
# sidecar. `host.docker.internal` resolution is set up on the sidecar in
# orchestration/dns_sidecar.sh and propagated by agent-entrypoint.sh
# into the agent's own /etc/hosts at startup.

# Launch the agent in the background so the watcher can run concurrently.
(
  timeout --kill-after=30 "$WALL_CLOCK_BACKSTOP_SEC" docker run --rm \
    --name "$CONTAINER_NAME" \
    --env-file "$REPO_ROOT/.env" \
    "${NETWORK_FLAGS[@]}" \
    -v "$RUN_DIR/workspace":/sandbox \
    -v "$REPO_ROOT/data":/sandbox/data:ro \
    -v "$RUN_DIR/.opencode":/root/.config/opencode \
    -v "$RUN_DIR/prompt.md":/opt/prompt.md:ro \
    -v "$RUN_DIR/agent_artifacts":/opt/agent_meta \
    fortree:agent /opt/agent-entrypoint.sh \
    > "$RUN_DIR/trajectory.jsonl" \
    2> "$RUN_DIR/agent_stderr.log"
) &
AGENT_PID=$!

# Idle watcher: polls trajectory.jsonl size; if no growth for IDLE_THRESHOLD,
# kills the container. Distinct from the wall-clock backstop — this fires
# fast when the LLM stream wedges silently.
#
# Two-stage kill so agent-entrypoint.sh's TERM trap can run opencode export
# before the container goes away: SIGTERM first, wait 30s, then SIGKILL only
# if the container hasn't exited on its own. Mirrors the wall-clock backstop
# which uses `timeout --kill-after=30`.
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
      printf '[heartbeat] trajectory idle for %ds, terminating %s\n' "$idle_for" "$CONTAINER_NAME" >&2
      printf '{"idle_seconds": %d, "killed_at": "%s"}\n' \
        "$idle_for" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$RUN_DIR/stream_stalled.flag"
      docker kill --signal=SIGTERM "$CONTAINER_NAME" 2>/dev/null || true
      # Grace window for the TERM trap to: wait for opencode to flush
      # + die (often slow under CPU load), then start a fresh `opencode
      # export` (~6s cold-start). 60s comfortably covers both on the
      # CPU-only GH-hosted runner.
      grace=60
      for _ in $(seq 1 "$grace"); do
        kill -0 "$AGENT_PID" 2>/dev/null || break
        sleep 1
      done
      if kill -0 "$AGENT_PID" 2>/dev/null; then
        printf '[heartbeat] %s still alive after %ds, SIGKILL\n' "$CONTAINER_NAME" "$grace" >&2
        docker kill --signal=SIGKILL "$CONTAINER_NAME" 2>/dev/null || true
      fi
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

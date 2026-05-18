#!/usr/bin/env bash
# Launch a single phase-3 agent run: render the prompt, render opencode's
# config, bind-mount a workspace, invoke opencode non-interactively.
#
# Required env vars: MODEL, RUNG, TARGET, RUN_ID, plus `.env` carrying
# either OPENROUTER_API_KEY (for openrouter/* MODELs) or OLLAMA_HOST (for
# ollama-* MODELs). Writes everything to runs/<RUN_ID>/. Does NOT score —
# phase 3 stops at "agent produced a workspace"; scoring is a follow-up
# command printed at the end.
#
# Phase-4b additions: a dnsmasq sidecar runs on a per-run docker bridge
# network and logs every DNS query the agent makes. The sidecar's lifecycle
# (start, IP discovery, teardown, log capture) is factored into
# orchestration/dns_sidecar.sh so the orchestration here stays linear.
#
# This script is the high-level orchestrator. Four pieces are factored out
# so they can be invoked / iterated on directly:
#   - orchestration/resolve_model.py     (MODEL → provider/slug)
#   - prompts/target_directive_{josh,mesa}.md  (per-target boilerplate)
#   - orchestration/dns_sidecar.sh       (per-run DNS sidecar lifecycle)
#   - orchestration/run_agent.sh         (the docker-run invocation)
set -euo pipefail

: "${MODEL:?MODEL not set; pick a short name from config/models.yaml (claude|gemma|kimi|minimax|mistral|ollama-qwen-coder-7b|ollama-qwen-coder-1_5b)}"
: "${RUNG:?RUNG not set; 1 or 5 (rungs 2-4 deferred to pilot phase)}"
: "${TARGET:?TARGET not set; josh or mesa}"
: "${RUN_ID:?RUN_ID not set; use \$(uuidgen)}"

WALL_CLOCK_BACKSTOP_SEC="${WALL_CLOCK_BACKSTOP_SEC:-1800}"

case "$TARGET" in josh|mesa) ;; *) echo "TARGET must be josh or mesa, got: $TARGET" >&2; exit 2;; esac
case "$RUNG"   in 1|5)       ;; *) echo "RUNG must be 1 or 5 (rungs 2-4 deferred), got: $RUNG" >&2; exit 2;; esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$REPO_ROOT/.env" ]; then
  echo "Missing $REPO_ROOT/.env — copy .env.example and set the keys your MODEL needs" >&2
  echo "  (OPENROUTER_API_KEY for openrouter/* models; OLLAMA_HOST for ollama-* models)" >&2
  exit 5
fi
# Source .env into the host shell so host-bound knobs (WALL_CLOCK_BACKSTOP_SEC,
# IDLE_THRESHOLD_SEC, BATCH_CONCURRENCY, …) take effect alongside the
# container-bound ones (OPENROUTER_API_KEY, OLLAMA_HOST) that --env-file
# already injects. set -a auto-exports every assignment; set +a restores.
set -a
. "$REPO_ROOT/.env"
set +a

# Batch driver (launch_batch.py) exports BATCH_DIR so all cells in a sweep
# land under runs/<batch-tag>/<run_id>/ next to that batch's worklist /
# joblog / manifest. Operator-invoked single cells get the legacy
# runs/<run_id>/ layout when BATCH_DIR is unset. The path may be relative
# or absolute; resolve to absolute so downstream `realpath` calls in
# run_agent.sh see the same string regardless of how it was passed.
RUNS_PARENT="${BATCH_DIR:-$REPO_ROOT/runs}"
case "$RUNS_PARENT" in
  /*) ;;
  *)  RUNS_PARENT="$REPO_ROOT/$RUNS_PARENT" ;;
esac
RUN_DIR="$RUNS_PARENT/$RUN_ID"
if [ -e "$RUN_DIR" ]; then
  echo "Run dir already exists: $RUN_DIR — refusing to overwrite" >&2
  exit 3
fi

WORKSPACE_DIR="$RUN_DIR/workspace"
CONFIG_DIR="$RUN_DIR/.opencode"
AGENT_ARTIFACTS_DIR="$RUN_DIR/agent_artifacts"
# opencode_data persists opencode's per-session SQLite DB on the host so
# (a) `opencode export` survives a TERM-killed agent and (b) the idle
# watcher in run_agent.sh has a liveness signal that catches sub-agent
# activity (parent agent's trajectory.jsonl goes silent while the `task`
# tool runs a sub-agent, but the DB gets mtime'd on every session event
# regardless of which agent emitted it).
OPENCODE_DATA_DIR="$RUN_DIR/opencode_data"
mkdir -p "$WORKSPACE_DIR" "$CONFIG_DIR" "$AGENT_ARTIFACTS_DIR" "$OPENCODE_DATA_DIR"

RESOLVED_MODEL_ID="$("$REPO_ROOT/orchestration/resolve_model.py" "$MODEL")"

case "$RUNG" in
  1) RUNG_FILE="$REPO_ROOT/prompts/rung1_minimal.md" ;;
  5) RUNG_FILE="$REPO_ROOT/prompts/rung5_master.md" ;;
esac
TARGET_DIRECTIVE_FILE="$REPO_ROOT/prompts/target_directive_${TARGET}.md"

{
  cat "$RUNG_FILE"
  echo ""
  echo ""
  echo "## Implementation directive"
  echo ""
  cat "$TARGET_DIRECTIVE_FILE"
  echo ""
  echo "---"
  echo ""
  cat "$REPO_ROOT/prompts/SIDECAR.md"
} > "$RUN_DIR/prompt.md"

sed "s|\${RESOLVED_MODEL_ID}|$RESOLVED_MODEL_ID|g" \
  "$REPO_ROOT/config/opencode.template.json" > "$CONFIG_DIR/opencode.json"

cat > "$RUN_DIR/run_meta.json" <<META
{
  "run_id": "$RUN_ID",
  "model": "$MODEL",
  "resolved_model_id": "$RESOLVED_MODEL_ID",
  "rung": $RUNG,
  "target": "$TARGET",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "wall_clock_backstop_sec": $WALL_CLOCK_BACKSTOP_SEC
}
META

echo "▶ Launching agent"
echo "  Model:      $MODEL → $RESOLVED_MODEL_ID"
echo "  Rung:       $RUNG"
echo "  Target:     $TARGET"
echo "  Run dir:    $RUN_DIR"
echo "  Backstop:   ${WALL_CLOCK_BACKSTOP_SEC}s"

IDLE_THRESHOLD_SEC="${IDLE_THRESHOLD_SEC:-120}"

# Start the DNS / egress sidecar; trap teardown so a backstop/SIGTERM/manual
# abort still captures dns.log and removes the network. dns_sidecar.sh
# writes $RUN_DIR/dns_sidecar.env with AGENT_NETMODE for the agent — a
# string like `container:dnsmasq-<id>` that gets passed straight to
# `docker run --network=...`, putting the agent in the sidecar's network
# namespace so the sidecar's iptables rules enforce egress.
trap '"$REPO_ROOT/orchestration/dns_sidecar.sh" stop "$RUN_DIR" || true' EXIT
"$REPO_ROOT/orchestration/dns_sidecar.sh" start "$RUN_DIR"
# shellcheck disable=SC1091
source "$RUN_DIR/dns_sidecar.env"
echo "  Net:        $AGENT_NETMODE (egress allowlist enforced)"

set +e
WALL_CLOCK_BACKSTOP_SEC="$WALL_CLOCK_BACKSTOP_SEC" \
IDLE_THRESHOLD_SEC="$IDLE_THRESHOLD_SEC" \
AGENT_NETMODE="$AGENT_NETMODE" \
  "$REPO_ROOT/orchestration/run_agent.sh" "$RUN_DIR"
AGENT_EXIT=$?
set -e

STREAM_STALLED="false"
if [ -f "$RUN_DIR/stream_stalled.flag" ]; then
  STREAM_STALLED="true"
fi

ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$RUN_DIR/run_meta.final.json" <<META
{
  "ended_at": "$ENDED_AT",
  "agent_exit_code": $AGENT_EXIT,
  "stream_stalled": $STREAM_STALLED,
  "trajectory_size_bytes": $(stat -c%s "$RUN_DIR/trajectory.jsonl" 2>/dev/null || echo 0),
  "stderr_size_bytes": $(stat -c%s "$RUN_DIR/agent_stderr.log" 2>/dev/null || echo 0)
}
META

echo ""
echo "✔ Agent done (exit $AGENT_EXIT)"
echo "  Trajectory: $RUN_DIR/trajectory.jsonl"
echo "  Stderr:     $RUN_DIR/agent_stderr.log"
echo ""
echo "Score with:"
echo "  docker run --rm --network=none \\"
echo "    -v $WORKSPACE_DIR:/sandbox \\"
echo "    -v $REPO_ROOT/data:/sandbox/data:ro \\"
echo "    fortree:scorer /opt/entrypoint-scorer.sh --target $TARGET"

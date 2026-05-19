#!/usr/bin/env bash
# Launch a single agent run: render the shared prompt body, seed the
# workspace's PLAN.md, render opencode's config, bring up the DNS sidecar,
# and hand off to run_agent.sh which invokes opencode eight times (one
# per todo) inside a single agent container.
#
# Required env vars: MODEL, RUNG, TARGET, RUN_ID, plus `.env` carrying
# either OPENROUTER_API_KEY (for openrouter/* MODELs) or OLLAMA_HOST (for
# ollama-* MODELs). Writes everything to runs/<RUN_ID>/. Does NOT score —
# scoring is a follow-up command printed at the end.
#
# Phase-4b: a dnsmasq sidecar runs on a per-run docker bridge network and
# logs every DNS query the agent makes. The sidecar's lifecycle (start,
# IP discovery, teardown, log capture) is factored into
# orchestration/dns_sidecar.sh so the orchestration here stays linear.
#
# This script is the high-level orchestrator. Five pieces are factored out
# so they can be invoked / iterated on directly:
#   - orchestration/resolve_model.py     (MODEL → provider/slug)
#   - prompts/rungs/rung<N>_*.md         (per-rung spec body)
#   - prompts/targets/{josh,mesa}.md     (per-target boilerplate)
#   - prompts/steps/step_NN_*.md         (per-todo step injections, repo-static)
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

# BATCH_DIR is the parent dir for this cell's artifacts. launch_batch.py
# exports it per-cell during a sweep (runs/<batch-tag>/); for one-off
# operator invocations, set it explicitly (e.g. runs/interactive). It can
# be relative (resolved against REPO_ROOT) or absolute. Downstream
# `realpath` calls require absolute, so canonicalise once.
: "${BATCH_DIR:?BATCH_DIR not set. Set it to a parent dir for the run, e.g. BATCH_DIR=runs/interactive. launch_batch.py exports it automatically for batch-driven sweeps.}"
case "$BATCH_DIR" in
  /*) ;;
  *)  BATCH_DIR="$REPO_ROOT/$BATCH_DIR" ;;
esac
RUN_DIR="$BATCH_DIR/$RUN_ID"
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
  1) RUNG_FILE="$REPO_ROOT/prompts/rungs/rung1_minimal.md" ;;
  5) RUNG_FILE="$REPO_ROOT/prompts/BASE_PROMPT.md" ;;
esac
TARGET_DIRECTIVE_FILE="$REPO_ROOT/prompts/targets/${TARGET}.md"

# Render the per-run shared body once (rung + target directive + SIDECAR).
# Each of the 8 step invocations concatenates this body with its assigned
# step injection file inside the container. The 8 step files live in the
# repo at prompts/steps/ and are bind-mounted read-only into the container.
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
} > "$RUN_DIR/prompt_body.md"
# Back-compat symlink: any tooling that still reads $RUN_DIR/prompt.md sees
# the rendered body (without the per-step injection, which is appended only
# at invocation time inside the agent container).
ln -sf prompt_body.md "$RUN_DIR/prompt.md"

# Seed PLAN.md in the workspace from the working-document template. The
# 8 opencode invocations all read and update this file; it is the shared
# state across steps since each invocation uses a fresh opencode session.
cp "$REPO_ROOT/prompts/PLAN_TEMPLATE.md" "$WORKSPACE_DIR/PLAN.md"

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

# Two related signals, both written if the idle-watcher fired:
#   idle_killed     — did the watcher SIGTERM the container? (raw fact)
#   stream_stalled  — did the agent stall *mid-work*?  Set true only when
#                     the watcher fired AND the workspace doesn't carry
#                     the artifacts a finished agent would have produced
#                     (run.sh + non-empty output/results.csv). A cell
#                     where the agent finished its loop then went idle
#                     reads as idle_killed=true, stream_stalled=false.
# Falls back gracefully if jq isn't available on the host or the flag
# predates the presumed_done field — assumes stalled, never assumes done.
IDLE_KILLED="false"
STREAM_STALLED="false"
if [ -f "$RUN_DIR/stream_stalled.flag" ]; then
  IDLE_KILLED="true"
  PRESUMED_DONE=$(jq -r '.presumed_done // false' "$RUN_DIR/stream_stalled.flag" 2>/dev/null || echo "false")
  if [ "$PRESUMED_DONE" != "true" ]; then
    STREAM_STALLED="true"
  fi
fi

ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$RUN_DIR/run_meta.final.json" <<META
{
  "ended_at": "$ENDED_AT",
  "agent_exit_code": $AGENT_EXIT,
  "idle_killed": $IDLE_KILLED,
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

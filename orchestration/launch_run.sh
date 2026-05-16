#!/usr/bin/env bash
# Launch a single phase-3 agent run: render the prompt, render opencode's
# config, bind-mount a workspace, invoke opencode non-interactively.
#
# Required env vars: MODEL, RUNG, TARGET, RUN_ID (and OPENROUTER_API_KEY
# in .env). Writes everything to runs/<RUN_ID>/. Does NOT score — phase 3
# stops at "agent produced a workspace"; scoring is a follow-up command
# printed at the end.
#
# Phase-4 additions (dnsmasq DNS observation, --dns flag) are deliberately
# absent: phase 3 first proves the end-to-end opencode → OpenRouter →
# workspace path works at all.
set -euo pipefail

: "${MODEL:?MODEL not set; pick a short name from config/models.yaml (claude|gemma|kimi|minimax|mistral)}"
: "${RUNG:?RUNG not set; 1 or 5 (rungs 2-4 deferred to pilot phase)}"
: "${TARGET:?TARGET not set; josh or mesa}"
: "${RUN_ID:?RUN_ID not set; use \$(uuidgen)}"

WALL_CLOCK_BACKSTOP_SEC="${WALL_CLOCK_BACKSTOP_SEC:-1800}"

case "$TARGET" in josh|mesa) ;; *) echo "TARGET must be josh or mesa, got: $TARGET" >&2; exit 2;; esac
case "$RUNG"   in 1|5)       ;; *) echo "RUNG must be 1 or 5 (rungs 2-4 deferred), got: $RUNG" >&2; exit 2;; esac

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$REPO_ROOT/.env" ]; then
  echo "Missing $REPO_ROOT/.env — copy .env.example and fill OPENROUTER_API_KEY" >&2
  exit 5
fi

RUN_DIR="$REPO_ROOT/runs/$RUN_ID"
if [ -e "$RUN_DIR" ]; then
  echo "Run dir already exists: $RUN_DIR — refusing to overwrite" >&2
  exit 3
fi

WORKSPACE_DIR="$RUN_DIR/workspace"
CONFIG_DIR="$RUN_DIR/.opencode"
mkdir -p "$WORKSPACE_DIR" "$CONFIG_DIR"

RESOLVED_MODEL_ID=$(
  python3 -c "
import sys, yaml
models = yaml.safe_load(open('$REPO_ROOT/config/models.yaml'))
slug = models.get('$MODEL')
if not slug:
    print(f'MODEL \"$MODEL\" not in config/models.yaml', file=sys.stderr); sys.exit(4)
print(slug)
"
)

case "$RUNG" in
  1) RUNG_FILE="$REPO_ROOT/prompts/rung1_minimal.md" ;;
  5) RUNG_FILE="$REPO_ROOT/prompts/rung5_master.md" ;;
esac
case "$TARGET" in
  josh) FRAMEWORK_DIRECTIVE="Implement this using the Josh DSL. Author .josh model files and any .jshd preprocessing config the model needs; do not implement the model in Python." ;;
  mesa) FRAMEWORK_DIRECTIVE="Implement this using the Mesa 3.x Python framework (already installed). The implementation should be a Python module that uses Mesa's Model and Agent classes." ;;
esac

{
  cat "$RUNG_FILE"
  echo ""
  echo ""
  echo "## Implementation directive"
  echo ""
  echo "$FRAMEWORK_DIRECTIVE"
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

set +e
timeout "$WALL_CLOCK_BACKSTOP_SEC" docker run --rm \
  --env-file "$REPO_ROOT/.env" \
  -v "$WORKSPACE_DIR":/sandbox \
  -v "$REPO_ROOT/data":/sandbox/data:ro \
  -v "$CONFIG_DIR":/root/.config/opencode \
  -v "$RUN_DIR/prompt.md":/opt/prompt.md:ro \
  fortree:agent \
  bash -c 'opencode run --dir /sandbox --agent coder --format json --print-logs "$(cat /opt/prompt.md)"' \
  > "$RUN_DIR/trajectory.jsonl" \
  2> "$RUN_DIR/agent_stderr.log"
AGENT_EXIT=$?
set -e

ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$RUN_DIR/run_meta.final.json" <<META
{
  "ended_at": "$ENDED_AT",
  "agent_exit_code": $AGENT_EXIT,
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
echo "  docker run --rm --network=none -v $WORKSPACE_DIR:/sandbox fortree:scorer /opt/entrypoint-scorer.sh --target $TARGET"

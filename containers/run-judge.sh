#!/usr/bin/env bash
# Pod-mode fuzzy-judge entrypoint. Lives only in `fortree:scorer` (added
# by the scorer Dockerfile stage). Invoked from scorer-and-upload.sh
# between the mechanical scorer and the mc-mirror upload, so the
# resulting scorer.fuzzy.json ships to the bucket in the same upload as
# scorer.json.
#
# This is the in-Pod analogue of the host-side
# `orchestration/run_fuzzy_judge.sh` (which still works against a pulled
# batch for ad-hoc re-judging). The two share the same JSON contract
# (fuzzy-v2 schema, FUZZY_JUDGE.md prompt, opencode.judge.json reviewer
# agent, _fuzzy_parse.py extractor) so a re-judge from the host produces
# byte-for-byte equivalent output.
#
# Inputs (cell-data tree from the agent initContainer + the scorer):
#   /cell-data/workspace/                       — agent's source + output/
#   /cell-data/workspace/results/scorer.json    — mechanical scorer result
#   /cell-data/agent_meta/steps/step_NN/        — per-step opencode exports
#
# Outputs (durable):
#   /cell-data/workspace/results/scorer.fuzzy.json  — parsed Q1/Q2/Q3
#   /cell-data/transcript.md                        — rendered for the judge
#                                                     (also useful post-hoc)
#
# Required env vars (mounted by the k8s Pod):
#   OPENROUTER_API_KEY   Same key the agent used; the judge model is
#                        configured separately (default `codex` →
#                        openrouter/openai/gpt-5-codex).
#
# Optional:
#   JUDGE_MODEL          Short name in config/models.yaml.  Default: codex.
#   CELL_DATA_DIR        Override the cell-data root.        Default: /cell-data.
#
# Exit codes — judge failure NEVER fails the scorer container; partial
# fuzzy data is fine for a cell with an otherwise-valid scorer.json. We
# always exit 0 from this script so scorer-and-upload.sh keeps going on
# to the bucket mirror; the _fuzzy_parse error-record path captures the
# failure mode in the file itself.

set -uo pipefail

SCHEMA_VERSION="fuzzy-v2"
CELL_DATA_DIR="${CELL_DATA_DIR:-/cell-data}"
JUDGE_MODEL="${JUDGE_MODEL:-codex}"

if [ ! -d "$CELL_DATA_DIR" ]; then
  echo "run-judge: cell-data dir not found: $CELL_DATA_DIR" >&2
  exit 0
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "run-judge: OPENROUTER_API_KEY not set — skipping fuzzy judge" >&2
  python3 /opt/orchestration/_fuzzy_parse.py \
    --raw /dev/null \
    --out "$CELL_DATA_DIR/workspace/results/scorer.fuzzy.json" \
    --judge-model-id "skipped" \
    --schema-version "$SCHEMA_VERSION" \
    --error "OPENROUTER_API_KEY not present in scorer container env" \
    || true
  exit 0
fi

if ! command -v opencode >/dev/null 2>&1; then
  echo "run-judge: opencode not on PATH — skipping fuzzy judge" >&2
  python3 /opt/orchestration/_fuzzy_parse.py \
    --raw /dev/null \
    --out "$CELL_DATA_DIR/workspace/results/scorer.fuzzy.json" \
    --judge-model-id "skipped" \
    --schema-version "$SCHEMA_VERSION" \
    --error "opencode CLI missing from scorer image" \
    || true
  exit 0
fi

SCORER_JSON="$CELL_DATA_DIR/workspace/results/scorer.json"
FUZZY_JSON="$CELL_DATA_DIR/workspace/results/scorer.fuzzy.json"
TRANSCRIPT="$CELL_DATA_DIR/transcript.md"

# FUZZY_JUDGE.md references `scorer.json` at the cell root, not under
# workspace/results/. Symlink it so the judge's read tool finds it at
# the path the prompt points to. The symlink is harmless in the mirror.
if [ -f "$SCORER_JSON" ] && [ ! -e "$CELL_DATA_DIR/scorer.json" ]; then
  ln -sf "$SCORER_JSON" "$CELL_DATA_DIR/scorer.json"
fi

# Build transcript.md from the per-step exports. extract_transcript.py
# discovers agent_meta/steps/ automatically and writes transcript.md at
# the run-dir root.
if ! python3 /opt/orchestration/extract_transcript.py "$CELL_DATA_DIR" >&2; then
  echo "run-judge: transcript build failed — judge will still run without it" >&2
  # Drop a placeholder so the prompt's `transcript.md` reference resolves.
  printf '# Transcript — unavailable\n\n(extract_transcript.py failed)\n' > "$TRANSCRIPT"
fi

# Resolve judge model short-name → OpenRouter slug.
RESOLVED_JUDGE_MODEL_ID="$(python3 /opt/orchestration/resolve_model.py "$JUDGE_MODEL" 2>&1)"
RESOLVE_RC=$?
if [ $RESOLVE_RC -ne 0 ]; then
  echo "run-judge: cannot resolve judge model '$JUDGE_MODEL': $RESOLVED_JUDGE_MODEL_ID" >&2
  python3 /opt/orchestration/_fuzzy_parse.py \
    --raw /dev/null \
    --out "$FUZZY_JSON" \
    --judge-model-id "$JUDGE_MODEL" \
    --schema-version "$SCHEMA_VERSION" \
    --error "resolve_model failed: $RESOLVED_JUDGE_MODEL_ID" \
    || true
  exit 0
fi

# Per-cell scratch dir for the judge's opencode XDG state, kept off the
# emptyDir's mirrored tree so we don't ship the node_modules / session
# DB to the bucket. (scorer-and-upload.sh's mc mirror still excludes
# **/node_modules/** as a belt-and-suspenders.)
JUDGE_XDG_HOME="$(mktemp -d -t fortree-judge-xdg.XXXXXX)"
JUDGE_DATA_HOME="$(mktemp -d -t fortree-judge-data.XXXXXX)"
mkdir -p "$JUDGE_XDG_HOME/opencode"
trap 'rm -rf "$JUDGE_XDG_HOME" "$JUDGE_DATA_HOME" 2>/dev/null || true' EXIT

sed "s|\${RESOLVED_JUDGE_MODEL_ID}|$RESOLVED_JUDGE_MODEL_ID|g" \
  /opt/config/opencode.judge.json \
  > "$JUDGE_XDG_HOME/opencode/opencode.json"

RAW_OUT="$CELL_DATA_DIR/.fuzzy_judge_raw.txt"
RAW_ERR="$CELL_DATA_DIR/.fuzzy_judge_stderr.log"

echo "▶ Running fuzzy judge ($JUDGE_MODEL → $RESOLVED_JUDGE_MODEL_ID)"
set +e
XDG_CONFIG_HOME="$JUDGE_XDG_HOME" \
XDG_DATA_HOME="$JUDGE_DATA_HOME" \
  opencode run \
    --dir "$CELL_DATA_DIR" \
    --agent reviewer \
    --print-logs \
    "$(cat /opt/prompts/FUZZY_JUDGE.md)" \
    > "$RAW_OUT" 2> "$RAW_ERR"
OC_EXIT=$?
set -e

if [ $OC_EXIT -ne 0 ]; then
  echo "  ✗ opencode exit=$OC_EXIT (raw at $RAW_OUT, stderr at $RAW_ERR)" >&2
  python3 /opt/orchestration/_fuzzy_parse.py \
    --raw "$RAW_OUT" \
    --out "$FUZZY_JSON" \
    --judge-model-id "$RESOLVED_JUDGE_MODEL_ID" \
    --schema-version "$SCHEMA_VERSION" \
    --error "opencode exit=$OC_EXIT" \
    || true
  # Keep raw_out around inside /cell-data so the upload captures it for
  # post-mortem; debug-aid only.
  exit 0
fi

if python3 /opt/orchestration/_fuzzy_parse.py \
     --raw "$RAW_OUT" \
     --out "$FUZZY_JSON" \
     --judge-model-id "$RESOLVED_JUDGE_MODEL_ID" \
     --schema-version "$SCHEMA_VERSION"; then
  echo "  ✓ wrote $FUZZY_JSON"
  # Parse succeeded; raw transcript is redundant.
  rm -f "$RAW_OUT" "$RAW_ERR"
else
  echo "  ✗ parse error (raw retained at $RAW_OUT)" >&2
fi

exit 0

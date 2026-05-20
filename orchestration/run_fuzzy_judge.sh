#!/usr/bin/env bash
# Run the host-side fuzzy LLM-judge against every cell in a completed
# batch directory. Writes `scorer.fuzzy.json` per cell and a batch-level
# `fuzzy_summary.md`. Designed to be invoked either standalone or by
# launch_batch.py (default-on; opt out with --skip-fuzzy-evaluation).
#
# Usage:
#   ./orchestration/run_fuzzy_judge.sh <batch-dir> [--force]
#
# Reads from `.env`:
#   OPENROUTER_API_KEY   Required. Same key the agent uses.
#   JUDGE_MODEL          Optional. Short name in config/models.yaml.
#                        Default: codex (→ openai/gpt-5-codex).
#
# Per cell: invokes opencode in non-agent ("reviewer") mode with
# read/glob/grep only. The prompt at prompts/fuzzy_judge.md tells the
# judge to answer three questions (Q1 framework usage, Q2 confusion
# patterns, Q3 run.sh shape) and emit a fenced JSON block. The script
# greps the last ```json block from opencode's output, validates the
# enums, wraps with judge_model_id + schema_version, and writes
# scorer.fuzzy.json.
#
# Idempotent: skips cells whose scorer.fuzzy.json already carries the
# expected schema_version. Pass --force to re-judge regardless.
#
# Always exits 0 — partial completion is data. Per-cell errors are
# logged to <batch-dir>/fuzzy.log and surfaced in fuzzy_summary.md.

set -uo pipefail

SCHEMA_VERSION="fuzzy-v2"

BATCH_DIR="${1:-}"
FORCE="false"
if [ "${2:-}" = "--force" ]; then
  FORCE="true"
fi

if [ -z "$BATCH_DIR" ]; then
  echo "Usage: $0 <batch-dir> [--force]" >&2
  exit 2
fi
if [ ! -d "$BATCH_DIR" ]; then
  echo "not a directory: $BATCH_DIR" >&2
  exit 2
fi
BATCH_DIR="$(realpath "$BATCH_DIR")"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -f "$REPO_ROOT/.env" ]; then
  echo "missing $REPO_ROOT/.env — copy .env.example and set OPENROUTER_API_KEY" >&2
  exit 5
fi
set -a
# shellcheck disable=SC1091
. "$REPO_ROOT/.env"
set +a

: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY not set in .env}"
JUDGE_MODEL="${JUDGE_MODEL:-codex}"

if ! command -v opencode >/dev/null 2>&1; then
  echo "opencode not found on PATH — install via scripts/install_opencode.sh" >&2
  exit 4
fi

RESOLVED_JUDGE_MODEL_ID="$("$REPO_ROOT/orchestration/resolve_model.py" "$JUDGE_MODEL")"

# Per-batch opencode config + data dirs keep the judge's session DB and
# config segregated from each cell's agent-phase opencode_data/.
# opencode 1.14.50 discovers its config at $XDG_CONFIG_HOME/opencode/
# opencode.json AND merges any .opencode/opencode.json in the working
# dir hierarchy, so the cell's agent-phase config (defining the `coder`
# agent + agent model) and the judge config (defining the `reviewer`
# agent + judge model) coexist; `--agent reviewer` picks ours.
#
# XDG_CONFIG_HOME goes to /tmp, NOT inside $BATCH_DIR: opencode treats
# $XDG_CONFIG_HOME/opencode/ as a project dir and installs its plugin
# tree (node_modules/, ~58 MB) there on first init. With XDG inside
# the batch dir, `mc mirror` swept all of that to GCS. /tmp/<batch-tag>/
# is per-batch (still segregated across concurrent batches on one host),
# torn down on exit, and never seen by the upload.
JUDGE_XDG_HOME="${TMPDIR:-/tmp}/fortree_judge_xdg/$(basename "$BATCH_DIR")"
JUDGE_DATA_HOME="$BATCH_DIR/fuzzy_opencode_data"
mkdir -p "$JUDGE_XDG_HOME/opencode" "$JUDGE_DATA_HOME"
trap 'rm -rf "$JUDGE_XDG_HOME" 2>/dev/null || true' EXIT

sed "s|\${RESOLVED_JUDGE_MODEL_ID}|$RESOLVED_JUDGE_MODEL_ID|g" \
  "$REPO_ROOT/config/opencode.judge.json" \
  > "$JUDGE_XDG_HOME/opencode/opencode.json"

PROMPT_FILE="$REPO_ROOT/prompts/fuzzy_judge.md"
if [ ! -f "$PROMPT_FILE" ]; then
  echo "missing prompt template: $PROMPT_FILE" >&2
  exit 5
fi

FUZZY_LOG="$BATCH_DIR/fuzzy.log"
: > "$FUZZY_LOG"

echo "▶ Fuzzy judge: $BATCH_DIR" | tee -a "$FUZZY_LOG"
echo "  Judge model: $JUDGE_MODEL → $RESOLVED_JUDGE_MODEL_ID" | tee -a "$FUZZY_LOG"
echo "  Force:       $FORCE" | tee -a "$FUZZY_LOG"

# Iterate plausible cell dirs. A cell dir has a `workspace/` subdir;
# everything else under <batch-dir> (cell-logs/, fuzzy_opencode_data/,
# manifest.jsonl, …) is filtered out by that check, so we don't need
# to special-case the uuid-shaped name. The .opencode_judge entry in
# the filter list is dead code for new batches (XDG is now /tmp) but
# kept for back-compat: re-judging a batch produced by the older
# script leaves the legacy dir in $BATCH_DIR.
TOTAL=0
JUDGED=0
SKIPPED=0
ERRORS=0

for cell in "$BATCH_DIR"/*/; do
  cell="${cell%/}"
  cell_name="$(basename "$cell")"
  case "$cell_name" in
    cell-logs|.opencode_judge|fuzzy_opencode_data) continue ;;
  esac
  if [ ! -d "$cell/workspace" ]; then
    continue
  fi
  TOTAL=$((TOTAL + 1))

  fuzzy_out="$cell/scorer.fuzzy.json"
  if [ -f "$fuzzy_out" ] && [ "$FORCE" != "true" ]; then
    existing_ver=$(python3 -c "import json,sys
try:
    print(json.load(open(sys.argv[1])).get('schema_version',''))
except Exception:
    print('')" "$fuzzy_out" 2>/dev/null)
    if [ "$existing_ver" = "$SCHEMA_VERSION" ]; then
      echo "  ↷ skip $cell_name (already judged, $SCHEMA_VERSION)" | tee -a "$FUZZY_LOG"
      SKIPPED=$((SKIPPED + 1))
      continue
    fi
  fi

  echo "  ▷ judge $cell_name" | tee -a "$FUZZY_LOG"
  raw_out="$cell/.fuzzy_judge_raw.txt"
  raw_err="$cell/.fuzzy_judge_stderr.log"

  # The reviewer agent in opencode.judge.json has only read/glob/grep
  # enabled, so --dir confines the judge's file access to this cell's
  # tree. external_directory=allow tolerates any symlink hopping in the
  # batch dir, but the practical surface is workspace/ + transcript.md
  # + scorer.json.
  set +e
  XDG_CONFIG_HOME="$JUDGE_XDG_HOME" \
  XDG_DATA_HOME="$JUDGE_DATA_HOME" \
    opencode run \
      --dir "$cell" \
      --agent reviewer \
      --print-logs \
      "$(cat "$PROMPT_FILE")" \
      > "$raw_out" 2> "$raw_err"
  oc_exit=$?
  set -e

  if [ $oc_exit -ne 0 ]; then
    echo "    ✗ opencode exit=$oc_exit (see $raw_err)" | tee -a "$FUZZY_LOG"
    python3 "$REPO_ROOT/orchestration/_fuzzy_parse.py" \
      --raw "$raw_out" \
      --out "$fuzzy_out" \
      --judge-model-id "$RESOLVED_JUDGE_MODEL_ID" \
      --schema-version "$SCHEMA_VERSION" \
      --error "opencode exit=$oc_exit"
    ERRORS=$((ERRORS + 1))
    continue
  fi

  if python3 "$REPO_ROOT/orchestration/_fuzzy_parse.py" \
       --raw "$raw_out" \
       --out "$fuzzy_out" \
       --judge-model-id "$RESOLVED_JUDGE_MODEL_ID" \
       --schema-version "$SCHEMA_VERSION"; then
    echo "    ✓ wrote $fuzzy_out" | tee -a "$FUZZY_LOG"
    JUDGED=$((JUDGED + 1))
    # Tidy: drop the raw transcript files once parsing succeeded.
    # Keep them on parse-error so the operator can inspect.
    rm -f "$raw_out" "$raw_err"
  else
    echo "    ✗ parse error (raw retained at $raw_out)" | tee -a "$FUZZY_LOG"
    ERRORS=$((ERRORS + 1))
  fi
done

echo "" | tee -a "$FUZZY_LOG"
echo "  Total cells: $TOTAL" | tee -a "$FUZZY_LOG"
echo "  Judged:      $JUDGED" | tee -a "$FUZZY_LOG"
echo "  Skipped:     $SKIPPED" | tee -a "$FUZZY_LOG"
echo "  Errors:      $ERRORS" | tee -a "$FUZZY_LOG"

# Roll up fuzzy_summary.md regardless of per-cell errors — the summary
# generator handles missing/partial scorer.fuzzy.json gracefully.
python3 "$REPO_ROOT/orchestration/_fuzzy_summary.py" "$BATCH_DIR" \
  > "$BATCH_DIR/fuzzy_summary.md" 2>> "$FUZZY_LOG" || \
    echo "  (fuzzy_summary.md generation failed; see $FUZZY_LOG)" | tee -a "$FUZZY_LOG"

echo "✔ Fuzzy judge done" | tee -a "$FUZZY_LOG"
exit 0

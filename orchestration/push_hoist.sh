#!/usr/bin/env bash
# Push locally-computed scorer.hoist.json records back to the bucket, so the
# hoist-v1 answers sit alongside scorer.json / scorer.fuzzy.json in the
# canonical per-cell record.
#
# This is the inverse of pull_artefacts.sh, and deliberately narrow: it
# uploads ONLY files named scorer.hoist.json, one `mc cp` per file to that
# cell's exact destination key. No `mc mirror`, so nothing else in the
# bucket can be overwritten or removed by a stray local path.
#
# The hoist judge runs host-side (orchestration/run_hoist_judge.py) rather
# than in-Pod, so unlike scorer.fuzzy.json these records are not carried up
# by the scorer container's own mirror — this script is how they get there.
#
# Usage:
#   ./orchestration/push_hoist.sh [--dry-run] [<batch-tag> ...]
#
# With no batch tags, every batch under runs/ is pushed. --dry-run lists the
# source → destination pairs and exits without writing anything.
#
# Reads MINIO_* from .env, same names as pull_artefacts.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DRY_RUN=0
BATCHES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *)         BATCHES+=("$1"); shift ;;
  esac
done

if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a
fi

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT not set}"
: "${MINIO_BUCKET:?MINIO_BUCKET not set}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY not set}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY not set}"

if ! command -v mc >/dev/null 2>&1; then
  echo "mc (MinIO client) not found on PATH" >&2
  exit 4
fi

if [ ${#BATCHES[@]} -eq 0 ]; then
  for d in "$REPO_ROOT"/runs/*/; do
    [ -d "$d" ] && BATCHES+=("$(basename "$d")")
  done
fi
if [ ${#BATCHES[@]} -eq 0 ]; then
  echo "no batches found under $REPO_ROOT/runs/" >&2
  exit 5
fi

ALIAS="fortree-archive"
mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

# Objects are keyed <bucket>/<batch-tag>/<batch-tag>/<cell-id>/... — the batch
# tag appears twice (once as the upload prefix, once inside it). Mirror that
# shape exactly so the record lands next to the scorer.json it belongs to.
n=0
skipped=0
for batch in "${BATCHES[@]}"; do
  base="$REPO_ROOT/runs/$batch"
  [ -d "$base" ] || { echo "  ⚠ no such batch dir: $base" >&2; continue; }
  while IFS= read -r -d '' src; do
    cell="$(basename "$(dirname "$(dirname "$(dirname "$src")")")")"
    # Never push an error record over a good one; re-run the judge instead.
    if ! python3 -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get('hoist') else 1)" "$src" 2>/dev/null; then
      echo "  ⊘ skip (parse_error record): $batch/$cell" >&2
      skipped=$((skipped + 1))
      continue
    fi
    dst="$ALIAS/$MINIO_BUCKET/$batch/$batch/$cell/workspace/results/scorer.hoist.json"
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "  would push: runs/$batch/$cell → $dst"
    else
      mc cp --quiet "$src" "$dst" >/dev/null
    fi
    n=$((n + 1))
  done < <(find "$base" -mindepth 4 -maxdepth 4 -path '*/workspace/results/scorer.hoist.json' -print0)
done

if [ "$DRY_RUN" -eq 1 ]; then
  echo "▶ dry run: $n record(s) would be pushed, $skipped skipped"
else
  echo "✔ pushed $n scorer.hoist.json record(s), $skipped skipped"
fi

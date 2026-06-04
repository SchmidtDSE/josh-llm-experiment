#!/usr/bin/env bash
# Pull a completed k8s batch's artefacts from the bucket into runs/<batch>/.
#
# After cells complete, each Pod's scorer container `mc mirror`s its
# /cell-data tree to:
#   <bucket>/<prefix>/<batch-tag>/<cell-id>/...
# This script pulls that whole tree back to the host for analysis with
# analysis/01_analysis.ipynb + analysis/aggregate.py.
#
# Usage:
#   ./orchestration/pull_artefacts.sh <batch-tag>
#
# Reads MINIO_ENDPOINT / MINIO_BUCKET / MINIO_ACCESS_KEY / MINIO_SECRET_KEY
# / MINIO_PREFIX from .env (gitignored), same names as the in-Pod
# scorer-and-upload.sh that wrote the artefacts.

set -euo pipefail

BATCH_TAG="${1:-}"
if [ -z "$BATCH_TAG" ]; then
  echo "Usage: $0 <batch-tag>" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Preserve env-supplied overrides — caller may run e.g.
# `MINIO_PREFIX=pr5-smoke ./orchestration/pull_artefacts.sh pr5-smoke`
# to override the local .env's MINIO_PREFIX.
_ORIG_MINIO_ENDPOINT="${MINIO_ENDPOINT:-}"
_ORIG_MINIO_BUCKET="${MINIO_BUCKET:-}"
_ORIG_MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-}"
_ORIG_MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-}"
_ORIG_MINIO_PREFIX="${MINIO_PREFIX:-}"

if [ ! -f "$REPO_ROOT/.env" ] && [ -z "$_ORIG_MINIO_BUCKET" ]; then
  echo "missing $REPO_ROOT/.env — copy .env.example and fill in MINIO_* keys" >&2
  exit 5
fi
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_ROOT/.env"
  set +a
fi

# Re-apply env-supplied values so they win over .env.
MINIO_ENDPOINT="${_ORIG_MINIO_ENDPOINT:-${MINIO_ENDPOINT:-}}"
MINIO_BUCKET="${_ORIG_MINIO_BUCKET:-${MINIO_BUCKET:-}}"
MINIO_ACCESS_KEY="${_ORIG_MINIO_ACCESS_KEY:-${MINIO_ACCESS_KEY:-}}"
MINIO_SECRET_KEY="${_ORIG_MINIO_SECRET_KEY:-${MINIO_SECRET_KEY:-}}"
MINIO_PREFIX="${_ORIG_MINIO_PREFIX:-${MINIO_PREFIX:-}}"

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT not set}"
: "${MINIO_BUCKET:?MINIO_BUCKET not set}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY not set}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY not set}"

if ! command -v mc >/dev/null 2>&1; then
  echo "mc (MinIO client) not found on PATH" >&2
  echo "install: curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o ~/.local/bin/mc && chmod +x ~/.local/bin/mc" >&2
  exit 4
fi

ALIAS="fortree-archive"
mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

if [ -n "$MINIO_PREFIX" ]; then
  SRC="$ALIAS/$MINIO_BUCKET/${MINIO_PREFIX%/}/$BATCH_TAG"
else
  SRC="$ALIAS/$MINIO_BUCKET/$BATCH_TAG"
fi
DEST="$REPO_ROOT/runs/$BATCH_TAG"
mkdir -p "$DEST"

echo "▶ Pulling $SRC → $DEST"
# Belt-and-suspenders: skip opencode's bundled node_modules if any
# escaped into the bucket from an early-pr5 cell (scorer-and-upload.sh
# now excludes them at upload time, but pre-existing batches may
# still carry them).
mc mirror --overwrite --quiet --exclude "**/node_modules/**" "$SRC" "$DEST"
echo "✔ Done"
echo
echo "Per-cell artefacts now under: $DEST/<cell-id>/"

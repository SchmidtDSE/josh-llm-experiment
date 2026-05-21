#!/usr/bin/env bash
# Pull a completed k8s batch's artefacts from the bucket into runs/<batch>/.
#
# After cells complete, each Pod's scorer container `mc mirror`s its
# /cell-data tree to:
#   <bucket>/<prefix>/<batch-tag>/<cell-id>/...
# This script pulls that whole tree back to the host for analysis with
# analysis/headline.ipynb + analysis/aggregate.py.
#
# Usage:
#   ./orchestration/pull_artefacts.sh <batch-tag>
#
# Reads MINIO_ENDPOINT / MINIO_BUCKET / MINIO_ACCESS_KEY / MINIO_SECRET_KEY
# / MINIO_PREFIX from .env (gitignored), same as orchestration/upload_batch.sh.

set -euo pipefail

BATCH_TAG="${1:-}"
if [ -z "$BATCH_TAG" ]; then
  echo "Usage: $0 <batch-tag>" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -f "$REPO_ROOT/.env" ]; then
  echo "missing $REPO_ROOT/.env — copy .env.example and fill in MINIO_* keys" >&2
  exit 5
fi
set -a
# shellcheck disable=SC1091
. "$REPO_ROOT/.env"
set +a

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT not set in .env}"
: "${MINIO_BUCKET:?MINIO_BUCKET not set in .env}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY not set in .env}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY not set in .env}"
MINIO_PREFIX="${MINIO_PREFIX:-}"

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
mc mirror --overwrite --quiet "$SRC" "$DEST"
echo "✔ Done"
echo
echo "Per-cell artefacts now under: $DEST/<cell-id>/"

#!/usr/bin/env bash
# Upload a completed batch directory to S3-compatible object storage
# (default: GCS via its S3 interoperability API). Runs host-side using the
# `mc` MinIO client — the agent container does not touch this path, so the
# uploads can't affect run-time behaviour, and the agent image stays free
# of credentials and of the `mc` binary.
#
# Usage:
#   ./orchestration/upload_batch.sh <batch-dir>
#
# Example:
#   ./orchestration/upload_batch.sh runs/batch-experimental_cells-panel
#
# Reads from `.env` (gitignored):
#   MINIO_ENDPOINT     S3-compatible endpoint URL.
#                      Default for the project: https://storage.googleapis.com
#                      (GCS via the S3 interop API).
#   MINIO_BUCKET       Destination bucket name.
#   MINIO_ACCESS_KEY   HMAC access key (GCS HMAC pair).
#   MINIO_SECRET_KEY   HMAC secret.
#   MINIO_PREFIX       Optional object-key prefix appended after the bucket
#                      (e.g. "fortree/2026-05/"). Empty by default; the
#                      batch dir name is always appended after this.
#
# Object layout under the bucket:
#   <prefix>/<batch-tag>/<run-id>/...      per-cell artefacts
#   <prefix>/<batch-tag>/batch_report.md   per-batch report
#   <prefix>/<batch-tag>/manifest.jsonl    aggregated manifest
#
# Idempotent: `mc mirror --overwrite` re-uploads changed files only.
# Failure leaves runs on local disk intact; just re-invoke once whatever
# was wrong (creds, network, bucket) is fixed.

set -euo pipefail

BATCH_DIR="${1:-}"
if [ -z "$BATCH_DIR" ]; then
  echo "Usage: $0 <batch-dir>" >&2
  echo "Example: $0 runs/batch-experimental_cells-panel" >&2
  exit 2
fi
if [ ! -d "$BATCH_DIR" ]; then
  echo "not a directory: $BATCH_DIR" >&2
  exit 2
fi
BATCH_DIR="$(realpath "$BATCH_DIR")"
BATCH_NAME="$(basename "$BATCH_DIR")"

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
  echo "install instructions: https://min.io/docs/minio/linux/reference/minio-mc.html#install-mc" >&2
  echo "Linux one-liner:" >&2
  echo "  curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o ~/.local/bin/mc && chmod +x ~/.local/bin/mc" >&2
  exit 4
fi

ALIAS="fortree-archive"
mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

if [ -n "$MINIO_PREFIX" ]; then
  DEST="$ALIAS/$MINIO_BUCKET/${MINIO_PREFIX%/}/$BATCH_NAME"
else
  DEST="$ALIAS/$MINIO_BUCKET/$BATCH_NAME"
fi

echo "▶ Uploading $BATCH_DIR"
echo "  → $DEST"
mc mirror --overwrite --quiet "$BATCH_DIR" "$DEST"
echo "✔ Done"
echo ""
echo "Verify with:"
echo "  mc ls --recursive $DEST | head"

#!/usr/bin/env bash
# Pod-mode mirror sidecar. Lives only in `fortree:scorer` (added by the
# scorer Dockerfile stage). Runs as a k8s sidecar (initContainer with
# restartPolicy: Always) alongside the agent initContainer and later the
# scorer main container; terminates automatically when the scorer exits.
#
# Job: keep an up-to-date snapshot of /cell-data in the bucket so that
# agent failures (OOMKilled at step_05, etc.) don't take the emptyDir
# down with them. Each `mc mirror` is delta-based, so the per-loop cost
# is dominated by listing files + comparing mtimes once /cell-data has
# stabilised; new step_NN/ writes from the agent are picked up by the
# next loop iteration.
#
# The scorer container still runs its own final `mc mirror` after
# scoring + judge; this sidecar is purely insurance for the failure
# path. Don't make the upload contract depend on it for happy-path
# cells — duplicates and incremental sync are fine, but a single
# canonical "final" sync from the scorer is the source of truth.
#
# Required env vars (same as scorer-and-upload.sh, mounted via the
# Pod template's secretKeyRef + value blocks):
#   MINIO_ENDPOINT    S3-compatible endpoint URL.
#   MINIO_BUCKET      Destination bucket name.
#   MINIO_ACCESS_KEY  HMAC access key.
#   MINIO_SECRET_KEY  HMAC secret.
#   BATCH_TAG         Batch identifier (k8s Job name).
#   RUN_ID            Per-cell run identifier (k8s Pod name / cell tag).
#
# Optional:
#   MINIO_PREFIX               Object-key prefix appended after the bucket.
#   UPLOAD_SOURCE_DIR          Directory tree to mirror (default /cell-data).
#   MIRROR_INTERVAL_SECONDS    Seconds between mirror loops (default 15).
#
# Exit behaviour: the loop is infinite by design. K8s terminates the
# sidecar when all non-sidecar regular containers (just `scorer`) exit.
# Any failure inside the loop is logged + ignored (the next iteration
# retries); we never want a transient mc error to take the sidecar
# down while the agent is still producing useful workspace state.

set -uo pipefail

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT not set}"
: "${MINIO_BUCKET:?MINIO_BUCKET not set}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY not set}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY not set}"
: "${BATCH_TAG:?BATCH_TAG not set}"
: "${RUN_ID:?RUN_ID not set}"
MINIO_PREFIX="${MINIO_PREFIX:-}"
UPLOAD_SOURCE_DIR="${UPLOAD_SOURCE_DIR:-/cell-data}"
INTERVAL="${MIRROR_INTERVAL_SECONDS:-15}"

ALIAS="fortree-archive"
mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

if [ -n "$MINIO_PREFIX" ]; then
  DEST="$ALIAS/$MINIO_BUCKET/${MINIO_PREFIX%/}/$BATCH_TAG/$RUN_ID"
else
  DEST="$ALIAS/$MINIO_BUCKET/$BATCH_TAG/$RUN_ID"
fi

echo "▶ mirror-sidecar: $UPLOAD_SOURCE_DIR → $DEST (interval ${INTERVAL}s)"

while true; do
  # Wait for the source dir to exist (agent's initContainer might not
  # have started yet on the first loop iteration). Don't error if so.
  if [ -d "$UPLOAD_SOURCE_DIR" ]; then
    # Belt-and-suspenders: exclude opencode's bundled node_modules
    # (~50 MiB of zod locales etc., re-fetched from the agent image on
    # each run; never useful in the bucket). scorer-and-upload.sh does
    # the same exclusion.
    if ! mc mirror --overwrite --quiet \
           --exclude "**/node_modules/**" \
           "$UPLOAD_SOURCE_DIR" "$DEST" 2>/dev/null; then
      echo "  ⚠ mirror tick failed (transient; retrying in ${INTERVAL}s)" >&2
    fi
  fi
  sleep "$INTERVAL"
done

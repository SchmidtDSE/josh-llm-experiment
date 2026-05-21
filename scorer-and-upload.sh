#!/usr/bin/env bash
# Pod-mode scorer entrypoint. Lives only in `fortree:scorer` (added by
# the scorer Dockerfile stage). Wraps the existing scorer entrypoint and
# then mirrors the workspace to S3-compatible object storage via `mc`,
# which is the upload responsibility currently owned by the host-side
# `orchestration/upload_batch.sh` (deleted in PR6 once k8s submission is
# live).
#
# Designed to be the main-container `command` of the target k8s Pod
# shape (see IMPLEMENTATION_PLAN.md §K8s execution). The existing
# `entrypoint-scorer.sh` stays in place as the canonical entrypoint for
# local smoke fixtures and the existing local orchestration; this
# wrapper is additive in PR3 — nothing invokes it yet.
#
# Invocation (under the k8s Pod, eventually):
#   /opt/scorer-and-upload.sh --target <josh|mesa>
#
# Required env vars (mirror upload_batch.sh's contract — same names so
# .env.example documentation carries over):
#   MINIO_ENDPOINT    S3-compatible endpoint URL.
#   MINIO_BUCKET      Destination bucket name.
#   MINIO_ACCESS_KEY  HMAC access key.
#   MINIO_SECRET_KEY  HMAC secret.
#   BATCH_TAG         Batch identifier (k8s Job name in PR5).
#   RUN_ID            Per-cell run identifier (k8s Pod name / cell tag in PR5).
#
# Optional:
#   MINIO_PREFIX      Object-key prefix appended after the bucket.
#   UPLOAD_SOURCE_DIR Directory tree to mirror (default `/sandbox`).
#                     The k8s Job template sets this to `/cell-data`,
#                     a shared emptyDir that contains both the agent's
#                     workspace and its per-step metadata; mirroring
#                     the whole tree gives the scorer.json + workspace
#                     + opencode state + trajectory.jsonl in one go.
#
# Object layout under the bucket (same as upload_batch.sh):
#   <prefix>/<batch-tag>/<run-id>/...
#
# Exit code is the scorer's exit code — uploads run even on score
# failure (failed cells are still data, matching the spirit of
# `upload_batch.sh` running after `launch_batch.py` regardless of
# per-cell outcome).

set -euo pipefail

: "${MINIO_ENDPOINT:?MINIO_ENDPOINT not set}"
: "${MINIO_BUCKET:?MINIO_BUCKET not set}"
: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY not set}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY not set}"
: "${BATCH_TAG:?BATCH_TAG not set}"
: "${RUN_ID:?RUN_ID not set}"
MINIO_PREFIX="${MINIO_PREFIX:-}"
UPLOAD_SOURCE_DIR="${UPLOAD_SOURCE_DIR:-/sandbox}"

# Always-upload-on-failure: the agent initContainer is wrapped to
# always exit 0 (see job.yaml.j2) and writes its real exit code to
# /cell-data/agent_exit_code. Read that here and decide whether to
# score/judge. A missing file means the wrapper itself was SIGKILL'd
# (e.g. cgroup OOM with memory.oom.group=1) — treat as catastrophic
# failure but still upload whatever artefacts the workspace contains.
AGENT_EXIT_FILE="/cell-data/agent_exit_code"
if [ -r "$AGENT_EXIT_FILE" ]; then
  AGENT_EXIT="$(cat "$AGENT_EXIT_FILE")"
else
  AGENT_EXIT="137"  # assume SIGKILL when the wrapper couldn't even record
  echo "▶ /cell-data/agent_exit_code missing — assuming agent SIGKILL (exit=137)" >&2
fi

if [ "$AGENT_EXIT" = "0" ]; then
  set +e
  python /opt/harness/run_metrics.py "$@"
  SCORER_RC=$?
  set -e

  # Run the LLM fuzzy judge (Q1: framework usage, Q2: confusion patterns,
  # Q3: run.sh wall-clock contract) between the mechanical scorer and the
  # upload, so scorer.fuzzy.json lands in the bucket alongside scorer.json.
  # Judge failure does NOT fail the cell — partial fuzzy data is fine and
  # the script always exits 0; the _fuzzy_parse error-record path captures
  # any failure inside scorer.fuzzy.json itself.
  /opt/run-judge.sh || true
else
  echo "▶ Agent failed (exit=$AGENT_EXIT). Skipping run_metrics.py + run-judge.sh;" >&2
  echo "  uploading partial workspace + per-step exports for post-mortem." >&2
  SCORER_RC="$AGENT_EXIT"
fi

ALIAS="fortree-archive"
mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

if [ -n "$MINIO_PREFIX" ]; then
  DEST="$ALIAS/$MINIO_BUCKET/${MINIO_PREFIX%/}/$BATCH_TAG/$RUN_ID"
else
  DEST="$ALIAS/$MINIO_BUCKET/$BATCH_TAG/$RUN_ID"
fi

echo "▶ Uploading $UPLOAD_SOURCE_DIR → $DEST"
# Exclude opencode's bundled node_modules (~50 MiB of zod locales + esbuild
# + zod-mini + …) — they're a runtime detail of opencode itself, totally
# irrelevant to the experiment and re-fetched from the agent image each
# run. The first pr5-smoke uploaded them; second smoke onwards skips them.
mc mirror --overwrite --quiet --exclude "**/node_modules/**" "$UPLOAD_SOURCE_DIR" "$DEST"
echo "✔ Upload done (scorer exit code: $SCORER_RC)"

exit "$SCORER_RC"

#!/usr/bin/env bash
# Render + apply a RESCORE batch: re-run only the scorer against cells'
# already-saved workspaces (pulled in-Pod from the bucket), without re-running
# the agent. Parallel to k8s_apply.sh but uses the agent-less rescore template.
# See SCORER_K8S.md.
#
# Usage:
#   ./orchestration/k8s_rescore.sh \
#       --batch-tag rescore-20260603 \
#       --image-scorer ghcr.io/schmidtdse/josh-llm-experiment/fortree-scorer:a5b3eae \
#       --manifest orchestration/rescore-manifest.csv
#
# The manifest (cols orig_batch_tag,run_id,target[,minio_prefix]) is produced by
# orchestration/classify_no_scorer.py. Other render_jobs.py flags pass through
# (e.g. --active-deadline-seconds, --judge-model, --compute-class).
#
# Cluster flags handled locally (not forwarded to render):
#   --context <ctx>   kubectl context (default: current).
#   --no-apply        Render only.
#   --watch           After apply, kubectl get -w on the Jobs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RENDER_ARGS=()
KUBECTL_CONTEXT=""
NO_APPLY=0
WATCH=0
BATCH_TAG=""
MANIFEST="$REPO_ROOT/orchestration/rescore-manifest.csv"

while [ $# -gt 0 ]; do
  case "$1" in
    --batch-tag)
      BATCH_TAG="$2"
      RENDER_ARGS+=("--batch-tag" "$2")
      shift 2
      ;;
    --manifest)
      MANIFEST="$2"
      shift 2
      ;;
    --context)
      KUBECTL_CONTEXT="$2"
      shift 2
      ;;
    --no-apply)
      NO_APPLY=1
      shift
      ;;
    --watch)
      WATCH=1
      shift
      ;;
    *)
      RENDER_ARGS+=("$1")
      shift
      ;;
  esac
done

if [ -z "$BATCH_TAG" ]; then
  echo "missing --batch-tag (the NEW rescore batch tag, e.g. rescore-20260603)" >&2
  exit 2
fi
if [ ! -f "$MANIFEST" ]; then
  echo "manifest not found: $MANIFEST (run classify_no_scorer.py first)" >&2
  exit 2
fi

BATCH_SLUG="$(echo "$BATCH_TAG" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+|-+$//g; s/-+/-/g')"
RENDERED_DIR="$REPO_ROOT/orchestration/rendered/$BATCH_SLUG"

echo "▶ Render (rescore)"
( cd "$REPO_ROOT" && python orchestration/render_jobs.py --rescore-manifest "$MANIFEST" "${RENDER_ARGS[@]}" )

KUBECTL=(kubectl)
if [ -n "$KUBECTL_CONTEXT" ]; then
  KUBECTL+=(--context "$KUBECTL_CONTEXT")
fi

if [ "$NO_APPLY" = "1" ]; then
  echo "▶ --no-apply set; manifests left at $RENDERED_DIR"
  exit 0
fi

echo "▶ kubectl apply -f $RENDERED_DIR"
"${KUBECTL[@]}" apply -f "$RENDERED_DIR"

echo "▶ Applied. To follow:"
echo "    kubectl -n joshsim get jobs -l batch-tag=$BATCH_SLUG -w"
echo "    kubectl -n joshsim logs -l batch-tag=$BATCH_SLUG -c scorer --tail=-1 -f --max-log-requests=20"
echo "  Then pull each ORIGINAL batch with its own prefix and re-aggregate, e.g.:"
echo "    MINIO_PREFIX=<orig-batch> ./orchestration/pull_artefacts.sh <orig-batch>"

if [ "$WATCH" = "1" ]; then
  echo
  echo "▶ Watching (Ctrl-C to detach)"
  "${KUBECTL[@]}" -n joshsim get jobs -l "batch-tag=$BATCH_SLUG" -w
fi

#!/usr/bin/env bash
# Render + apply one batch of per-cell k8s Jobs to the cluster.
#
# Wraps the render_jobs.py + kubectl apply two-step. Convenience layer
# only; the actual submission is `kubectl apply -f <rendered-dir>/`,
# which can also be run by hand.
#
# Usage:
#   ./orchestration/k8s_apply.sh \
#       --batch-tag pr5-smoke \
#       --image-agent  ghcr.io/schmidtdse/josh-llm-experiment/fortree-agent:sha-abcdef \
#       --image-scorer ghcr.io/schmidtdse/josh-llm-experiment/fortree-scorer:sha-abcdef \
#       --single-cell model=claude,target=josh
#
# Or for the full panel:
#   ./orchestration/k8s_apply.sh \
#       --batch-tag headline-2026-05 \
#       --image-agent  …:sha-abcdef \
#       --image-scorer …:sha-abcdef \
#       --matrix orchestration/matrix.csv
#
# Optional flags forwarded to render_jobs.py: see `render_jobs.py --help`.
# Optional cluster flags this script accepts:
#   --context <ctx>     kubectl context (default: current).
#   --no-apply          Render only; print what would be applied.
#   --watch             After apply, kubectl get -w on the Jobs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RENDER_ARGS=()
KUBECTL_CONTEXT=""
NO_APPLY=0
WATCH=0
BATCH_TAG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --batch-tag)
      BATCH_TAG="$2"
      RENDER_ARGS+=("--batch-tag" "$2")
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
  echo "missing --batch-tag" >&2
  exit 2
fi

# Slugify batch tag the same way render_jobs.py does so the path matches.
BATCH_SLUG="$(echo "$BATCH_TAG" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+|-+$//g; s/-+/-/g')"
RENDERED_DIR="$REPO_ROOT/orchestration/rendered/$BATCH_SLUG"

echo "▶ Render"
# `uv run` honours pyproject.toml + uv.lock so the renderer uses the
# project's pinned Python + Jinja/PyYAML versions, not whatever happens
# to be on $PATH on this host.
( cd "$REPO_ROOT" && uv run orchestration/render_jobs.py "${RENDER_ARGS[@]}" )

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
echo "    kubectl -n joshsim logs -l batch-tag=$BATCH_SLUG -c agent  --tail=-1 -f --max-log-requests=20"
echo "    kubectl -n joshsim logs -l batch-tag=$BATCH_SLUG -c scorer --tail=-1 -f --max-log-requests=20"

if [ "$WATCH" = "1" ]; then
  echo
  echo "▶ Watching (Ctrl-C to detach)"
  "${KUBECTL[@]}" -n joshsim get jobs -l "batch-tag=$BATCH_SLUG" -w
fi

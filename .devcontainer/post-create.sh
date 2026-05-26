#!/usr/bin/env bash
# Devcontainer post-create hook. Idempotent — re-runs are no-ops.
#
# Devcontainer features handle docker (DinD), gh, gcloud + gke-gcloud-auth-plugin,
# kubectl, and common-utils. This script adds the bits the features don't cover:
#
#   1. The MinIO client `mc`. Used host-side by orchestration/pull_artefacts.sh
#      to fetch per-cell artefacts from the GCS bucket via the S3 interop API.
#      (`mc` also lives inside fortree:scorer for the in-Pod upload; that copy
#      is independent — see scripts/install_mc.sh.) We reuse the same install
#      script so the host and the scorer image share a single source of truth
#      for the rolling-latest sha256 anchor.
#   2. `pixi install` to materialise the orchestrator's Python env from
#      pixi.lock so the user can run `pixi run ...` without paying the
#      lazy-install cost on first invocation.

set -euo pipefail

if ! command -v mc >/dev/null 2>&1; then
    echo "Installing mc via scripts/install_mc.sh ..."
    ./scripts/install_mc.sh
else
    echo "mc already installed at $(command -v mc); leaving as-is"
fi

# Pixi env. `pixi install` is idempotent — uses pixi.lock as the source of
# truth, no-op when the env is already materialised.
if [ -f pixi.toml ]; then
    echo "Materialising pixi env ..."
    pixi install
fi

echo "post-create.sh done."

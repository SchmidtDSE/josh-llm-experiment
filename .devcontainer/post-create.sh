#!/usr/bin/env bash
# Devcontainer post-create hook. Idempotent — re-runs are no-ops.
#
# Devcontainer features handle docker (DinD), gh, gcloud, kubectl, and
# common-utils. This script adds the bits the features don't (reliably) cover:
#
#   1. The GKE gcloud auth plugin. The dhoeric/google-cloud-cli feature can
#      install it via `installGkeGcloudAuthPlugin`, but v1.0.1's plugin step
#      targets the legacy `google-cloud-sdk-gke-gcloud-auth-plugin` package
#      (frozen at an older version that won't co-install with the renamed,
#      current `google-cloud-cli` gcloud) — so it silently no-ops and kubectl
#      can't authenticate to GKE. We disable that option in devcontainer.json
#      and install the correctly-named package here instead.
#   2. The MinIO client `mc`. Used host-side by orchestration/pull_artefacts.sh
#      to fetch per-cell artefacts from the GCS bucket via the S3 interop API.
#      (`mc` also lives inside fortree:scorer for the in-Pod upload; that copy
#      is independent — see scripts/install_mc.sh.) We reuse the same install
#      script so the host and the scorer image share a single source of truth
#      for the rolling-latest sha256 anchor.
#   3. `pixi install` to materialise the orchestrator's Python env from
#      pixi.lock so the user can run `pixi run ...` without paying the
#      lazy-install cost on first invocation.

set -euo pipefail

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! command -v gke-gcloud-auth-plugin >/dev/null 2>&1; then
    echo "Installing gke-gcloud-auth-plugin (current google-cloud-cli-* package) ..."
    $SUDO apt-get update -qq
    $SUDO apt-get install -y google-cloud-cli-gke-gcloud-auth-plugin
else
    echo "gke-gcloud-auth-plugin already installed at $(command -v gke-gcloud-auth-plugin); leaving as-is"
fi

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

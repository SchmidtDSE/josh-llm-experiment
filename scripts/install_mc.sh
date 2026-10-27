#!/usr/bin/env bash
# Install the MinIO client (`mc`) on the devcontainer host. MinIO no longer
# publishes community mc binaries (dl.min.io returns 410 Gone), so — as in
# SchmidtDSE/josh#524 — we copy the mc bundled in the frozen bitnamilegacy
# MinIO image. The image ref is read from the Dockerfile's `mc-source` stage
# so the host and the scorer image share a single digest pin.
#
# The scorer image does NOT run this script; it COPYs mc straight from the
# `mc-source` stage. The agent image gets no `mc` at all, so the agent
# container has no path to the bucket — preserving the trust boundary.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MC_IMAGE="${MC_IMAGE:-$(sed -n 's/^FROM \(bitnamilegacy\/minio[^ ]*\) AS mc-source$/\1/p' "$REPO_ROOT/Dockerfile")}"
MC_INSTALL_PATH="${MC_INSTALL_PATH:-/usr/local/bin/mc}"

if [ -z "$MC_IMAGE" ]; then
  echo "ERROR: could not find the mc-source stage in $REPO_ROOT/Dockerfile" >&2
  exit 1
fi

CID="$(docker create "$MC_IMAGE")"
trap 'docker rm "$CID" >/dev/null' EXIT
docker cp "$CID:/opt/bitnami/minio-client/bin/mc" "$MC_INSTALL_PATH"
chmod +x "$MC_INSTALL_PATH"
"$MC_INSTALL_PATH" --version | head -1

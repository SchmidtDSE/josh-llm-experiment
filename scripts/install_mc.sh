#!/usr/bin/env bash
# Install the MinIO client (`mc`) into the scorer image. Pinned to the final
# community release on GitHub and verified against its published sha256:
# MinIO archived the open-source mc in 2025 and dl.min.io now returns
# 410 Gone for community binaries, so there is no rolling-latest to track.
#
# Invoked from the Dockerfile during the scorer-stage build. The agent
# image does NOT run this script, so `mc` is not on the agent's PATH and
# the agent container has no path to the bucket — preserving the trust
# boundary.

set -euo pipefail

MC_RELEASE="RELEASE.2025-08-13T08-35-41Z"
MC_SHA256="01f866e9c5f9b87c2b09116fa5d7c06695b106242d829a8bb32990c00312e891"
MC_BINARY_URL="${MC_BINARY_URL:-https://github.com/minio/mc/releases/download/${MC_RELEASE}/mc.linux-amd64.${MC_RELEASE}}"
MC_INSTALL_PATH="${MC_INSTALL_PATH:-/usr/local/bin/mc}"

curl -fSL "$MC_BINARY_URL" -o "$MC_INSTALL_PATH"
echo "${MC_SHA256}  ${MC_INSTALL_PATH}" | sha256sum -c -
chmod +x "$MC_INSTALL_PATH"

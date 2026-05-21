#!/usr/bin/env bash
# Install the MinIO client (`mc`) into the scorer image. Records the
# downloaded binary's sha256 as the reproducibility anchor — MinIO's
# release-channel URL serves the rolling latest binary with no committed
# tag, same situation as the Josh fat jar (see install_josh.sh).
#
# Invoked from the Dockerfile during the scorer-stage build. The agent
# image does NOT run this script, so `mc` is not on the agent's PATH and
# the agent container has no path to the bucket — preserving the trust
# boundary.

set -euo pipefail

MC_BINARY_URL="${MC_BINARY_URL:-https://dl.min.io/client/mc/release/linux-amd64/mc}"
MC_INSTALL_PATH="${MC_INSTALL_PATH:-/usr/local/bin/mc}"

curl -fSL "$MC_BINARY_URL" -o "$MC_INSTALL_PATH"
chmod +x "$MC_INSTALL_PATH"
sha256sum "$MC_INSTALL_PATH"

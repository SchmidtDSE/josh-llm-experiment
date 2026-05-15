#!/usr/bin/env bash
# Install Eclipse Temurin 21 JRE from Adoptium's apt repo. Pinned by major
# version; the package auto-updates within the major version. Used by the
# Josh CLI (the rolling joshsim-fat.jar targets class file v63 = Java 21+).
#
# Invoked from the Dockerfile during image build. Assumes ca-certificates,
# curl, and gnupg are already present in the base image.

set -euo pipefail

mkdir -p /etc/apt/keyrings
curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
  | gpg --dearmor -o /etc/apt/keyrings/adoptium.gpg

echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb bookworm main" \
  > /etc/apt/sources.list.d/adoptium.list

apt-get update
apt-get install -y --no-install-recommends temurin-21-jre
rm -rf /var/lib/apt/lists/*

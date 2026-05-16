#!/usr/bin/env bash
# Pre-warm the tiktoken cl100k_base cache so entropy.py works offline.
# Without this, `tiktoken.get_encoding('cl100k_base')` tries to fetch the
# BPE file from openaipublic.blob.core.windows.net the first time it's
# loaded — which fails under --network=none in the scorer.
#
# Invoked from the Dockerfile during image build. TIKTOKEN_CACHE_DIR is
# set as an ENV in the Dockerfile so both build-time pre-warm and
# runtime lookup resolve to the same path.

set -euo pipefail

mkdir -p "$TIKTOKEN_CACHE_DIR"
python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

#!/usr/bin/env bash
# Install opencode at the pinned version using the upstream installer. Symlink
# the resulting binary into /usr/local/bin for a stable path.
#
# Invoked from the Dockerfile during image build.

set -euo pipefail

OPENCODE_VERSION="${OPENCODE_VERSION:-1.14.50}"

curl -fsSL https://opencode.ai/install \
  | bash -s -- --version "$OPENCODE_VERSION" --no-modify-path

# Upstream installer drops the binary under $HOME/.opencode/bin. Symlink it to
# /usr/local/bin so any tooling can reference one stable path regardless of
# which user runs as.
INSTALLED_AT="$HOME/.opencode/bin/opencode"
if [ ! -x "$INSTALLED_AT" ]; then
  echo "install_opencode: expected $INSTALLED_AT to exist after install" >&2
  exit 1
fi
ln -sf "$INSTALLED_AT" /usr/local/bin/opencode

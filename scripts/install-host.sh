#!/usr/bin/env bash
# Install the host-side orchestration layer: uv, OpenShell, opencode.
#
# Architecture rationale: OpenShell IS the agent sandbox boundary (Landlock fs
# isolation + network proxy with host/path-level allowlist + OCSF logs).
# Wrapping OpenShell in another Docker layer would double-sandbox for no real
# gain. So OpenShell and opencode run on the host; only the scorer is
# Docker-based (--network=none + read-only workspace mount).
#
# Pins in config/VERSIONS.md.

set -euo pipefail

OPENCODE_VERSION="${OPENCODE_VERSION:-1.14.50}"
OPENSHELL_VERSION="${OPENSHELL_VERSION:-0.0.36}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing uv (Astral)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs to $HOME/.local/bin by default.
  export PATH="$HOME/.local/bin:$PATH"
else
  echo "    uv already on PATH ($(uv --version))"
fi

echo "==> Installing openshell==${OPENSHELL_VERSION}"
uv tool install --force "openshell==${OPENSHELL_VERSION}"

echo "==> Installing opencode==${OPENCODE_VERSION}"
curl -fsSL https://opencode.ai/install \
  | bash -s -- --version "${OPENCODE_VERSION}" --no-modify-path
# opencode installs to $HOME/.opencode/bin. Symlink into ~/.local/bin so the
# user's PATH only needs to know about one directory.
mkdir -p "$HOME/.local/bin"
ln -sf "$HOME/.opencode/bin/opencode" "$HOME/.local/bin/opencode"

echo
echo "==> Versions:"
"$HOME/.local/bin/uv" --version
"$HOME/.local/bin/openshell" --version
"$HOME/.local/bin/opencode" --version

cat <<EOF

Done. Add \$HOME/.local/bin to your PATH if it isn't already:

  export PATH="\$HOME/.local/bin:\$PATH"

Agent-side runtime (Python 3.11, JDK 17, Josh CLI, scientific deps) is NOT
installed by this script. It is installed in phase 3, when the OpenShell
sandbox policy starts exposing those tools to the agent process.
EOF

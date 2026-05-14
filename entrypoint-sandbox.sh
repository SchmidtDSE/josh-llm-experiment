#!/usr/bin/env bash
set -euo pipefail

# Sandbox container entrypoint. Two modes:
#   --sandbox=off  (phase 3): run opencode directly against the bind-mounted workspace.
#   --sandbox=on   (phase 4): create an OpenShell sandbox with the pinned policy,
#                              then exec opencode inside it.
#
# The orchestrator passes the prompt path, model id, and workspace via env vars
# rendered into the opencode config; this script only chooses the wrapper.

SANDBOX_MODE="off"
PASSTHROUGH=()

for arg in "$@"; do
  case "$arg" in
    --sandbox=on)  SANDBOX_MODE="on" ;;
    --sandbox=off) SANDBOX_MODE="off" ;;
    *) PASSTHROUGH+=("$arg") ;;
  esac
done

# Phase 1 only needs `opencode --version` and `openshell --version` to work,
# both of which short-circuit the wrapper logic.
if [ "${#PASSTHROUGH[@]}" -gt 0 ]; then
  case "${PASSTHROUGH[0]}" in
    opencode|openshell|python|python3|java|josh|bash|sh)
      exec "${PASSTHROUGH[@]}"
      ;;
  esac
fi

# Phase 3+ wiring lands here. Stub until then.
if [ ! -f /opt/orchestration/agent_phase.sh ]; then
  echo "entrypoint-sandbox: /opt/orchestration/agent_phase.sh not present (phase 3 not complete)" >&2
  echo "  sandbox mode requested: $SANDBOX_MODE" >&2
  exit 64
fi

exec /opt/orchestration/agent_phase.sh --sandbox="$SANDBOX_MODE" "${PASSTHROUGH[@]}"

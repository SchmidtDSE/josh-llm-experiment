#!/usr/bin/env bash
# DNS-observation + egress-enforcement sidecar lifecycle for one agent run.
#
# Usage:
#   dns_sidecar.sh start <RUN_DIR>     # create network + start sidecar
#   dns_sidecar.sh stop  <RUN_DIR>     # collect log, tear down
#
# `start`:
#   1. Creates a fresh docker bridge network (`fortree-run-<id>`).
#   2. Runs `fortree:dnsmasq` on that network with CAP_NET_ADMIN +
#      `--add-host=host.docker.internal:host-gateway`. The sidecar's
#      sidecar-init.sh installs iptables rules that REJECT anything not
#      on the dnsmasq-maintained ipset allowlist, then execs dnsmasq.
#   3. Writes <RUN_DIR>/dns_sidecar.env with one line for the caller:
#        AGENT_NETMODE=container:<sidecar-container-name>
#      run_agent.sh passes that string to `docker run --network=...`,
#      so the agent shares the sidecar's network namespace and is
#      forced through the iptables rules.
#
# `stop` is idempotent: capture docker-logs into <RUN_DIR>/dns.log,
# remove the sidecar container, remove the network. Safe to call from
# an EXIT trap even after a backstop SIGKILL.
set -euo pipefail

SIDECAR_IMAGE="${SIDECAR_IMAGE:-fortree:dnsmasq}"

cmd_start() {
  local run_dir="$(realpath "$1")"
  local run_id="$(basename "$run_dir")"
  local network="fortree-run-${run_id}"
  local container="dnsmasq-${run_id}"

  docker network create "$network" > /dev/null

  docker run -d --name "$container" \
    --network "$network" \
    --cap-add NET_ADMIN \
    --add-host=host.docker.internal:host-gateway \
    "$SIDECAR_IMAGE" > /dev/null

  cat > "$run_dir/dns_sidecar.env" <<ENV
AGENT_NETMODE=container:$container
ENV
}

cmd_stop() {
  local run_dir="$(realpath "$1")"
  local run_id="$(basename "$run_dir")"
  local network="fortree-run-${run_id}"
  local container="dnsmasq-${run_id}"

  if docker inspect "$container" > /dev/null 2>&1; then
    docker logs "$container" > "$run_dir/dns.log" 2>&1 || true
    docker rm -f "$container" > /dev/null 2>&1 || true
  fi

  if docker network inspect "$network" > /dev/null 2>&1; then
    docker network rm "$network" > /dev/null 2>&1 || true
  fi
}

main() {
  if [ $# -lt 2 ]; then
    echo "Usage: $0 <start|stop> <RUN_DIR>" >&2
    exit 2
  fi
  local sub="$1"
  shift
  case "$sub" in
    start) cmd_start "$@" ;;
    stop)  cmd_stop  "$@" ;;
    *) echo "Unknown subcommand: $sub" >&2; exit 2 ;;
  esac
}

main "$@"

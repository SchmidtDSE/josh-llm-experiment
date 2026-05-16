#!/usr/bin/env bash
# DNS-observability sidecar lifecycle for a single agent run.
#
# Usage:
#   dns_sidecar.sh start <RUN_DIR>     # create network + start dnsmasq
#   dns_sidecar.sh stop  <RUN_DIR>     # collect log, tear down
#
# `start` creates a fresh docker bridge network and runs `fortree:dnsmasq`
# (built from the Dockerfile dnsmasq stage) on it. It writes
# <RUN_DIR>/dns_sidecar.env with two lines for the caller to source:
#   AGENT_NETWORK=fortree-run-<id>
#   AGENT_DNS=<sidecar-ip-on-that-network>
#
# `stop` is idempotent — safe to call from a trap that may fire after the
# agent's wall-clock backstop killed everything. It writes <RUN_DIR>/dns.log
# from the sidecar's stdout/stderr, removes the sidecar container, and
# removes the network. Missing pieces are skipped without error.
set -euo pipefail

SIDECAR_IMAGE="${SIDECAR_IMAGE:-fortree:dnsmasq}"

_run_id_of() {
  basename "$(realpath "$1")"
}

cmd_start() {
  local run_dir="$(realpath "$1")"
  local run_id="$(basename "$run_dir")"
  local network="fortree-run-${run_id}"
  local container="dnsmasq-${run_id}"

  docker network create "$network" > /dev/null

  docker run -d --name "$container" \
    --network "$network" \
    --cap-add NET_ADMIN \
    "$SIDECAR_IMAGE" > /dev/null

  # Resolve the sidecar's IP on the new network.
  local sidecar_ip
  sidecar_ip=$(docker inspect -f \
    "{{(index .NetworkSettings.Networks \"${network}\").IPAddress}}" \
    "$container")
  if [ -z "$sidecar_ip" ]; then
    echo "dns_sidecar: failed to resolve $container IP on $network" >&2
    exit 1
  fi

  cat > "$run_dir/dns_sidecar.env" <<ENV
AGENT_NETWORK=$network
AGENT_DNS=$sidecar_ip
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

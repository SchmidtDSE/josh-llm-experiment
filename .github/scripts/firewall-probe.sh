#!/usr/bin/env bash
# Probe the per-run egress firewall enforced by fortree:dnsmasq.
#
# Starts the sidecar on a throwaway docker network, joins busybox probes
# to the sidecar's netns via --network=container:..., and TCP-connects to
# each host:port in the probe table below. Asserts that allowlisted hosts
# (per orchestration/dnsmasq.conf `ipset=` entries) succeed and that
# hosts deliberately NOT on the allowlist are REJECTed at the kernel.
#
# No agent, no model, no API key — deterministic CI material.
#
# Usage: firewall-probe.sh [sidecar-image-tag]    (default: fortree:dnsmasq)
set -euo pipefail

SIDECAR_IMAGE="${1:-fortree:dnsmasq}"
PROBE_IMAGE="busybox:1.36"

SIDECAR="fortree-firewall-probe-$$"
NETWORK="fortree-firewall-probe-$$"

cleanup() {
  docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker pull "$PROBE_IMAGE" >/dev/null
docker network create "$NETWORK" >/dev/null

docker run -d --name "$SIDECAR" \
  --network "$NETWORK" \
  --cap-add NET_ADMIN \
  --add-host=host.docker.internal:host-gateway \
  "$SIDECAR_IMAGE" >/dev/null

# Wait for sidecar-init.sh to install iptables rules and exec dnsmasq.
# It's cheap (<1s typically) but the docker daemon takes its time.
for _ in $(seq 1 10); do
  if docker exec "$SIDECAR" pgrep dnsmasq >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

probe_tcp() {
  local host="$1"
  local port="$2"
  # Override /etc/resolv.conf so name resolution goes through the
  # sidecar's dnsmasq (which populates the ipset). Docker injects its
  # embedded DNS at 127.0.0.11 by default; that bypasses the allowlist.
  docker run --rm --network "container:$SIDECAR" "$PROBE_IMAGE" sh -c "
    echo nameserver 127.0.0.1 > /etc/resolv.conf
    nc -zvw5 $host $port
  "
}

# Hosts chosen for stability: openrouter.ai + mesa.readthedocs.io are
# both Cloudflare-fronted and rarely unavailable. pypi.org +
# stackoverflow.com are similarly stable. joshsim.org is deliberately
# left off this allow probe — it's on DreamHost shared hosting and has
# transient outages that would make this smoke test flaky.
#
# entry: <host>|<port>|<allow|block>
PROBES=(
  "openrouter.ai|443|allow"
  "mesa.readthedocs.io|443|allow"
  "pypi.org|443|block"
  "stackoverflow.com|443|block"
)

FAIL=0
for entry in "${PROBES[@]}"; do
  IFS='|' read -r HOST PORT EXPECTED <<< "$entry"
  echo "::group::probe $HOST:$PORT (expect $EXPECTED)"
  if probe_tcp "$HOST" "$PORT"; then
    ACTUAL=allow
  else
    ACTUAL=block
  fi
  echo "::endgroup::"
  if [ "$ACTUAL" = "$EXPECTED" ]; then
    echo "✓ $HOST:$PORT — $ACTUAL"
  else
    echo "✗ $HOST:$PORT — expected $EXPECTED, got $ACTUAL" >&2
    FAIL=1
  fi
done

echo "::group::sidecar state after probes"
echo "--- ipset allowed_dst (resolved IPs added by dnsmasq) ---"
docker exec "$SIDECAR" ipset list allowed_dst 2>&1 | head -25 || true
echo ""
echo "--- iptables OUTPUT chain (packet counters) ---"
docker exec "$SIDECAR" iptables -L OUTPUT -v -n 2>&1 || true
echo "::endgroup::"

exit "$FAIL"

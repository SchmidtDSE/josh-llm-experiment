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

# Wait for the sidecar's HEALTHCHECK to flip to `healthy` before probing.
# Until then iptables rules + dnsmasq aren't guaranteed up, and the
# probes would race the init. See HEALTHCHECK in Dockerfile.dnsmasq.
status=""
for _ in $(seq 1 30); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$SIDECAR" 2>/dev/null || echo unknown)"
  if [ "$status" = "healthy" ]; then break; fi
  sleep 0.5
done
if [ "$status" != "healthy" ]; then
  echo "firewall-probe: sidecar didn't become healthy within 15s (last status: $status)" >&2
  docker logs "$SIDECAR" >&2 || true
  exit 1
fi

probe_tcp() {
  local host="$1"
  local port="$2"
  # The probe container also inherits the sidecar's /etc/resolv.conf
  # (same inode under --network=container:), so DNS goes through
  # dnsmasq and the ipset populates. No per-probe override needed.
  docker run --rm --network "container:$SIDECAR" "$PROBE_IMAGE" \
    nc -zvw5 "$host" "$port"
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

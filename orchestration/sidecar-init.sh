#!/bin/sh
# Per-run egress-enforcement sidecar: dnsmasq + iptables + ipset.
#
# Runs in a network namespace shared with the agent container (the agent
# joins via `docker run --network=container:dnsmasq-<id>`). All packets
# the agent emits hit the iptables rules set up here.
#
# Policy:
#   - Default DROP on OUTPUT.
#   - Allow loopback and ESTABLISHED/RELATED.
#   - Allow 53/udp + 53/tcp to public DNS (dnsmasq forwards there).
#   - Allow anything destined for an IP in the `allowed_dst` ipset.
#     dnsmasq populates that set as it resolves hosts named in its
#     `ipset=/host/allowed_dst` directives (see /etc/dnsmasq.conf).
#   - Allow anything destined for the bridge gateway (so the agent can
#     reach `host.docker.internal` for an ollama on the host).
#
# Strict mode is deliberately conservative: we'd rather a misconfigured
# allowlist surface as a connect failure than silently let the agent
# reach unintended hosts.
set -eu

UPSTREAM_DNS="1.1.1.1 8.8.8.8"
IPSET_NAME="allowed_dst"

# Override docker's embedded DNS (127.0.0.11) so this container's own
# name lookups go through dnsmasq running locally — otherwise lookups
# would bypass our `ipset=` directives and hosts would never enter the
# allowlist.
echo "nameserver 127.0.0.1" > /etc/resolv.conf

# Recreate the ipset on every start so a recycled container doesn't
# inherit stale entries. The set is hash-of-IP; dnsmasq adds members as
# `inet` IPv4 addresses.
ipset destroy "$IPSET_NAME" 2>/dev/null || true
ipset create "$IPSET_NAME" hash:ip family inet timeout 0

# Resolve the bridge gateway (route the netns inherited from the docker
# network). Allowed unconditionally so host.docker.internal → host port
# (e.g. ollama on :11434) works.
GATEWAY="$(ip route show default | awk '/^default/ {print $3; exit}')"
echo "[sidecar-init] bridge gateway: ${GATEWAY:-<unknown>}" >&2

iptables -F OUTPUT
iptables -P OUTPUT DROP

iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
for ns in $UPSTREAM_DNS; do
  iptables -A OUTPUT -d "$ns" -p udp --dport 53 -j ACCEPT
  iptables -A OUTPUT -d "$ns" -p tcp --dport 53 -j ACCEPT
done
if [ -n "${GATEWAY:-}" ]; then
  iptables -A OUTPUT -d "$GATEWAY" -j ACCEPT
fi
iptables -A OUTPUT -m set --match-set "$IPSET_NAME" dst -j ACCEPT
iptables -A OUTPUT -m limit --limit 10/min -j LOG --log-prefix "[egress-reject] " --log-level 4
iptables -A OUTPUT -j REJECT --reject-with icmp-port-unreachable

echo "[sidecar-init] iptables OUTPUT chain installed; default DROP + ipset $IPSET_NAME" >&2

# -k: foreground so the container stays alive. dnsmasq writes its query
# log to stderr (log-facility=- in dnsmasq.conf) and populates the ipset
# as its `ipset=...` directives are matched against incoming queries.
exec dnsmasq -k

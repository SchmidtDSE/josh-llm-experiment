#!/usr/bin/env python3
"""Add `host.docker.internal` to /etc/hosts pointing at the default gateway.

Used by agent-entrypoint.sh at agent startup. The agent shares the
dnsmasq sidecar's network namespace via `--network=container:...`, which
makes docker reject `--add-host` on the agent container, so we can't set
host.docker.internal via a docker run flag.

Belt-and-suspenders: empirically, current Docker (≥ 24.x) propagates the
sidecar's /etc/hosts (including its --add-host entries) into a
netns-joining container, so the `host.docker.internal` entry is usually
already present and this script no-ops via `already_present()`. The
script is here for docker versions / configurations where that
propagation doesn't happen — we read the default route from
/proc/net/route (same netns as the sidecar = same gateway) and append
the entry ourselves.

Idempotent: bails early if `host.docker.internal` is already in
/etc/hosts. Exits 0 on success or no-op, 1 if the default gateway
couldn't be determined.
"""

from __future__ import annotations

import re
import socket
import struct
import sys

HOSTS_PATH = "/etc/hosts"
ROUTE_PATH = "/proc/net/route"
TARGET = "host.docker.internal"


def already_present() -> bool:
    with open(HOSTS_PATH, encoding="utf-8") as f:
        return bool(re.search(rf"\b{re.escape(TARGET)}\b", f.read()))


def default_gateway() -> str | None:
    """Parse /proc/net/route for the default route's gateway as dotted-quad.

    Column layout: Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT.
    Default route has Destination == "00000000" (and Mask == "00000000").
    Gateway is little-endian hex of the IPv4 address.
    """
    with open(ROUTE_PATH, encoding="utf-8") as f:
        next(f, None)  # skip header
        for line in f:
            parts = line.split()
            if len(parts) >= 8 and parts[1] == "00000000" and parts[7] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
    return None


def main() -> int:
    if already_present():
        return 0
    gw = default_gateway()
    if not gw:
        print(
            f"[agent-host-internal] WARN: could not resolve default gateway; "
            f"{TARGET} not set in {HOSTS_PATH}",
            file=sys.stderr,
        )
        return 1
    with open(HOSTS_PATH, "a", encoding="utf-8") as f:
        f.write(f"{gw} {TARGET}\n")
    print(f"[agent-host-internal] resolved {TARGET} → {gw}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

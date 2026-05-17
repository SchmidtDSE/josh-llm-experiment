FROM python:3.11-slim-bookworm AS base

# Multistage image:
#   - agent:   docker run --rm --env-file .env -v ./runs/<id>:/sandbox
#                fortree:agent opencode run --config /opt/opencode.json ...
#   - scorer:  docker run --rm --network=none -v ./runs/<id>:/sandbox
#                fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa>
#   - dnsmasq: docker run -d --network fortree-run-<id>
#                fortree:dnsmasq   (alpine + dnsmasq, query logging on)
#
# `base` holds everything common to agent/scorer. `agent` is base with no
# extra payload. `scorer` adds harness code + acceptance ranges. The
# structural separation matters: an agent container cannot read
# `/opt/harness/acceptance_ranges.json` because those files don't exist
# in `fortree:agent` — they're only in `fortree:scorer`. `dnsmasq` is a
# separate tiny alpine image used as a per-run DNS sidecar so we can log
# every name the agent resolves; it shares no layers with base.

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gnupg \
    && rm -rf /var/lib/apt/lists/*

# Java 21 (Eclipse Temurin) for the Josh CLI. The rolling joshsim-fat.jar
# targets class file v63, so Java 17 (max v61) won't load it.
COPY scripts/install_java.sh /tmp/install_java.sh
RUN /tmp/install_java.sh && rm /tmp/install_java.sh

# Josh CLI: rolling main fat jar, sha256 pinned at build time.
COPY scripts/install_josh.sh /tmp/install_josh.sh
RUN /tmp/install_josh.sh && rm /tmp/install_josh.sh

# Python scientific stack (mesa, numpy, pandas, scipy, xarray, netCDF4,
# rasterio, tiktoken — pinned in requirements.txt). System python is 3.11.
COPY config/requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r /opt/requirements.txt

# opencode: pinned via the upstream installer, symlinked into /usr/local/bin.
COPY scripts/install_opencode.sh /tmp/install_opencode.sh
RUN /tmp/install_opencode.sh && rm /tmp/install_opencode.sh

ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
COPY scripts/init_tiktoken.sh /tmp/init_tiktoken.sh
RUN /tmp/init_tiktoken.sh && rm /tmp/init_tiktoken.sh

WORKDIR /sandbox

# ---------- agent stage ----------
FROM base AS agent
# Notably does NOT contain /opt/harness/ or the scorer entrypoint —
# `fortree:agent` cannot read /opt/harness/acceptance_ranges.json because
# those files only exist in `fortree:scorer`. The only payload is a thin
# wrapper that runs `opencode run` and then `opencode export` so the
# orchestrator can read a normalized session JSON instead of walking the
# streaming-event trajectory.
COPY agent-entrypoint.sh /opt/agent-entrypoint.sh
RUN chmod +x /opt/agent-entrypoint.sh

# ---------- scorer stage ----------
FROM base AS scorer
COPY harness/ /opt/harness/
COPY entrypoint-scorer.sh /opt/entrypoint-scorer.sh
RUN chmod +x /opt/entrypoint-scorer.sh

# ---------- dnsmasq stage ----------
# Independent of `base` — runs only as a per-run DNS sidecar on the same
# bridge network as the agent. Logs every query to stderr where launch_run.sh
# captures it via `docker logs` on teardown.
FROM alpine:3.20 AS dnsmasq
RUN apk add --no-cache dnsmasq
COPY orchestration/dnsmasq.conf /etc/dnsmasq.conf
EXPOSE 53/udp 53/tcp
# -k keeps dnsmasq in the foreground so the container stays up.
ENTRYPOINT ["dnsmasq", "-k"]

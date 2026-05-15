# Single image used two ways:
#   - sandbox: openshell sandbox create --from . -- opencode ...
#     (community base satisfies OpenShell's sandbox readiness contract:
#     supervisor + sandbox users, sshd, the iproute2/nftables tooling the
#     egress proxy uses, etc. We inherit all of that.)
#   - scorer:  docker run --rm --network=none -v workspace:/sandbox
#                  ghcr.io/.../fortree:<tag>
#                  /opt/entrypoint-scorer.sh --target <josh|mesa>
#
# By FROM-ing the community base we avoid reverse-engineering the sandbox
# contract — NVIDIA defines it, we extend it.

# Pinned community base. SHA tag is from the NVIDIA/OpenShell-Community repo;
# bump to roll the inherited supervisor-adjacent layer.
FROM ghcr.io/nvidia/openshell-community/sandboxes/base:db19652 AS fortree

USER root

# Java 17 for the Josh CLI. noble/main ships openjdk-17-jre-headless.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Josh CLI: rolling main fat jar, sha256 pinned at build time.
COPY scripts/install_josh.sh /tmp/install_josh.sh
RUN /tmp/install_josh.sh && rm /tmp/install_josh.sh

# Python 3.11 + scientific stack in a dedicated venv at /opt/fortree-venv.
# Base ships uv and a 3.12 .venv at /sandbox/.venv; we deliberately don't
# touch that so anything in base depending on it keeps working.
COPY config/requirements.txt /opt/requirements.txt
RUN uv venv --python 3.11 /opt/fortree-venv \
    && uv pip install --python /opt/fortree-venv/bin/python --no-cache -r /opt/requirements.txt
ENV PATH="/opt/fortree-venv/bin:${PATH}"

# Override the base's opencode pin (1.2.18) with the one we want.
RUN npm install -g opencode-ai@1.14.50

# Scorer entrypoint. Phase 2 will COPY harness/ in.
COPY entrypoint-scorer.sh /opt/entrypoint-scorer.sh
RUN chmod +x /opt/entrypoint-scorer.sh

# Phase 2 will uncomment.
# COPY harness /opt/harness

# The base sets ENTRYPOINT=[/bin/bash] so `openshell sandbox connect` lands
# in a shell. That prepends bash to whatever command docker run passes,
# which breaks plain `docker run fortree python -c "..."`. Clear it; the
# OpenShell supervisor sets its own entrypoint in sandbox mode, and the
# scorer mode invokes its entrypoint explicitly.
ENTRYPOINT []

# Drop back to the sandbox user that the base set. The supervisor (when this
# image runs under OpenShell) will manage privilege itself; for plain-Docker
# scorer mode, running as sandbox keeps us off root.
USER sandbox
WORKDIR /sandbox

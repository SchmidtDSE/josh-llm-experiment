FROM python:3.11-slim-bookworm AS fortree

# Single image used two ways:
#   - agent:  docker run --rm --env-file .env -v ./runs/<id>:/sandbox
#               fortree:<tag> opencode run --config /opt/opencode.json ...
#   - scorer: docker run --rm --network=none -v ./runs/<id>:/sandbox
#               fortree:<tag> /opt/entrypoint-scorer.sh --target <josh|mesa>
#
# Same env both ways — that is the whole point of unifying these images.

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      openjdk-17-jre-headless \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

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

# Scorer entrypoint. Phase 2 will COPY harness/ in and the entrypoint will
# dispatch into harness/run_metrics.py.
COPY entrypoint-scorer.sh /opt/entrypoint-scorer.sh
RUN chmod +x /opt/entrypoint-scorer.sh

# Phase 2 will uncomment.
# COPY harness /opt/harness

WORKDIR /sandbox

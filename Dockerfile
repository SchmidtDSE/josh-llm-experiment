FROM python:3.11-slim-bookworm AS base

# Multistage build for the agent + scorer roles. Both inherit from
# `base` (python + java + josh + opencode + scientific stack):
#
#   docker build --target agent  -t fortree:agent  .
#   docker build --target scorer -t fortree:scorer .
#
# Run them as:
#   - agent:  docker run --rm --env-file .env -v ./runs/<id>:/sandbox
#               fortree:agent /opt/agent-entrypoint.sh
#   - scorer: docker run --rm --network=none -v ./runs/<id>:/sandbox
#               fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa>
#
# Source layout: all container-entrypoint scripts live in `containers/`
# at repo root. They're COPY'd into /opt/ inside the image so runtime
# paths are unaffected by the source-side reshuffle.
#
# Structural separation matters: an agent container cannot read
# /opt/harness/acceptance_ranges.json because those files only exist in
# fortree:scorer.
#
# Phase 5 had a third per-run dnsmasq egress sidecar (fortree:dnsmasq)
# built from a separate Dockerfile.dnsmasq; both that file and the
# sidecar were retired in Phase 6 PR4 + PR6.

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

# Default heap ceiling for ALL `java` invocations inside the container —
# Josh CLI (via the wrapper), agent-authored `java -jar joshsim-fat.jar`
# variants, and the scorer's `josh validate`. JAVA_TOOL_OPTIONS is the
# canonical JVM env var honored by every invocation regardless of how
# it was launched, so agents that bypass the wrapper still get a sane
# heap budget instead of the JVM's host-RAM-derived ergonomic default
# (~25 % of host RAM, ~63 GiB on this fleet's 251 GiB host — wildly
# too generous when 10 cells run concurrently).
#
# 16 GiB per JVM × 10 concurrent josh cells = 160 GiB heap, comfortable
# in our 251 GiB host. Agents can still override per-invocation by
# re-exporting JAVA_TOOL_OPTIONS in their `run.sh`, or by passing
# JAVA_OPTS to the `josh` wrapper (see scripts/install_josh.sh).
ENV JAVA_TOOL_OPTIONS="-Xmx16g"

# Josh CLI: DEV fat jar (carries the `mcp` subcommand, SchmidtDSE/josh#440),
# pinned by sha256. The jar is a ROLLING artifact at a fixed URL, so the pin
# is load-bearing: install_josh.sh fails on mismatch (integrity), and bumping
# JOSH_JAR_SHA256 busts this RUN layer's cache (otherwise a rebuild keeps the
# stale cached jar). Bump the sha when intentionally moving to a newer dev
# build (fetch it via `pixi run get-jars`). Pin back to main with
# --build-arg JOSH_JAR_URL=...main/joshsim-fat.jar JOSH_JAR_SHA256=<main sha>.
ARG JOSH_JAR_URL=https://joshsim.org/dist/dev/joshsim-fat.jar
ARG JOSH_JAR_SHA256=f3713a8baabeec443046980462e9c639f396f665ffb61f9adcd8762506bec6b8
COPY scripts/install_josh.sh /tmp/install_josh.sh
RUN JOSH_JAR_URL="$JOSH_JAR_URL" JOSH_JAR_SHA256="$JOSH_JAR_SHA256" /tmp/install_josh.sh && rm /tmp/install_josh.sh

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
# Notably does NOT contain /opt/harness/, the scorer entrypoint, or `mc` —
# `fortree:agent` cannot read /opt/harness/acceptance_ranges.json (only
# exists in `fortree:scorer`) and has no MinIO client on PATH, so it
# cannot exfiltrate to the bucket either. The payload is a thin wrapper
# that runs `opencode run` 8× (one per todo) plus the static per-step
# injection prompts the wrapper concatenates onto the per-cell body.
COPY containers/agent-entrypoint.sh /opt/agent-entrypoint.sh
RUN chmod +x /opt/agent-entrypoint.sh
# Bake the 8 per-step injection prompts into the image. Previously
# bind-mounted at run time by the local orchestration (going away in
# PR6); the k8s Pod model cannot bind-mount host files, and these
# prompts are repo-committed, deterministic, and small (~40 lines total).
COPY prompts/steps/ /opt/steps/
# Bake the canonical synthetic climate netCDFs into the image (~2.5 MiB).
# In local orchestration these were bind-mounted from the repo's data/
# dir; the k8s flow has no host filesystem to mount from, so we bake +
# copy. The agent's Pod prelude does `cp /opt/data/*.nc /sandbox/data/`
# before launching opencode so the workspace sees them at the canonical
# path the prompt names. Without this seed, the agent would write its
# own generate_data.py to synthesise replacements — observed once in a
# pr5 smoke, which then chose 1 km grid resolution (≈14 000 cells) vs
# the canonical 31×50 = 1 550 cells, and stalled in josh preprocess.
COPY data/*.nc /opt/data/
# Bake the run.sh seed (chmod +x, N_REPLICATES wiring + default 2).
# Agent prelude `install -m 0755`s this into /sandbox/run.sh so the
# agent inherits a working stub it fills in. Scorer overrides
# N_REPLICATES=100 at invocation time for the canonical workload.
COPY containers/agent-run.sh.seed /opt/run.sh.seed
RUN chmod 0755 /opt/run.sh.seed

# josh-mcp arm: the constrained agent (no bash) doesn't author run.sh
# itself; instead the prelude installs a one-line shim (run.sh) that
# execs a harness-supplied generic MCP runner (runner.py), which reads
# agent-authored /sandbox/mcp_calls.json and forwards every entry to
# the `josh mcp` stdio server via the Python MCP client. The agent's
# deliverable for this arm is the .josh source + mcp_calls.json;
# runner.py is the immutable runtime. Both seeds live in the agent
# image so the setup initContainer can install them target-aware (see
# orchestration/templates/job.yaml.j2). See prompts/targets/josh-mcp.md
# for the agent-facing contract.
COPY containers/josh-mcp-runner.py /opt/josh-mcp-runner.py
COPY containers/josh-mcp-run.sh /opt/josh-mcp-run.sh
RUN chmod 0755 /opt/josh-mcp-run.sh

# ---------- scorer stage ----------
FROM base AS scorer
COPY harness/ /opt/harness/
COPY containers/entrypoint-scorer.sh /opt/entrypoint-scorer.sh
RUN chmod +x /opt/entrypoint-scorer.sh

# MinIO client (`mc`) for the k8s Pod scorer entrypoint — uploads the
# completed /sandbox to S3-compatible object storage. Installed in the
# scorer stage only; the agent stage above has no `mc` and no path to
# bucket credentials.
COPY scripts/install_mc.sh /tmp/install_mc.sh
RUN /tmp/install_mc.sh && rm /tmp/install_mc.sh

COPY containers/scorer-and-upload.sh /opt/scorer-and-upload.sh
RUN chmod +x /opt/scorer-and-upload.sh

# Fuzzy-judge assets — Q1/Q2/Q3 LLM-judge driven from inside the scorer
# container (see run-judge.sh). Scorer-only by design: the agent image
# above doesn't carry FUZZY_JUDGE.md or the judge opencode config, so
# the agent can't read the rubric it'll be evaluated against. The judge
# itself only uses read/glob/grep (config/opencode.judge.json) and only
# writes scorer.fuzzy.json — no path back into agent territory.
# Layout mirrors the repo layout at /opt/ so resolve_model.py finds
# config/models.yaml via its usual `__file__.parent.parent` walk
# without forking the path logic.
COPY prompts/FUZZY_JUDGE.md /opt/prompts/FUZZY_JUDGE.md
COPY config/opencode.judge.json /opt/config/opencode.judge.json
COPY config/models.yaml /opt/config/models.yaml
COPY orchestration/_fuzzy_parse.py /opt/orchestration/_fuzzy_parse.py
COPY orchestration/extract_transcript.py /opt/orchestration/extract_transcript.py
COPY orchestration/resolve_model.py /opt/orchestration/resolve_model.py
COPY containers/run-judge.sh /opt/run-judge.sh
RUN chmod +x /opt/run-judge.sh

# Mirror-sidecar: runs alongside the agent initContainer (k8s native
# sidecar pattern, initContainer with restartPolicy: Always). Keeps
# /cell-data continuously synced to the bucket so an agent OOM doesn't
# take the workspace with it. See mirror-sidecar.sh for the full
# rationale and orchestration/templates/job.yaml.j2 for the wiring.
COPY containers/mirror-sidecar.sh /opt/mirror-sidecar.sh
RUN chmod +x /opt/mirror-sidecar.sh

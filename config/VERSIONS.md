# Pinned tool versions

Changes here trigger a fresh experimental batch; do not edit mid-experiment.

## Architecture

One image — `fortree` — used two ways: as the agent runtime (`docker run ... opencode run ...`) and as the scorer (`docker run --network=none ... /opt/entrypoint-scorer.sh`). Identical env in both roles.

## Host pins

Docker and `uv`, versions floating. See README for install steps.

## Image pins — `fortree` Docker image

Built from [Dockerfile](../Dockerfile).

| Tool        | Version                                                              | Source |
| ----------- | -------------------------------------------------------------------- | ------ |
| Base        | `python:3.11-slim-bookworm`                                          | Docker Hub. |
| OpenJDK     | 17 (Bookworm distro `openjdk-17-jre-headless`)                       | apt. |
| Josh CLI    | rolling main, sha256 `ef5f7ef9dc0bffbe2ed79c80fd6c0813db8120eb74bbf995cbc694c0de248984` | `https://joshsim.org/dist/main/joshsim-fat.jar` via [scripts/install_josh.sh](../scripts/install_josh.sh). |
| Python      | 3.11 (from the base image).                                          | Docker Hub. |
| Python pkgs | see [requirements.txt](requirements.txt).                            | pip into system Python. |
| opencode    | 1.14.50                                                              | upstream installer via [scripts/install_opencode.sh](../scripts/install_opencode.sh). |

# Pinned tool versions

Changes here trigger a fresh experimental batch; do not edit mid-experiment.

## Architecture

OpenShell's [docker compute driver](https://docs.nvidia.com/openshell/latest/reference/sandbox-compute-drivers#docker-driver) lets us hand it our own image instead of using the community sandbox. The same `fortree` image is used two ways: as the sandbox base for the agent (OpenShell injects the supervisor and launches opencode under it) and as the scorer (`docker run --network=none ...`). Identical env in both roles — that is the whole reason for the unification.

## Host pins

| Tool      | Version  | Source |
| --------- | -------- | ------ |
| OpenShell | 0.0.36   | `uv tool install openshell==0.0.36` |

Plus Docker and `uv` (versions floating; see README for install steps).

## Image pins — `fortree` Docker image

Baked into [Dockerfile](../Dockerfile).

| Tool        | Version                                                              | Source |
| ----------- | -------------------------------------------------------------------- | ------ |
| Python      | 3.11 (slim-bookworm)                                                 | `python:3.11-slim-bookworm` Docker image. |
| OpenJDK     | 17 (distro)                                                          | bookworm `openjdk-17-jre-headless`. |
| Josh CLI    | rolling main, sha256 `ef5f7ef9dc0bffbe2ed79c80fd6c0813db8120eb74bbf995cbc694c0de248984` | `https://joshsim.org/dist/main/joshsim-fat.jar` via [scripts/install_josh.sh](../scripts/install_josh.sh). |
| opencode    | 1.14.50                                                              | `https://opencode.ai/install --version 1.14.50` via [scripts/install_opencode.sh](../scripts/install_opencode.sh). |
| Python pkgs | see [requirements.txt](requirements.txt).                            | pip. |

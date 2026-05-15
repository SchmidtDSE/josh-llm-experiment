# Pinned tool versions

Changes here trigger a fresh experimental batch; do not edit mid-experiment.

## Architecture

OpenShell ships a [community base sandbox image](https://github.com/NVIDIA/OpenShell-Community/tree/main/sandboxes/base) that already satisfies the supervisor's readiness contract (the right users, sshd, the iproute2/nftables tooling the egress proxy uses, opencode preinstalled, etc.). Rather than reverse-engineer that contract into our own image, we `FROM` the community base and layer on what we need for the ForeverTree task.

One image — `fortree` — used two ways: as the sandbox base for the OpenShell agent (`openshell sandbox create --from .`) and as the scorer (`docker run --network=none ... /opt/entrypoint-scorer.sh`). Identical env in both roles.

## Host pins

| Tool      | Version  | Source |
| --------- | -------- | ------ |
| OpenShell | 0.0.36   | `uv tool install openshell==0.0.36` |

Plus Docker and `uv` (versions floating; see README for install steps).

## Image pins — `fortree` Docker image

Built from [Dockerfile](../Dockerfile).

### Inherited (from community base)

| Tool      | Source |
| --------- | ------ |
| Base OS   | `ghcr.io/nvidia/openshell-community/sandboxes/base:db19652` (NVIDIA/OpenShell-Community commit `db19652`, 2026-05-13). Re-pin by SHA tag to roll the inherited layer. |
| Ubuntu    | Noble 20251013 (via `nvcr.io/nvidia/base/ubuntu:noble-20251013`). |
| uv        | Whatever the base ships. Used to manage our Python 3.11 venv. |
| Node.js   | 22.22.1 (community base pin; needed for opencode). |
| sshd, iproute2, nftables, dnsutils, etc. | community base. Required by the OpenShell supervisor/egress proxy. |

### Layered on top (what this repo controls)

| Tool        | Version                                                              | Source |
| ----------- | -------------------------------------------------------------------- | ------ |
| OpenJDK     | 17 (Noble distro `openjdk-17-jre-headless`)                          | apt. |
| Josh CLI    | rolling main, sha256 `ef5f7ef9dc0bffbe2ed79c80fd6c0813db8120eb74bbf995cbc694c0de248984` | `https://joshsim.org/dist/main/joshsim-fat.jar` via [scripts/install_josh.sh](../scripts/install_josh.sh). |
| Python      | 3.11 (uv-managed venv at `/opt/fortree-venv`).                       | `uv venv --python 3.11`. Base ships 3.12 by default; ours coexists. |
| opencode    | 1.14.50                                                              | `npm install -g opencode-ai@1.14.50` (overrides the base's 1.2.18 pin). |
| Python pkgs | see [requirements.txt](requirements.txt).                            | pip into `/opt/fortree-venv`. |

`PATH` is prepended with `/opt/fortree-venv/bin` so `python` and `python3` resolve to our 3.11.

# Pinned tool versions

Changes here trigger a fresh experimental batch; do not edit mid-experiment.

## Host-side (orchestration layer)

Installed by [`scripts/install-host.sh`](../scripts/install-host.sh). The host
runs OpenShell directly — OpenShell is the agent sandbox boundary, so wrapping
it in another container layer would double-sandbox for no benefit.

| Tool      | Version  | Source                                                     |
| --------- | -------- | ---------------------------------------------------------- |
| uv        | latest   | `curl -LsSf https://astral.sh/uv/install.sh \| sh`         |
| OpenShell | 0.0.36   | `uv tool install openshell==0.0.36`                        |
| opencode  | 1.14.50  | `https://opencode.ai/install --version 1.14.50`            |

The agent-runtime layer (Python 3.11, JDK 17, Josh CLI, scientific deps) is
deferred to phase 3 — that is where the OpenShell sandbox policy starts
exposing those tools to the agent process.

## Scorer image ([Dockerfile.scorer](../Dockerfile.scorer))

Runs with `--network=none` + read-only workspace mount. Self-contained;
independent of host versions.

| Tool        | Version                                                              | Source |
| ----------- | -------------------------------------------------------------------- | ------ |
| Python      | 3.11 (slim-bookworm)                                                 | `python:3.11-slim-bookworm` Docker image. |
| OpenJDK     | 17 (distro)                                                          | bookworm `openjdk-17-jre-headless`. |
| Josh CLI    | rolling main, sha256 `ef5f7ef9dc0bffbe2ed79c80fd6c0813db8120eb74bbf995cbc694c0de248984` | `https://joshsim.org/dist/main/joshsim-fat.jar`. |
| Python pkgs | see [requirements.txt](requirements.txt).                            | pip. |

The scorer's Python and Java pins match what `scripts/install-host.sh` will
install on the host in phase 3, so scoring re-runs see the same library
behaviour the agent's `./run.sh` produced.

# Pinned tool versions

These are the tooling pins baked into [Dockerfile.scorer](../Dockerfile.scorer)
and [Dockerfile.sandbox](../Dockerfile.sandbox). Changes here trigger a fresh
batch tag; do not edit mid-experiment.

| Tool          | Version                                                    | Source |
| ------------- | ---------------------------------------------------------- | ------ |
| Python        | 3.11 (slim-bookworm for scorer, slim-trixie for sandbox)   | `python:3.11-slim-{bookworm,trixie}` Docker images. |
| OpenJDK       | 17 (distro openjdk-17 on scorer; Eclipse Temurin on sandbox) | bookworm `openjdk-17-jre-headless`; Adoptium `temurin-17-jre`. |
| opencode      | 1.14.50                                                    | `https://opencode.ai/install --version 1.14.50` |
| OpenShell     | 0.0.36                                                     | `uv tool install openshell==0.0.36` |
| Josh CLI      | rolling main, sha256 `ef5f7ef9dc0bffbe2ed79c80fd6c0813db8120eb74bbf995cbc694c0de248984` | `https://joshsim.org/dist/main/joshsim-fat.jar`. The Josh repo doesn't tag releases; the sha256 of the prod fat jar captured at image build time is the pinning record. |

Python package pins live in [requirements.txt](requirements.txt). Bookworm vs.
trixie split is because OpenShell needs glibc 2.39+ (manylinux_2_39), which
bookworm doesn't provide; the scorer doesn't need OpenShell so stays on the
older base with distro-provided JDK 17.

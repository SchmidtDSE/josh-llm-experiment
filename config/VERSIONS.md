# Pinned tool versions

These are the tooling pins baked into [Dockerfile.base](../Dockerfile.base),
[Dockerfile.scorer](../Dockerfile.scorer), and
[Dockerfile.sandbox](../Dockerfile.sandbox). Changes here trigger a fresh
batch tag; do not edit mid-experiment.

| Tool          | Version                                                    | Source |
| ------------- | ---------------------------------------------------------- | ------ |
| Python        | 3.11 (slim-trixie)                                         | `python:3.11-slim-trixie` Docker image, used by `Dockerfile.base`. |
| OpenJDK       | Eclipse Temurin 17 (JRE)                                   | Adoptium apt repo, package `temurin-17-jre`. Trixie dropped distro-provided openjdk-17; Adoptium fills the gap and pins us to the same upstream the user-facing Josh project recommends. |
| opencode      | 1.14.50                                                    | `https://opencode.ai/install --version 1.14.50` (sandbox image only). |
| OpenShell     | 0.0.36                                                     | `uv tool install openshell==0.0.36` (sandbox image only). Wheels require glibc 2.39+ which is what drove the trixie choice. |
| Josh CLI      | rolling main, sha256 `ef5f7ef9dc0bffbe2ed79c80fd6c0813db8120eb74bbf995cbc694c0de248984` | `https://joshsim.org/dist/main/joshsim-fat.jar`. SchmidtDSE/josh has no tagged releases; the sha256 of the prod fat jar captured at image build time is the pinning record. |

Python package pins live in [requirements.txt](requirements.txt).

## Image hierarchy

```
Dockerfile.base    → fortree-base    (Python 3.11 + Temurin 17 + Josh CLI + scientific stack)
Dockerfile.scorer  → fortree-scorer  (fortree-base + entrypoint-scorer.sh)
Dockerfile.sandbox → fortree-sandbox (fortree-base + uv + openshell + opencode + entrypoint-sandbox.sh)
```

Build order: `base` first, then the two leaves in either order. The scorer
inherits the same environment the sandbox uses to run agent-produced code, so
reference and agent code execute under identical Python/Java/Josh.

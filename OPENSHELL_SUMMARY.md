# OpenShell integration — debugging notes

A running log of what we tried, what we learned about OpenShell's architecture along the way, and where we stand right now. Written so a future contributor (or a future you, or a future Claude) doesn't re-tread the same ground.

The image and host bootstrap are validated; what remains is getting the OpenShell gateway *daemon* running on the local host so we can `openshell sandbox create --from .` against the unified `fortree` image. Everything from there is downstream work (phases 2–5 of [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)).

## Goal

One unified Docker image, two roles:
1. **Sandbox base** — what OpenShell launches the agent (opencode) inside.
2. **Scorer** — what `docker run --network=none` invokes for offline validation of the agent's output.

Same image both ways → byte-identical Python, Java, Josh, library versions in agent and scorer. No env drift to debug.

For (1) to work, the OpenShell gateway daemon needs to be running on the host and configured to use the **docker compute driver** (not k3s). The docker driver does plain `docker run` of our image with the supervisor binary bind-mounted in.

## OpenShell architecture (what we learned)

OpenShell ships as three binaries that have to be reasoned about separately:

| Binary | Role | Distribution |
|---|---|---|
| `openshell` | User-facing CLI. Talks to a gateway over gRPC. | pypi wheel, musl tarball, .deb / .rpm / Homebrew, source build. |
| `openshell-gateway` | Daemon that owns sandbox lifecycle. Schedules sandboxes via a compute driver. | Standalone tarball (glibc-linked), gateway docker image (`ghcr.io/nvidia/openshell/gateway:<tag>`), bundled in .deb / .rpm, source build. |
| `openshell-sandbox` | Supervisor that runs *inside* each sandbox container. Enforces Landlock + seccomp + egress proxy. Bind-mounted into the sandbox at launch time by the docker driver; the gateway resolves which version to inject automatically (the image tag is baked into the gateway binary at compile time). | Distributed alongside the gateway in release artifacts; pulled as a container image at runtime by the docker driver. |

Compute drivers in 0.0.37: `docker`, `podman`, `kubernetes`, `vm` (experimental). The gateway picks via `--drivers <name>` or `OPENSHELL_DRIVERS=<name>`. If neither is set, auto-detection order is `kubernetes → podman → docker`.

**Critical confusion we hit (twice)**: in v0.0.36 the pypi-installed `openshell` CLI shipped a `gateway start` subcommand that *deployed the gateway as a k3s cluster in a container*. That mechanism is **not** the docker driver — it's the kubernetes driver in disguise. v0.0.37 removed the `gateway start|stop|destroy` commands precisely because they were misleading: now the gateway is a proper system service that you install via .deb/.rpm/Homebrew/pypi, or run yourself as a docker container.

### Image we build (Path A′)

[`Dockerfile`](Dockerfile) inherits from `ghcr.io/nvidia/openshell-community/sandboxes/base:db19652` so we get NVIDIA's sandbox-readiness contract for free (supervisor + sandbox users, sshd, iproute2 / nftables for the egress proxy, opencode preinstalled). We layer on:

- OpenJDK 17 (Adoptium / distro)
- The Josh CLI (sha256-pinned fat jar)
- Python 3.11 in a venv at `/opt/fortree-venv` (base ships 3.12; ours coexists)
- opencode 1.14.50 via `npm install -g` (overrides the base's 1.2.18)
- `entrypoint-scorer.sh` for the scorer-role invocation
- `ENTRYPOINT []` so plain `docker run fortree python ...` works (base sets `/bin/bash` as ENTRYPOINT, which would otherwise prepend bash to every command)

Image is ~7.3 GB (community base is 4.8 GB, our layer adds 2.5 GB).

## Timeline of attempts

### Arch 1 — Docker-in-Docker (nested sandboxing) — *dropped*

First attempt put OpenShell *inside* a Docker image. The orchestrator would `docker run` our image and the entrypoint would call `openshell sandbox create` from within. Rejected because OpenShell *is* the sandbox boundary; nesting it inside Docker added cap-add complexity (`--cap-add SYS_ADMIN` for user namespaces, etc.) for no security gain.

### Arch 2 — Host OpenShell + Docker scorer ("Arch A") — *partially worked*

Host installs `openshell` + `opencode` + `uv` directly. Agent runs on the host under OpenShell's policy. Scorer is the only Docker artifact. Image: `python:3.11-slim-bookworm` + Adoptium JDK 17 + our deps (no community base inheritance yet).

- ✅ Host install + scorer image both built and validated (gates 2–6).
- ❌ `openshell sandbox create --from .` failed — minimal image didn't satisfy OpenShell's pod-readiness contract. Container scheduled, image pulled, pod refused to become Ready (no sshd, no supervisor user/group, etc.).

The 0.0.36 CLI's `gateway start` was deploying k3s under the hood, which has a documented but undocumented-to-us pod-readiness contract. That's what failed.

### Arch 3 — Single unified `fortree` image (Path A′) — *current*

Switched to `FROM ghcr.io/nvidia/openshell-community/sandboxes/base:db19652` and layered our additions. Now the readiness contract is satisfied by construction (we inherit everything NVIDIA's base provides) and the image is used for both sandbox and scorer roles. This is what's on the `phase-1-env-bootstrap` branch today.

Validation:
- ✅ Gates 1–6 pass on both the codespace and on a real machine (Linux Mint).
- ✅ `openshell sandbox create --from .` builds the image and pushes it into the gateway successfully.
- ❌ Pod readiness fails *after* the image is in the gateway. Two distinct failure modes:

#### Codespace failure mode — disk pressure

The k3s cluster running inside the gateway container tried to extract our 7.3 GB image into its containerd snapshot storage. The codespace's 32 GB partition (already 22 GB consumed by the OS, devcontainer, vscode-server) couldn't fit:

```
× ctr images import exited with code 1
ctr: failed to extract layer (sha256:8551a3ca…)
to overlayfs as "extract-…":
write /var/lib/rancher/k3s/.../snapshots/70/.../claude/versions/2.1.140:
no space left on device
```

Aggressive prune + retry → same error. **Documented as "deferred to a real host"**, validation moved local.

#### Local failure mode — Docker Desktop VM cap

On Linux Mint with Docker Desktop, `openshell sandbox create --from .` got further: image built, pushed (4.8 GB exported into the gateway), gateway logged "Image is available." Then the gRPC RPC dropped mid-flight with "Connection reset by peer."

Diagnostic showed the **`openshell-cluster-openshell` container had been *removed*** (not crashed, not restarted — gone). Found the root cause in journalctl:

```
[15:05:11.540324368Z][main.qemu] VM has started: qemu-system-x86_64 ... -m 16384 -smp 8
```

Docker Desktop on Linux runs all containers in a **16 GB qemu VM**. The host had 39 GB RAM available but the VM was capped at 16 GB. The k3s cluster + scheduling our heavy sandbox image overran that cap, Docker Desktop's OOM tracer killed the VM, and on restart the cluster container was gone.

**Three hosts, three k3s failures**: the pypi-distributed `openshell gateway start` pattern (which deploys k3s) has now tripped on a codespace (disk), a Docker Desktop VM on Linux (memory), and an earlier dev attempt (also disk). The k3s path is the wrong one for our workload.

### Pivot to v0.0.37 + docker driver

User pointed at the v0.0.37 release notes:

> Starting in v0.0.37 the gateway service is now managed as a proper system service setup by the system's package manager. […] On the runtime side, RFC-0001 is now substantially implemented with pluggable compute drivers for Docker, Podman, Kubernetes, and experimental MicroVM-backed sandboxes.

That fixes our entire problem class: drop k3s, run the gateway as a system service (or container), tell it `OPENSHELL_DRIVERS=docker`, and sandbox creation becomes plain `docker run` of our image. No qemu cap, no pod scheduling, no readiness probes.

Upgrade attempts on Linux Mint:

1. **`.deb` install via `install.sh`** → installed cleanly but the gateway binary failed: `GLIBC_2.38 not found, GLIBC_2.39 not found`. NVIDIA's .deb was built against glibc ≥ 2.39 (Ubuntu 24.04 / Debian trixie); Linux Mint Lemur is on glibc < 2.38 (Mint 21.x / Ubuntu 22.04). The release artifacts include a `musl` variant *for the CLI* but not for the gateway daemon, so the standalone tarball wouldn't help either.

2. **Docker-image gateway** (`ghcr.io/nvidia/openshell/gateway:0.0.37`, multi-arch confirmed) → currently in progress. Container runs in Docker Desktop's VM but is just a binary, not a full k3s cluster, so the memory profile is sane. Iterating through the gateway's required configuration:
   - ❌ TLS required by default. User pointed at [TLS handling best-practices doc](https://docs.nvidia.com/openshell/latest/security/best-practices#tls-handling); abandoned `--disable-tls` for the documented `generate-certs` mTLS bootstrap.
   - ❌ `OPENSHELL_DB_URL` required. Added `sqlite:/var/lib/openshell/gateway/openshell.db?mode=rwc`.
   - ❌ Cert generation fails with `Permission denied` writing to the host bind mount, even with `--user $(id -u):$(id -g)`. Suspected Docker Desktop virtiofs uid-mapping quirk.

## Where we are right now

**Active blocker**: gateway cert generation writes to its state directory, which is bind-mounted from the host. Docker Desktop on Linux Mint is rejecting the write despite explicit `--user` flag matching the host owner.

**Next step to try**: drop the bind mount entirely, use a **docker named volume** for gateway state. Docker manages permissions inside the volume; no host-fs uid translation. Trade-off: state isn't directly inspectable from the host without `docker run --rm -v <vol>:/data alpine`, but for a research harness that's fine.

```sh
docker volume create openshell-gateway-state

# Cert generation
docker run --rm \
  -v openshell-gateway-state:/var/lib/openshell \
  ghcr.io/nvidia/openshell/gateway:0.0.37 \
  generate-certs --output-dir /var/lib/openshell/tls --server-san localhost

# Actual gateway
docker run -d --name openshell-gw \
  --restart unless-stopped \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v openshell-gateway-state:/var/lib/openshell \
  -e OPENSHELL_DRIVERS=docker \
  -e OPENSHELL_BIND_ADDRESS=0.0.0.0 \
  -e OPENSHELL_SERVER_PORT=17670 \
  -e OPENSHELL_DB_URL=sqlite:/var/lib/openshell/gateway/openshell.db?mode=rwc \
  -e OPENSHELL_TLS_CERT=/var/lib/openshell/tls/server/tls.crt \
  -e OPENSHELL_TLS_KEY=/var/lib/openshell/tls/server/tls.key \
  -e OPENSHELL_TLS_CLIENT_CA=/var/lib/openshell/tls/ca.crt \
  -p 17670:17670 \
  ghcr.io/nvidia/openshell/gateway:0.0.37
```

After that, the CLI (musl tarball, since the .deb's CLI also has the glibc problem) needs to be told about the gateway with the matching client cert + CA. The CLI is presumably `openshell gateway add https://127.0.0.1:17670 --tls-cert ... --tls-key ... --tls-ca ...` — exact flag names to confirm by reading `openshell gateway add --help`.

Then the validation gate:

```sh
openshell sandbox create --from . --auto-providers -- josh --version
```

Should print the Josh sha256 from inside the sandbox.

## What we've simplified, and what's intrinsic complexity

Things we tried to simplify by removing them:

- **Dropped `Dockerfile.sandbox + Dockerfile.scorer`** → one `Dockerfile`.
- **Dropped `scripts/install-host.sh`** → README prose, "install these three things manually."
- **Dropped `scripts/install_opencode.sh`** → one `npm install -g` line in the Dockerfile (after switching to community-base inheritance, npm was already there).
- **Dropped from-source build of `openshell-gateway`** → use the published docker image instead.
- **Dropped the k3s-cluster-in-docker gateway** → either the v0.0.37 system service or the docker-image gateway.

Things that turned out to be **intrinsic complexity we have to absorb**:

- **The image is ~7.3 GB.** Driven by the community base (4.8 GB) — NVIDIA bundles Node, sshd, iproute2, nftables, etc. We can't shrink this without forking their base, and the inheritance is what gives us the supervisor's readiness contract for free.
- **Multiple bind-mounted state directories** (tls, gateway DB, eventually run logs). The gateway expects to write to them; permissions matter on Linux with non-root daemon usage.
- **mTLS is the default and is correct.** Following NVIDIA's `generate-certs` + cert env-var pattern is the documented path. We briefly tried `--disable-tls` and reverted on user pushback (correct — the docs warn it removes transport-level authentication).
- **OpenShell is alpha software pinned by version.** Behavior changes between point releases; v0.0.37 broke compatibility with v0.0.36 state (and was the change we wanted, because it fixed the k3s default).

## Open items

- Confirm the named-volume cert generation works on Linux Mint + Docker Desktop.
- Find the exact CLI flag names for `openshell gateway add` with client cert + CA. Likely `--tls-cert`, `--tls-key`, `--tls-ca` mirroring the gateway env-vars; confirm in `openshell gateway add --help` once we have a working CLI binary.
- Install the v0.0.37 CLI on Linux Mint. The .deb CLI also has the glibc 2.39 dependency, so use the musl tarball: `openshell-x86_64-unknown-linux-musl.tar.gz` (gateway daemon stays in the docker image; CLI is host-side).
- Update [`config/VERSIONS.md`](config/VERSIONS.md): bump OpenShell pin from `0.0.36` (host pypi install) to `0.0.37` (gateway docker image at `sha256:65c9dc5d…`, CLI from the musl tarball).
- Update [`README.md`](README.md) host prerequisites section to reflect the new install path — three artifacts (Docker, the CLI musl tarball, the gateway docker image) instead of "uv tool install openshell".
- Eventually, once the gateway is up: run `openshell sandbox create --from .` for the validation gate and document the result.

## Lessons we hope not to re-learn

- **Don't conflate the CLI with the gateway daemon.** They're separate binaries with separate distribution paths; problems with one are not problems with the other.
- **`openshell gateway start` in v0.0.36 is *not* the docker driver.** It deploys k3s. We spent meaningful time fighting k3s symptoms (DiskPressure, OOM-VM-restart) before identifying that this command was the wrong tool entirely.
- **The compute driver is chosen at the gateway level**, not the CLI level. You can't tell the v0.0.36 CLI's `gateway start` to use the docker driver — it's hard-coded to k3s. Upgrade to v0.0.37 to get a deployment model that exposes driver choice.
- **Glibc compatibility matters when consuming binary releases of Rust tools.** NVIDIA's .deb assumes a current-ish Ubuntu; if you're a release behind, the gateway binary won't load. The docker image is the portable fallback.
- **Docker Desktop on Linux is a qemu VM, not native Docker.** Memory and disk limits apply to the VM, not the host. For serious work, install native Docker Engine. We didn't, and learned the limits the hard way.
- **NVIDIA's docs are correct but not always discoverable.** Several times the right answer was hiding in a doc page we hadn't read closely enough. When stuck, re-read the relevant sections rather than guess.

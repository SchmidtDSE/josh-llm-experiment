# ForeverTree LLM Experiments

A controlled experiment harness for measuring how prompt detail affects
LLM-generated code when targeting a constrained domain-specific
language (Josh) versus a general-purpose agent-based framework (Mesa).

This repository runs the AI-evaluation experiments reported in our
USRSE'26 submission on the [Josh][josh] vegetation modeling platform.
This README covers installation and execution; see
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) for the hypothesis,
prompt-detail ladder, scoring methodology, egress-observability
rationale, and threats to validity.

> **Status (phase 1 complete).** The harness is being built out in
> phases; [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) is the
> source of truth for what is done and what is still to do. As of
> phase 1, the unified `fortree` Docker image builds and validates on
> a local host; the scoring harness, orchestration scripts, prompts
> rungs 1–5, and observation layer are not yet in the repo.
> References below marked *(planned)* describe the target shape.

[josh]: https://joshsim.org/

## Repository layout

Items marked *(planned)* will land in subsequent phases per
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Items without a marker
are present on the current branch.

```
.
├── README.md                     # This file (install + run)
├── EXPERIMENTAL_DESIGN.md        # Methodology, scoring, threats to validity
├── IMPLEMENTATION_PLAN.md        # Phase plan + current build status
├── Dockerfile                    # Unified fortree image (agent + scorer)
├── entrypoint-scorer.sh          # Dispatches to harness/run_metrics.py
├── .env.example                  # Copy to .env; OPENROUTER_API_KEY lives there
├── scripts/
│   ├── install_josh.sh           # Installs Josh CLI inside the image
│   └── install_opencode.sh       # Installs opencode inside the image
├── config/
│   ├── VERSIONS.md               # Pinned tool versions
│   ├── requirements.txt          # Pinned Python deps
│   ├── models.yaml               # (planned) short-name → OpenRouter ID map
│   ├── opencode.template.json    # (planned) per-run opencode config template
│   └── docs_categories.yaml      # (planned) URL → category tag, analysis-time
├── data/                         # Climate inputs (Tulare County, FGOALS-g3 / SSP2-4.5)
│   ├── precip_tulare_annual.nc
│   └── maxtemp_tulare_annual.nc
├── prompts/
│   ├── BASE_PROMPT.md            # Full ForeverTree spec (used as rung 5)
│   ├── SIDECAR.md                # Boilerplate footer appended to every rung
│   ├── PLAN_TEMPLATE.md          # Seed for /sandbox/PLAN.md (multi-invocation working doc)
│   ├── rungs/
│   │   └── rung1_minimal.md      # kept for future use; default RUNG is 5 (BASE_PROMPT.md)
│   ├── steps/
│   │   └── step_01..08_*.md      # Per-todo step injections (8 files, repo-static)
│   └── targets/
│       ├── josh.md
│       └── mesa.md
├── harness/                      # acceptance_ranges.json today;
│                                 # scoring entry point + runners + validators planned, phase 2b
├── orchestration/                # (planned, phase 3+) — launch_run.sh, launch_batch.py, dnsmasq.conf
├── .github/workflows/            # CI: smoke.yml (deterministic, every push) + integration.yml (workflow_dispatch, ollama or openrouter)
└── results/                      # (planned, phase 5) — per-run JSON manifests
```

## Running it

### Prerequisites

The host needs Docker and [uv][uv]. Install each manually:

```sh
# 1. Docker daemon (system-specific).
#    https://docs.docker.com/engine/install/

# 2. uv — Astral's single-binary Python tool installer.
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Build the three fortree images. The agent + scorer share a base
#    layer (Dockerfile) and differ only in payload; the dnsmasq sidecar
#    is built from a separate alpine-based Dockerfile.dnsmasq.
docker build --target agent  -t fortree:agent  .
docker build --target scorer -t fortree:scorer .
docker build -f Dockerfile.dnsmasq -t fortree:dnsmasq .
```

[uv]: https://docs.astral.sh/uv/

### Image-only sanity checks (works today)

After the prerequisites step, these commands work on the current
branch and validate the images' installed environments:

```sh
docker run --rm fortree:agent python -c \
  "import mesa, numpy, pandas, scipy, xarray, netCDF4, rasterio, tiktoken; print('ok')"
docker run --rm fortree:agent josh --version       # prints pinned sha256
docker run --rm fortree:agent opencode --version   # prints 1.14.50
docker run --rm --network=none fortree:agent python -c "print('offline')"
docker run --rm --env-file .env fortree:agent printenv OPENROUTER_API_KEY
docker run --rm fortree:dnsmasq --test             # validates dnsmasq.conf syntax
```

### Running the integration test locally

The agent path supports Ollama as an OpenRouter-free alternative. Useful
for replication without a paid API key, and for the `integration.yml`
CI workflow's ollama branch.

```sh
# 1. Install ollama on the host (https://ollama.com/download).
# 2. Pull the model used by the integration workflow:
ollama pull qwen2.5-coder:7b   # ~4.7 GB

# 3. Point the launcher at the host's ollama via .env. On Linux, the agent
#    container reaches the host through the docker bridge gateway
#    (typically 172.17.0.1). On macOS / Docker Desktop, use host.docker.internal.
cp .env.example .env
# edit .env:
#   OLLAMA_HOST=http://172.17.0.1:11434       # Linux
#   OLLAMA_HOST=http://host.docker.internal:11434  # macOS / Docker Desktop

# 4. Run.
MODEL=ollama-qwen-coder-7b RUNG=5 TARGET=mesa RUN_ID="$(uuidgen)" \
  ./orchestration/launch_run.sh
```

`config/models.yaml` also exposes `ollama-qwen-coder-1_5b` as a smaller
fallback for resource-constrained environments.

### CI

- `.github/workflows/smoke.yml` runs on every push and on PRs to `dev`/`main`.
  It builds `fortree:scorer` and asserts every fixture under `reference/`
  produces the expected scorer outcome — no model is invoked.
- `.github/workflows/integration.yml` is `workflow_dispatch`-only.
  Inputs `model`, `rung`, `target`. Dispatches on the model short-name:
  - `ollama-*` → free, spins up ollama on the runner. Slow on CPU.
  - anything else → uses OpenRouter; requires repo secret `OPENROUTER_API_KEY`.
  Runs the full agent → scorer → report pipeline. Uploads `runs/<id>/`
  as a workflow artifact, and posts the rendered `report.md` into the
  run's Summary tab for in-UI review.

### One-off local run *(planned, phase 5)*

```sh
./orchestration/launch_run.sh \
  --model claude \
  --target josh \
  --run-id "$(uuidgen)"
```

`launch_run.sh` will wrap the five-step flow: it brings up a per-run
Docker bridge network with a dnsmasq sidecar (query logging on),
runs opencode inside the pinned `fortree` image bound to that
network for step 1, runs the conformance and validation harnesses
against the workspace, invokes opencode again for the recovery step
if needed, and runs the final validation. It captures the dnsmasq
DNS log, all opencode trajectories, and the harness output, and
appends a row to `results/manifest.jsonl`.

### Running a cell end-to-end

A "cell" is one experimental point: model × rung × target × one run ID,
end-to-end through agent → scorer → report. The same code path runs
under CI and under the local batch driver.

```sh
MODEL=claude RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) \
  ./orchestration/launch_cell.sh
```

Output lands in `runs/<RUN_ID>/`: the agent workspace, `scorer.json`,
`report.md`, `dns.log`, the opencode trajectory + session export, and
`run_meta.cell.json` (per-step pass/fail).

### Running a batch locally

`launch_batch.py` fans out N cells concurrently on the local host
using a `ThreadPoolExecutor` over `launch_cell.sh` subprocesses. Each
cell owns its own bridge network + dnsmasq sidecar (named by
`RUN_ID`); the only shared mount is read-only `data/`. Two CLI forms:

```sh
# Single cell × N replicates
./orchestration/launch_batch.py \
  --model claude --rung 5 --target josh --runs 4 --jobs 4

# Matrix via CSV (header: model,rung,target,replicates; '#' comments OK)
./orchestration/launch_batch.py --cells cells.csv --jobs 8
```

While the batch runs, the orchestrator shows a live panel with the
cells currently in flight + their lifecycle phase
(`AGENT → SCORE → REPORT → DONE`, derived by polling each run dir for
which artifacts exist), a progress bar with rough ETA, and a per-cell
✔/✗ scroll above the panel. When a cell fails, the last ~20 lines of
its log are dumped inline so you don't have to navigate to find the
cause. In non-TTY contexts (CI, redirects, piping to `tee`), the live
panel auto-disables and you get clean line-oriented output instead.

Per-cell outputs go to `runs/<RUN_ID>/` (same layout as a single cell).
Batch metadata goes to a sibling `runs/<BATCH_TAG>/`:

| File                     | Contents |
| ------------------------ | -------- |
| `worklist.tsv`           | `model rung target run_id` per cell |
| `joblog.tsv`             | Per-cell `seq model rung target run_id started_at runtime_s exit_code` |
| `manifest.jsonl`         | One JSON object per cell with `run_meta` + `cell` (step statuses) + full `scorer.json`, preserved in worklist order |
| `summary.txt`            | Totals: succeeded / failed / concurrency |
| `cell-logs/<run_id>.log` | Per-cell stdout+stderr capture (so concurrent cells don't interleave on the terminal) |

Concurrency caps and what binds them, roughly worst-binding first:

- **OpenRouter rate / concurrency limits per key.** Start at `--jobs 4`
  on a paid sweep; tune up watching for 429s in `runs/<id>/agent_stderr.log`.
- **Memory.** Each cell is ~0.5-1.5 GB resident (JVM spikes during
  `josh parse`). A 64 GB host fits ~30 concurrent comfortably.
- **Docker default bridge subnets** allow ~31 concurrent
  `fortree-run-*` networks before allocation churn. Above that,
  widen `default-address-pools` in `/etc/docker/daemon.json`.
- **CPU** is rarely binding: most wall time is API wait, with short
  bursts during `./run.sh` and JVM startup.

Host prerequisites for the orchestrator: Docker, Python 3.11+, and
[uv][uv]. `pyproject.toml` at the repo root declares `pyyaml` and
`rich` (the latter powers the live UI and auto-degrades to plain
output in non-TTY contexts; the import itself is required). One-time
setup on a fresh host:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv isn't already there
uv sync                                            # installs pyyaml + rich into .venv
```

Then invoke the orchestrator through uv so the venv is picked up
automatically:

```sh
uv run orchestration/launch_batch.py --cells cells.csv --jobs 8
```

The batch driver also runs a pre-sweep cleanup of any orphan
`fortree-run-*` networks / `dnsmasq-*` containers left behind by a
prior SIGKILL'd batch, so a hard-kill of the driver is recoverable —
the next `launch_batch.py` invocation scrubs whatever leaked.

### Archiving a batch to GCS (S3 interop)

Add `--upload` to `launch_batch.py` and the batch driver invokes
`orchestration/upload_batch.sh` against the completed batch dir at
the end of the run:

```sh
uv run orchestration/launch_batch.py --cells headline_panel.csv \
  --jobs 4 --batch-tag headline-2026-05 --upload
```

For crash recovery (or to opt into the upload after-the-fact), run
the standalone script directly:

```sh
./orchestration/upload_batch.sh runs/batch-experimental_cells-panel
```

Both paths read `MINIO_ENDPOINT` / `MINIO_BUCKET` / `MINIO_ACCESS_KEY` /
`MINIO_SECRET_KEY` / `MINIO_PREFIX` from `.env`. Uses the [`mc` MinIO
client][mc] host-side — the agent container is not in the upload path,
so credentials never touch the runner, and the agent image stays free
of the `mc` binary. The script is idempotent (`mc mirror --overwrite`)
so re-running on the same batch dir just syncs whatever changed.
Auto-upload failure is non-fatal: the batch artefacts stay on disk,
and the standalone script can recover.

[mc]: https://min.io/docs/minio/linux/reference/minio-mc.html

Requires `mc` on the host. Linux one-liner:

```sh
curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc -o ~/.local/bin/mc
chmod +x ~/.local/bin/mc
```

Object layout under the bucket:
- `<prefix>/<batch-tag>/<run-id>/…` — per-cell artefacts
- `<prefix>/<batch-tag>/batch_report.md` — per-batch report
- `<prefix>/<batch-tag>/manifest.jsonl` — aggregated manifest

### Running a batch on GKE (phase 6+)

The Phase 6 refactor replaces the local-Docker launcher with **one
k8s Job per cell** on GKE Autopilot. Each Job has shape:

| Container        | Phase       | Role |
| ---------------- | ----------- | ---- |
| `setup`          | initContainer | Seeds `/cell-data` (shared `emptyDir`) from a per-cell `ConfigMap`. |
| `agent`          | initContainer | `fortree:agent`; runs the 8-step opencode flow against `/cell-data/workspace`. |
| `scorer`         | container   | `fortree:scorer`; runs the scoring harness, then `mc mirror`s the whole `/cell-data` tree to GCS. |

Egress is monitored (opencode `trajectory.jsonl`), not enforced — see
[EXPERIMENTAL_DESIGN.md §Egress observability](EXPERIMENTAL_DESIGN.md).

#### Cluster prerequisites *(infra layer, already provisioned for `dse-nps`)*

A fork would need to replicate:

- GKE Autopilot cluster `josh-k8s-gke` in `us-west1`.
- Namespace `joshsim` + KSA `joshsim-batch` with RBAC to create/delete
  Secrets, create/get/list/watch Jobs, get/list Pods.
- NetworkPolicy restricting pod egress to ports 53 (DNS) + 443 (HTTPS).
- GCS bucket `dse-nps-josh-batch-storage` (US multi-region, uniform
  bucket-level access).
- A workload SA (`josh-k8s-gcs-sa@dse-nps`) with `storage.objectAdmin`
  on that bucket; HMAC keys minted off it and stored in Secret Manager
  as `josh-k8s-minio-access-key` + `josh-k8s-minio-secret-key`.

Terraform for the above lives in the infra repo under
`environments/josh-k8s/`.

#### Operator setup *(one-time per workstation or dev VM)*

```sh
# 1. kubectl + the GKE-specific auth plugin via Google's apt repo.
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
  | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get update
sudo apt-get install -y kubectl google-cloud-cli-gke-gcloud-auth-plugin

# 2. Authenticate gcloud (personal account; a GCE-attached SA with the
#    right roles also works, but the default `josh-dev-compute-sa` lacks
#    `container.clusters.get` so personal-account login is simpler).
gcloud auth login
gcloud config set project dse-nps

# 3. Fetch cluster credentials → writes a kubeconfig context.
gcloud container clusters get-credentials josh-k8s-gke \
  --region us-west1 --project dse-nps

# 4. Verify.
kubectl get ns joshsim
```

#### Repo-level one-time setup *(after the build-images workflow first lands)*

```sh
# 1. The build-images CI auto-runs on push to feat/k8s-** branches and
#    to dev / feat/k8s-refactor. To rebuild manually:
gh workflow run build-images.yml --ref dev -R SchmidtDSE/josh-llm-experiment

# 2. Flip GHCR package visibility to Public so the cluster pulls without
#    auth — one-time, per package, via the GitHub UI:
#      https://github.com/orgs/SchmidtDSE/packages/container/josh-llm-experiment%2Ffortree-agent/settings
#      https://github.com/orgs/SchmidtDSE/packages/container/josh-llm-experiment%2Ffortree-scorer/settings
#    Verify locally:
docker pull ghcr.io/schmidtdse/josh-llm-experiment/fortree-agent:latest

# 3. Create the two long-lived Secrets in the joshsim namespace, sourced
#    from .env. Idempotent (the dry-run/apply pattern updates in place).
set -a; . .env; set +a
kubectl create secret generic minio-creds -n joshsim \
  --from-literal=endpoint="${MINIO_ENDPOINT}" \
  --from-literal=bucket="${MINIO_BUCKET}" \
  --from-literal=access-key="${MINIO_ACCESS_KEY}" \
  --from-literal=secret-key="${MINIO_SECRET_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic openrouter-creds -n joshsim \
  --from-literal=api-key="${OPENROUTER_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -
```

The HMAC pair in `minio-creds` mirrors the values in Secret Manager
(`josh-k8s-minio-{access,secret}-key`); the project-level `.env` is the
operational source of truth so the same credentials work for both the
host-side `upload_batch.sh` and the in-Pod scorer.

#### Per-batch flow

```sh
# 1. Push the branch you want to ship from. Auto-triggers a build for
#    feat/k8s-** branches (paths-filtered to Dockerfile / harness /
#    entrypoints / etc.). Capture the short SHA — that's the image tag.
git push
SHA=$(git rev-parse --short HEAD)
gh run watch -R SchmidtDSE/josh-llm-experiment   # block until green

# 2. Render + apply a smoke or panel.
./orchestration/k8s_apply.sh \
  --batch-tag "$(date -u +%Y%m%d)-smoke" \
  --image-agent  "ghcr.io/schmidtdse/josh-llm-experiment/fortree-agent:${SHA}" \
  --image-scorer "ghcr.io/schmidtdse/josh-llm-experiment/fortree-scorer:${SHA}" \
  --single-cell model=sonnet,target=josh

# Or a full matrix (CSV with columns `model,target`):
./orchestration/k8s_apply.sh \
  --batch-tag "headline-$(date -u +%Y%m%d)" \
  --image-agent  "ghcr.io/schmidtdse/josh-llm-experiment/fortree-agent:${SHA}" \
  --image-scorer "ghcr.io/schmidtdse/josh-llm-experiment/fortree-scorer:${SHA}" \
  --matrix orchestration/matrix.csv

# 3. Watch (one Pod per cell):
kubectl -n joshsim get jobs   -l batch-tag=<batch-tag> -w
kubectl -n joshsim logs       -l batch-tag=<batch-tag> -c agent  --tail=-1 -f --max-log-requests=20
# After the agent exits, the scorer starts:
kubectl -n joshsim logs       -l batch-tag=<batch-tag> -c scorer --tail=-1 -f --max-log-requests=20

# 4. Pull artefacts back from the bucket for analysis.
MINIO_PREFIX=<batch-tag> ./orchestration/pull_artefacts.sh <batch-tag>
ls runs/<batch-tag>/                         # one subdir per cell
```

`orchestration/k8s_apply.sh` writes the rendered manifests to
`orchestration/rendered/<batch-tag>/<cell-id>.yaml` (gitignored) before
applying — useful for inspection or for re-applying by hand. Pass
`--no-apply` to render only.

### Re-scoring a completed batch

The scoring container is target-agnostic and stateless against an
agent's `workspace/`, so a methodology revision (acceptance ranges,
new consistency metric, etc.) can be applied to a frozen batch without
re-running the agents. See
[SCORING.md §Re-analysing-completed-runs](SCORING.md#re-analysing-completed-runs)
for the methodology side; this is the operator interface.

The rescore mirrors `launch_batch.py`'s shape for the forward path:
[`orchestration/rescore_batch.py`](orchestration/rescore_batch.py) is
the batch driver, with
[`orchestration/rescore_cell.sh`](orchestration/rescore_cell.sh) as
the per-cell scorer invocation (analog of `launch_cell.sh` Step 2 in
isolation).

```sh
# Whole batch — discovers cells from run_meta.json under the batch dir
uv run orchestration/rescore_batch.py runs/batch-headline-2026-05

# One cell at a time (good for iterating on a methodology revision)
uv run orchestration/rescore_batch.py runs/batch-headline-2026-05 \
  --cell <run-id>

# Archive a methodology revision under its own suffix
uv run orchestration/rescore_batch.py runs/batch-headline-2026-05 \
  --suffix .phase5b-v1

# Parallel resume after a host crash
uv run orchestration/rescore_batch.py runs/batch-headline-2026-05 \
  --jobs 8 --skip-existing
```

#### What gets overwritten

Outputs land alongside the originals under the suffix (default
`.rescored`). Everything not in the left column stays untouched:

| Touched                                                | Not touched (originals)                                            |
|---|---|
| `<run-id>/scorer.rescored.json`                        | `<run-id>/scorer.json`                                             |
| `<run-id>/scorer.rescored.stderr`                      | `<run-id>/report.md`, `trajectory.jsonl`, `run_meta*.json`, …      |
| `<run-id>/workspace/output/results.csv` (re-executed)  | `<batch>/manifest.jsonl`                                           |
| `<run-id>/workspace/results/scorer.json` (legacy dup)  | `<batch>/batch_report.md`                                          |
| `<batch>/joblog.rescored.tsv`                          | `<batch>/joblog.tsv`                                               |
| `<batch>/manifest.rescored.jsonl`                      |                                                                    |
| `<batch>/batch_report.rescored.md`                     |                                                                    |

The two "touched workspace" rows deserve attention: the scorer's
[`harness/runner.py`](harness/runner.py) re-executes the agent's
`./run.sh` to get a fresh `output/results.csv`, then scores it. For
Josh targets the CSV is byte-identical to the original. For Mesa
targets it drifts at the 10⁻¹¹ scale (the stochastic-Gaussian noise
term inside the agent's script). The canonical frozen-evidence record
is `<run-id>/scorer.json` at the batch root — the workspace dup at
`workspace/results/scorer.json` is just a convenience the scoring
container writes inside the workspace and is not the source of truth.

Re-running with the same `--suffix` overwrites the suffixed files
in-place; `--skip-existing` short-circuits cells that already have a
non-empty `scorer<SUFFIX>.json`.

#### Interaction with `upload_batch.sh`

A rescore does NOT auto-upload — `--upload` is a `launch_batch.py`
flag, not a rescore flag. To archive rescored artefacts to the bucket:

```sh
./orchestration/upload_batch.sh runs/batch-headline-2026-05
```

`mc mirror --overwrite` (no `--remove`) means all the new suffixed
files get uploaded **and** the mutated workspace bytes overwrite their
bucket counterparts. The bucket's `scorer.json`, `manifest.jsonl`,
`batch_report.md`, `joblog.tsv`, and per-cell `report.md` stay
untouched — those names don't exist in the rescore output.

If you want the bucket archive to preserve the original `workspace/`
byte-for-byte: **upload before the first rescore**, then keep rescored
artefacts local (skip the second `upload_batch.sh` call). The
methodology-revision artefacts (`scorer.rescored.json`, etc.) stay in
the local run dir; the bucket remains a snapshot of the original
headline batch. For Josh targets the workspace mutation is a no-op so
this matters less; for Mesa targets it's the 10⁻¹¹-scale drift, which
is below any meaningful gate threshold but means the bucket diverges
bit-for-bit from the agent's original output if you re-upload after
rescoring.

### Optional host-side opencode (for LLM-judge passes)

The mechanical scorer is fully containerised — no host opencode
required for headline-batch scoring. But the post-hoc LLM-judge
described in [SCORING.md §LLM-judge passes](SCORING.md#llm-judge-passes-post-hoc-for-our-convenience)
runs opencode against completed runs directly from the host (no new
container per cell; same `OPENROUTER_API_KEY` from `.env`). To enable
that path, install opencode on the host at the same pinned version
the image uses (1.14.50):

```sh
curl -fsSL https://opencode.ai/install \
  | bash -s -- --version 1.14.50 --no-modify-path
# adds opencode to $HOME/.opencode/bin/opencode
export PATH="$HOME/.opencode/bin:$PATH"   # add to your shell profile
opencode --version  # → 1.14.50
```

Same upstream installer the Dockerfile invokes inside the `base`
stage (via [scripts/install_opencode.sh](scripts/install_opencode.sh)),
so the host opencode is byte-identical to what the agent container
uses — no version skew between agent runs and judge runs.

This is genuinely optional: only needed if you intend to run
`orchestration/run_fuzzy_judge.sh` (the spec lives in SCORING.md;
the script itself is a deferred-implementation item).

### Full sweep *(planned, phase 6+)*

The rung-ladder was retired; headline runs are `model × target ×
replicates` and live in a single committed matrix CSV. Commit the
panel description, then launch it in one invocation:

```sh
./orchestration/launch_batch.py \
  --cells headline_panel.csv --jobs 4 \
  --batch-tag headline-2026-05
```

`headline_panel.csv` columns are `model,rung,target,replicates`
(header mandatory; `#` comment lines OK; column order fixed). One row
per cell:

```csv
model,rung,target,replicates
claude,5,josh,3
claude,5,mesa,3
gemma,5,josh,3
gemma,5,mesa,3
kimi,5,josh,3
kimi,5,mesa,3
minimax,5,josh,3
minimax,5,mesa,3
mistral,5,josh,3
mistral,5,mesa,3
```

### Pilot sweep *(planned, phase 6)*

Before the headline run, a small pilot validates the loop and factors
across the model panel — same shape, fewer rows / replicates:

```sh
./orchestration/launch_batch.py \
  --cells pilot_panel.csv --jobs 2 \
  --batch-tag pilot-2026-05
```

```csv
model,rung,target,replicates
claude,5,josh,2
claude,5,mesa,2
mistral,5,josh,2
mistral,5,mesa,2
```

## Environment variables

`OPENROUTER_API_KEY` is the only one consumed today (loaded from `.env`).
The rest are the target shape for the phase-5 orchestrator and are
listed here so the variable contract is visible from the start.

| Variable                 | Used since | Required | Purpose |
| ------------------------ | ---------- | -------- | ------- |
| `OPENROUTER_API_KEY`     | phase 1    | conditional | API key for the OpenRouter gateway. Required when `MODEL` is an `openrouter/*` short name (all of `claude`, `gemma`, `kimi`, `minimax`, `mistral`). |
| `OLLAMA_HOST`            | phase 4    | conditional | Base URL of an Ollama server (default `http://localhost:11434`). Required when `MODEL` is an `ollama-*` short name; ignored otherwise. From inside the agent container on Linux, point this at the docker bridge gateway (typically `http://172.17.0.1:11434`); on Docker Desktop / macOS, `http://host.docker.internal:11434`. |
| `MODEL`                  | phase 3    | yes      | Short name from `config/models.yaml`. |
| `RUNG`                   | phase 3    | no       | Prompt rung. Defaults to 5 (the master spec). Rung 1 (`prompts/rungs/rung1_minimal.md`) is wired but unused by headline runs; the rung-ladder was retired in favour of the single-prompt panel. |
| `TARGET`                 | phase 3    | yes      | `josh` or `mesa`. |
| `RUN_ID`                 | phase 3    | yes      | Unique identifier for this generation. UUID preferred. |
| `MINIO_ENDPOINT`         | phase 4d   | yes for upload | S3-compatible endpoint. Default `https://storage.googleapis.com` — we hit GCS via its S3 interop API; no MinIO server runs anywhere, `mc` is just the client. Read by `orchestration/upload_batch.sh` (host-side, post-batch); the agent container does not see these vars. |
| `MINIO_BUCKET`           | phase 4d   | yes for upload | Destination bucket name for per-run artifact archival. |
| `MINIO_ACCESS_KEY`       | phase 4d   | yes for upload | HMAC access key for the bucket (GCS HMAC pair). |
| `MINIO_SECRET_KEY`       | phase 4d   | yes for upload | HMAC secret. |
| `MINIO_PREFIX`           | phase 4d   | no       | Optional object-key prefix appended after the bucket (e.g. `fortree/2026-05/`). Batch dir name is always appended after this. |
| `WALL_CLOCK_BACKSTOP_SEC`| phase 3    | no       | Hard ceiling on agent wall time for the full 8-step multi-invocation chain. Default 1800. |
| `IDLE_THRESHOLD_SEC`     | phase 3    | no       | Kill the agent if no new trajectory event lands for this many seconds. Default 120. Catches silent LLM-stream stalls distinct from the wall-clock backstop. |
| `TOKEN_BACKSTOP`         | phase 3    | no       | Completion-token cap per opencode invocation (per step). Default 100000. |
| `SKIP_FUZZY_CONFORMANCE` | phase 5    | no       | Skip the optional LLM-judge target check. Default false; set true for cost-sensitive runs. |
| `FAIL_FAST_ON_STEP_ERROR`| phase 5c   | no       | Multi-invocation failure mode. `false` (default, production): per-step failures are logged and the loop continues. `true` (dev/CI): the first non-zero opencode exit aborts the loop — surfaces broken plumbing fast. |

## Authentication

A single OpenRouter API key covers the entire model panel.
`OPENROUTER_API_KEY` is loaded from a gitignored `.env` and passed to
the agent container with `docker run --env-file .env`. opencode
reads it from its rendered `opencode.json` *(planned, phase 3)*.

opencode is configured to use OpenRouter as its only provider; the
rendered config pins the resolved OpenRouter model slug per run.
The `WebFetch` tool allowlist *(planned, phase 3)* permits
`openrouter.ai` alongside the documentation hosts so the inference
call can be made out of the container.

Sample rendered `opencode.json` *(target shape for phase 3)*:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "openrouter": { "apiKey": "${OPENROUTER_API_KEY}", "disabled": false }
  },
  "disabled_providers": ["openai", "anthropic", "google", "groq"],
  "agents": {
    "coder": {
      "model": "openrouter/${RESOLVED_MODEL_ID}",
      "tools": {
        "read":  { "enabled": true },
        "write": { "enabled": true, "scope": "${WORKSPACE}" },
        "edit":  { "enabled": true, "scope": "${WORKSPACE}" },
        "glob":  { "enabled": true },
        "grep":  { "enabled": true },
        "bash":  {
          "enabled": true,
          "allow": ["./run.sh", "ls", "cat", "head", "tail", "find", "wc", "tree"]
        },
        "webfetch": {
          "enabled": true,
          "allow_hosts": [
            "joshsim.org", "mesa.readthedocs.io", "docs.python.org",
            "numpy.org", "docs.scipy.org", "pandas.pydata.org",
            "docs.xarray.dev", "unidata.github.io", "openrouter.ai"
          ]
        }
      }
    }
  }
}
```

opencode's tool allowlists are the policy layer; the dnsmasq sidecar
on the agent's bridge network is the passive tripwire that records
every DNS query the container makes, so any host the agent reaches
shows up in the per-run log regardless of which tool triggered it.

## Reproducibility

- Pinned opencode, Josh, and Python-stack versions in
  [`config/VERSIONS.md`](config/VERSIONS.md). Upgrade requires a fresh
  experimental batch.
- Pinned unified [`Dockerfile`](Dockerfile) (used both as agent
  runtime and as the `--network=none` scorer), tagged per batch.
- Josh CLI sha256 captured at image build time (no tagged releases
  upstream).
- Pinned model IDs in `config/models.yaml` *(planned, phase 3)*. Drift
  will be logged when a provider returns a different `model_id` than
  requested.
- Pre-registered acceptance ranges committed to Git before any runs
  *(planned, phase 2)*.
- Per-run manifests in `results/manifest.jsonl` will be append-only
  and committed *(planned, phase 5)*. Each entry will record the
  prompt rung, model, target, resolved model ID, OpenRouter cost,
  all metrics, dnsmasq DNS log path, and S3 URIs for the
  artifacts. The artifact store is the project's existing GCS
  bucket accessed via the S3 interoperability API; uploads happen
  per-run from `orchestration/upload_run.sh` *(planned, phase 4)*.
- Prompt files versioned in Git; any change forces a new batch tag.

## License

BSD 3-Clause. See [`LICENSE`](LICENSE).

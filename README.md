# ForeverTree LLM Experiments

A controlled experiment harness for measuring how prompt detail affects
LLM-generated code when targeting a constrained domain-specific
language (Josh) versus a general-purpose agent-based framework (Mesa).

This repository runs the AI-evaluation experiments reported in our
USRSE'26 submission on the [Josh][josh] vegetation modeling platform.
This README covers installation and execution; see
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) for the hypothesis,
scoring methodology, egress-observability rationale, and threats to
validity.

> **Execution path.** Each cell runs as one k8s Job on GKE Autopilot.
> The agent and scorer images are built by
> [`.github/workflows/build-images.yml`](.github/workflows/build-images.yml)
> and pulled by the cluster; per-cell artefacts land in the GCS
> bucket via an in-Pod `mc mirror` from the scorer container.
> Methodology, scoring axes, and threats to validity are in
> [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md).

[josh]: https://joshsim.org/

## Repository layout

```
.
├── README.md                     # This file (install + run)
├── EXPERIMENTAL_DESIGN.md        # Methodology, scoring axes + metrics, LLM-judge, re-scoring, threats to validity
├── Dockerfile                    # Unified fortree image (agent + scorer)
├── containers/                   # Container-entrypoint shell scripts (baked into the images)
│   ├── agent-entrypoint.sh       # fortree:agent entry — runs the 8-step opencode flow
│   ├── entrypoint-scorer.sh      # fortree:scorer entry — dispatches to harness/run_metrics.py
│   ├── scorer-and-upload.sh      # k8s scorer entry — scores then `mc mirror`s to GCS
│   ├── run-judge.sh              # k8s scorer entry — in-Pod fuzzy LLM judge (Q1/Q2/Q3)
│   ├── mirror-sidecar.sh         # k8s sidecar — continuous mc mirror for OOM forensics
│   └── agent-run.sh.seed         # `./run.sh` seed installed by the agent prelude
├── .devcontainer/                # Pixi-based devcontainer (DinD + kubectl + gcloud + mc)
├── pixi.toml                     # Host-side env for the k8s submission path
├── .env.example                  # Copy to .env; OPENROUTER_API_KEY + MINIO_* live there
├── scripts/                      # In-image installers (install_josh.sh, install_opencode.sh, install_mc.sh, …)
├── config/
│   ├── VERSIONS.md               # Pinned tool versions
│   ├── requirements.txt          # Pinned Python deps baked into the image
│   ├── models.yaml               # Short-name → OpenRouter ID map
│   ├── opencode.template.json    # Per-run opencode config template
│   ├── opencode.judge.json       # Read-only judge config used by containers/run-judge.sh
│   └── docs_categories.yaml      # URL → category tag, analysis-time
├── data/                         # Synthetic climate inputs + their generator, reference simulator, and CF-validator
│   ├── generate_synthetic_climate.py  # writes the netCDFs (deterministic, CF-1.8)
│   ├── maxtemp_synthetic.nc           # temperature forcing (K)
│   ├── precip_synthetic.nc            # precipitation flux (kg m⁻² s⁻¹)
│   ├── reference_sim.py               # spec-faithful simulator → harness/acceptance_ranges.json
│   └── validate_synthetic_climate.py  # IOOS compliance-checker harness
├── prompts/
│   ├── BASE_PROMPT.md            # Full ForeverTree spec (the only rung used by headline runs)
│   ├── SIDECAR.md                # Boilerplate footer appended to BASE_PROMPT (env, inputs, output contract)
│   ├── RUNSH.md                  # run.sh contract; appended for the full-tools arms only (josh, mesa)
│   ├── plans/{bash,mcp}/PLAN_TEMPLATE.md  # Seed for /sandbox/PLAN.md — bash (full-tools) vs mcp (constrained) variants
│   ├── FUZZY_JUDGE.md            # Q1/Q2/Q3 rubric for the in-Pod LLM judge
│   ├── steps/                    # Per-todo step injections (8 files, repo-static)
│   └── targets/{josh,mesa,josh-mcp}.md  # Per-target directive
├── harness/                      # Scoring entry point (run_metrics.py) + validators + acceptance ranges
├── orchestration/                # k8s submission surface — render_jobs.py, k8s_apply.sh, pull_artefacts.sh, templates/job.yaml.j2, matrix.csv
├── analysis/                     # aggregate.py + numbered notebooks (00_apply_scoring, 01_analysis, 02_runtime_outliers, 03_manuscript_claims); aggregated.csv committed
├── reference/                    # Golden fixtures consumed by smoke CI
└── .github/workflows/            # CI: smoke.yml (every push) + build-images.yml (GHCR builds)
```

## Running it

### Image-only sanity checks

The unified fortree image builds both stages (agent, scorer) off the
same base. Manual builds are rarely needed — CI pushes both to GHCR
on every push to `dev` / `feat/k8s-**` (see
[`.github/workflows/build-images.yml`](.github/workflows/build-images.yml)) —
but to validate the installed environment locally:

```sh
docker build --target agent  -t fortree:agent  .
docker build --target scorer -t fortree:scorer .

docker run --rm fortree:agent python -c \
  "import mesa, numpy, pandas, scipy, xarray, netCDF4, rasterio, tiktoken; print('ok')"
docker run --rm fortree:agent josh --version       # prints pinned sha256
docker run --rm fortree:agent opencode --version   # prints 1.14.50
docker run --rm --network=none fortree:agent python -c "print('offline')"
```

### CI

- [`.github/workflows/smoke.yml`](.github/workflows/smoke.yml) runs on
  every push and on PRs to `dev` / `main`. It builds `fortree:scorer`
  and asserts every fixture under `reference/` produces the expected
  scorer outcome — no model is invoked.
- [`.github/workflows/build-images.yml`](.github/workflows/build-images.yml)
  builds + pushes `fortree-agent` and `fortree-scorer` to GHCR on push
  to `dev` / `feat/k8s-**` branches, and on manual dispatch.

### Running a batch on GKE

Each cell runs as **one k8s Job** on GKE Autopilot, with shape:

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

Terraform for the above lives in the infra (`SchmidtDSE/fire-recovery-iac`) repo under
`environments/josh-k8s/`.

#### Operator setup *(one-time per workstation or dev VM)*

**Recommended: open the repo in the devcontainer** —
[.devcontainer/devcontainer.json](.devcontainer/devcontainer.json) is a
Debian + pixi image preloaded with docker-in-docker, gh, gcloud +
gke-gcloud-auth-plugin, kubectl, and mc. Open in VS Code → "Reopen in
Container" (or `devcontainer up` from the CLI). The devcontainer's
`postCreateCommand` ([.devcontainer/post-create.sh](.devcontainer/post-create.sh))
installs the GKE auth plugin and `mc` (via
[scripts/install_mc.sh](scripts/install_mc.sh)) and runs `pixi install`,
all automatically.

If you opened in the devcontainer, skip directly to step 2 below
(`gcloud auth login`). If you're working on a bare host without a
devcontainer, do all four steps:

```sh
# 1. kubectl + the GKE-specific auth plugin via Google's apt repo.
#    (Skip if you opened the devcontainer — both are preinstalled.)
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

Once `kubectl get ns joshsim` returns the namespace, the common host
verbs are available as pixi tasks:

```sh
pixi run render -- --batch-tag X --image-agent ... --image-scorer ... --single-cell model=sonnet,target=josh
pixi run apply  -- --batch-tag X --image-agent ... --image-scorer ... --single-cell model=sonnet,target=josh
pixi run pull   -- <batch-tag>
pixi run aggregate runs/<batch-tag>
pixi run lab
```

Tasks pass everything after `--` straight through to the underlying
script. See [pixi.toml](pixi.toml) for the full task list.

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
operational source of truth so `pixi run pull` and the in-Pod scorer
both reach the same bucket.

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

#### Re-scoring a completed batch

The scoring container is target-agnostic and stateless against an
agent's `workspace/`, so a methodology revision (acceptance ranges,
new metric, etc.) — or recovering a cell that timed out mid-step —
can be applied to a frozen batch without re-running the agents.
`pixi run rescore` renders an agent-less k8s Job
([`orchestration/templates/rescore-job.yaml.j2`](orchestration/templates/rescore-job.yaml.j2))
that pulls the target cell from the bucket in-Pod, runs the scorer
against its `workspace/`, and writes `scorer.json` back to the
original key. The classifier
[`orchestration/classify_no_scorer.py`](orchestration/classify_no_scorer.py)
decides which cells qualify. Full methodology (including why this is
bias-free) is in
[EXPERIMENTAL_DESIGN.md §Scoring](EXPERIMENTAL_DESIGN.md#scoring).

#### LLM-judge (Q1/Q2/Q3)

Runs in-Pod inside the scorer container via
[`containers/run-judge.sh`](containers/run-judge.sh), driven by
[`config/opencode.judge.json`](config/opencode.judge.json) and
[`prompts/FUZZY_JUDGE.md`](prompts/FUZZY_JUDGE.md). Output lands as a
sibling `scorer.fuzzy.json` next to `scorer.json` and is mirrored to
the bucket alongside the rest of the cell artefacts. No host-side
opencode required.

#### Analysis flow

Once a batch is pulled, all analysis is host-side: `pixi run aggregate`
rolls every cell's `scorer.json` + agent metadata into
[`analysis/aggregated.csv`](analysis/aggregated.csv) (committed), and
the numbered notebooks consume it (`pixi run lab`):

```
pixi run aggregate runs/<batch> [runs/<batch> ...]   # → analysis/aggregated.csv
analysis/00_apply_scoring.ipynb     # convergence loop: rescore + re-rep to N
analysis/01_analysis.ipynb          # headline figures (Panels A/B, cost, runtime)
analysis/02_runtime_outliers.ipynb  # narrative diagnosis of the slow-mesa tail
analysis/03_manuscript_claims.ipynb # every paper number with its provenance
```

**The numbers are pipeline *role*, not a strict running order.**
`00_apply_scoring` is an **idempotent** orchestration loop, not a
one-shot first step — in the real headline run we aggregated, looked at
`01_analysis`, *then* used `00` to drive the dataset to completeness.
It reads the current `aggregated.csv`, decides what's still needed, and
**prints** the exact `pixi run rescore` / `pixi run apply` commands to
paste (it never fires `kubectl`/`pixi` itself). Each pass:

```
aggregate ──▶ 00_apply_scoring ──▶ (paste) rescore RUNTIME_KILL cells
   ▲                │                       + apply fresh re-rep cells
   │                ▼                                   │
   └──────── pull new tags ◀───────────────────────────┘
            (repeat until: every combo at N, no RUNTIME_KILL pending)
```

When `00` reports converged, `01_analysis`, `02_runtime_outliers`, and
`03_manuscript_claims` produce the final figures and paper numbers.
Because `aggregated.csv` is committed, those notebooks reproduce every
figure from a clean checkout without bucket access; re-running the loop
(or re-deriving the CSV) needs the bucket. Rescore mechanics are in §Re-scoring a completed batch above;
the bias-free rationale is in
[EXPERIMENTAL_DESIGN.md §Scoring](EXPERIMENTAL_DESIGN.md#scoring).

## Environment variables

The agent and scorer containers consume these. Host-side variables
(`MINIO_*`, `OPENROUTER_API_KEY`) are loaded from `.env` and injected
into the Pod via k8s Secrets (see "Repo-level one-time setup" above).

| Variable                 | Required | Purpose |
| ------------------------ | -------- | ------- |
| `OPENROUTER_API_KEY`     | yes      | API key for the OpenRouter gateway. A single key covers the entire nine-model panel (see [`config/models.yaml`](config/models.yaml)) plus the judge. Mounted into the agent + judge containers via the `openrouter-creds` Secret. |
| `MODEL`                  | yes      | Short name from [`config/models.yaml`](config/models.yaml). Resolved to a provider slug at render time by [`orchestration/resolve_model.py`](orchestration/resolve_model.py). |
| `TARGET`                 | yes      | `josh`, `mesa`, or `josh-mcp` (constrained Josh: no bash, Josh pipeline via MCP). |
| `RUN_ID`                 | yes      | Unique identifier for this cell. Generated by `render_jobs.py`. |
| `N_REPLICATES`           | no       | Number of replicates the scorer-side `run.sh` invokes. Default 2 in the agent seed; the scorer overrides to 100 at invocation time for the canonical workload. |
| `MINIO_ENDPOINT`         | yes      | S3-compatible endpoint. Default `https://storage.googleapis.com` — we hit GCS via its S3 interop API; `mc` is the client. |
| `MINIO_BUCKET`           | yes      | Destination bucket for per-cell artefacts. |
| `MINIO_ACCESS_KEY`       | yes      | HMAC access key (GCS HMAC pair). |
| `MINIO_SECRET_KEY`       | yes      | HMAC secret. |
| `MINIO_PREFIX`           | no       | Optional object-key prefix; batch tag is appended after it. |
| `WALL_CLOCK_BACKSTOP_SEC`| no       | Hard ceiling on agent wall time for the full 8-step multi-invocation chain. Default 1800. |
| `IDLE_THRESHOLD_SEC`     | no       | Kill the agent if no new trajectory event lands for this many seconds. Default 120. Catches silent LLM-stream stalls distinct from the wall-clock backstop. |
| `TOKEN_BACKSTOP`         | no       | Completion-token cap per opencode invocation (per step). Default 100000. |
| `FAIL_FAST_ON_STEP_ERROR`| no       | Multi-invocation failure mode. `false` (default, production): per-step failures are logged and the loop continues. `true` (dev/CI): the first non-zero opencode exit aborts the loop — surfaces broken plumbing fast. |

## Authentication

A single OpenRouter API key covers the entire model panel.
`OPENROUTER_API_KEY` is loaded from a gitignored `.env` into the
`openrouter-creds` k8s Secret (see "Repo-level one-time setup"), and
the Job manifest mounts it into the agent + judge containers as
`OPENROUTER_API_KEY`. opencode is configured to use OpenRouter as
its only provider via [`config/opencode.template.json`](config/opencode.template.json)
(agent) and [`config/opencode.judge.json`](config/opencode.judge.json)
(judge), both rendered with the resolved OpenRouter model slug per
run.

## Reproducibility

- Pinned opencode, Josh, and Python-stack versions in
  [`config/VERSIONS.md`](config/VERSIONS.md). Upgrade requires a fresh
  experimental batch.
- Pinned unified [`Dockerfile`](Dockerfile); the agent + scorer images
  are GHCR-tagged by short SHA per batch, and the `:<short-sha>` tag
  is what `render_jobs.py` bakes into each Job manifest.
- Josh CLI sha256 captured at image build time (no tagged releases
  upstream).
- Pinned model IDs in [`config/models.yaml`](config/models.yaml).
  Drift is logged when a provider returns a different `model_id` than
  requested.
- Pre-registered acceptance ranges committed to Git
  ([`harness/acceptance_ranges.json`](harness/acceptance_ranges.json)).
- Per-cell artefacts under `<bucket>/<prefix>/<batch-tag>/<run-id>/`
  are the canonical record. The rolled-up
  [`analysis/aggregated.csv`](analysis/aggregated.csv) is committed,
  so the headline notebook
  ([`analysis/01_analysis.ipynb`](analysis/01_analysis.ipynb)) and
  [`analysis/02_runtime_outliers.ipynb`](analysis/02_runtime_outliers.ipynb)
  regenerate every figure from a clean checkout without bucket access;
  re-deriving the CSV itself from the raw artefacts needs the bucket.
- Prompt files versioned in Git; any change forces a new batch tag.

## Open Source

This project is built on the work of others, with gratitude:

### R (analysis figures)

- **R** (base) under GPL-2.0-or-later for the statistical environment behind the headline figures ([`analysis/01_analysis.ipynb`](analysis/01_analysis.ipynb)).
- **ggplot2** under MIT for the faceted headline panels.
- **dplyr** under MIT for data wrangling in the panels.
- **tidyr** under MIT for reshaping in the panels.
- **readr** under MIT for reading `aggregated.csv`.
- **forcats** under MIT for factor ordering in the panels.
- **scales** under MIT for axis scales and label formatting.
- **patchwork** under MIT for composing the multi-panel figures.
- **ggrepel** under GPL-3.0-only for non-overlapping figure labels.
- **showtext** under Apache-2.0 for embedding the Lato font into the figures.
- **IRkernel** under MIT for the R Jupyter kernel that `pixi run lab` uses.
- **Lato** under the SIL Open Font License 1.1 for chart typography (bundled in [`analysis/fonts/`](analysis/fonts/)).

### Python (agent, scorer, analysis)

- **Mesa** under Apache-2.0 for the agent-based framework that is the general-purpose comparison target.
- **NumPy** under BSD-3-Clause for numerics in the scorer and the Mesa data path.
- **pandas** under BSD-3-Clause for tabular handling in the scorer, `aggregate.py`, and the notebooks.
- **SciPy** under BSD-3-Clause for the OLS `observed ~ predicted` fit in the ecology gate.
- **xarray** under Apache-2.0 for netCDF access in the Mesa data path.
- **netCDF4** under MIT for reading the synthetic climate inputs.
- **rasterio** under BSD-3-Clause for geospatial raster I/O.
- **tiktoken** under MIT for the token-level entropy code metric.
- **Jinja2** under BSD-3-Clause for rendering the per-cell k8s Job manifests and opencode configs.
- **PyYAML** under MIT for parsing `models.yaml` and the config sidecars.
- **compliance-checker** under Apache-2.0 for CF/ACDD validation of the synthetic dataset.
- **mcp** (Python SDK) under MIT for the client the `josh-mcp` runner forwards typed tool calls through.
- **matplotlib** under the Matplotlib (PSF-based) license for plots in the Python notebooks.
- **JupyterLab** under BSD-3-Clause for the notebook environment.
- **ipykernel** under BSD-3-Clause for the Python notebook kernel.

### Infrastructure (orchestration & runtime)

- **opencode** under MIT for the agent runtime that drives each model through the 8-step flow.
- **Eclipse Temurin / OpenJDK 21** under GPLv2 with the Classpath Exception for the JVM the Josh CLI runs on.
- **kubectl** under Apache-2.0 for applying the per-cell k8s Jobs.
- **MinIO Client (`mc`)** under GNU AGPLv3 for mirroring per-cell artefacts between the Pod and GCS.

This project's code is available under the [BSD 3-Clause License](LICENSE).

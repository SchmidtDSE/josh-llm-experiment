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
│   ├── BASE_PROMPT.md            # Full ForeverTree spec (becomes rung 5)
│   ├── SIDECAR.md                # Boilerplate footer appended to every rung
│   ├── rung1_minimal.md          # (planned)
│   ├── rung2_basic.md            # (planned)
│   ├── rung3_specified.md        # (planned)
│   ├── rung4_detailed.md         # (planned)
│   ├── rung5_master.md           # (planned)
│   └── recovery_template.md      # (planned)
├── harness/                      # acceptance_ranges.json today;
│                                 # scoring entry point + runners + validators planned, phase 2b
├── orchestration/                # (planned, phase 3+) — launch_run.sh, launch_batch.sh, dnsmasq.conf
├── .github/workflows/            # CI: smoke.yml (deterministic, every push) + integration-ollama.yml (workflow_dispatch)
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

# 3. Build the unified fortree image. Used both as the agent runtime
#    and as the --network=none scorer.
docker build -t fortree:latest .
```

[uv]: https://docs.astral.sh/uv/

### Image-only sanity checks (works today)

After the prerequisites step, these commands work on the current
branch and validate the image's installed environment:

```sh
docker run --rm fortree:latest python -c \
  "import mesa, numpy, pandas, scipy, xarray, netCDF4, rasterio, tiktoken; print('ok')"
docker run --rm fortree:latest josh --version       # prints pinned sha256
docker run --rm fortree:latest opencode --version   # prints 1.14.50
docker run --rm --network=none fortree:latest python -c "print('offline')"
docker run --rm --env-file .env fortree:latest printenv OPENROUTER_API_KEY
```

### Running the integration test locally

The agent path supports Ollama as an OpenRouter-free alternative. Useful
for replication without a paid API key, and for the `integration-ollama.yml`
CI workflow.

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
- `.github/workflows/integration-ollama.yml` is `workflow_dispatch`-only.
  It runs a full agent → scorer pipeline against Ollama and uploads
  `runs/<id>/` (including a rendered `report.md`) as a workflow artifact.

### One-off local run *(planned, phase 5)*

```sh
./orchestration/launch_run.sh \
  --model claude \
  --rung 3 \
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

### Full experimental cell *(planned, phase 5)*

```sh
./orchestration/launch_batch.sh \
  --model claude \
  --rung 3 \
  --target josh \
  --runs 3
```

`launch_batch.sh` will fan out `RUNS` parallel `launch_run.sh`
invocations locally via `xargs -P` (or GNU `parallel`) with fresh
`RUN_ID`s, each on its own bridge network. Runs are
network-I/O-bound on OpenRouter latency rather than CPU-bound on
the host, so single-machine parallelism is the default.

### Full sweep *(planned, phase 6+)*

```sh
for model in claude gemma kimi minimax mistral; do
  for rung in 1 2 3 4 5; do
    for target in josh mesa; do
      ./orchestration/launch_batch.sh \
        --model "$model" --rung "$rung" --target "$target" --runs 3
    done
  done
done
```

### Pilot sweep *(planned, phase 6)*

Before the headline run, a small pilot validates the loop and
factors. See
[OPEN_QUESTIONS.md item 9](OPEN_QUESTIONS.md#9) *(planned)*:

```sh
for model in claude mistral; do
  for rung in 1 5; do
    for target in josh mesa; do
      ./orchestration/launch_batch.sh \
        --model "$model" --rung "$rung" --target "$target" --runs 2
    done
  done
done
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
| `RUNG`                   | phase 3    | yes      | Prompt rung, 1–5. |
| `TARGET`                 | phase 3    | yes      | `josh` or `mesa`. |
| `RUN_ID`                 | phase 3    | yes      | Unique identifier for this generation. UUID preferred. |
| `MINIO_ENDPOINT`         | phase 4    | yes      | S3-compatible endpoint. Default `https://storage.googleapis.com` — we hit GCS via its S3 interop API; no MinIO server runs anywhere, `mc` is just the client. |
| `MINIO_BUCKET`           | phase 4    | yes      | Destination bucket name for per-run artifact archival. |
| `MINIO_ACCESS_KEY`       | phase 4    | yes      | HMAC access key for the bucket (GCS HMAC pair). |
| `MINIO_SECRET_KEY`       | phase 4    | yes      | HMAC secret. |
| `BATCH_TAG`              | phase 4    | yes      | Identifier prefixed to every object path; bumped per experimental batch. |
| `WALL_CLOCK_BACKSTOP_SEC`| phase 3    | no       | Hard ceiling on agent wall time per phase. Default 1800. |
| `IDLE_THRESHOLD_SEC`     | phase 3    | no       | Kill the agent if no new trajectory event lands for this many seconds. Default 120. Catches silent LLM-stream stalls distinct from the wall-clock backstop. |
| `TOKEN_BACKSTOP`         | phase 3    | no       | Completion-token cap per phase. Default 100000. |
| `SKIP_FUZZY_CONFORMANCE` | phase 5    | no       | Skip the optional LLM-judge target check. Default false; set true for cost-sensitive runs. |

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

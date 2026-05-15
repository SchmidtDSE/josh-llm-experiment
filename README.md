# ForeverTree LLM Experiments

A controlled experiment harness for measuring how prompt detail affects
LLM-generated code when targeting a constrained domain-specific
language (Josh) versus a general-purpose agent-based framework (Mesa).

This repository runs the AI-evaluation experiments reported in our
USRSE'26 submission on the [Josh][josh] vegetation modeling platform.
This README covers installation and execution; see
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) for the hypothesis,
prompt-detail ladder, scoring methodology, sandbox-policy rationale, and
threats to validity.

> **Status (phase 1 complete).** The harness is being built out in
> phases; [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) is the
> source of truth for what is done and what is still to do. As of
> phase 1, the unified `fortree` Docker image builds and validates on
> a local host; the scoring harness, orchestration scripts, prompts
> rungs 1–5, and OpenShell policy are not yet in the repo. References
> below marked *(planned)* describe the target shape.

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
├── Dockerfile                    # Unified fortree image (sandbox + scorer)
├── entrypoint-scorer.sh          # Dispatches to harness/run_metrics.py
├── .env.example                  # Copy to .env; OPENROUTER_API_KEY lives there
├── scripts/
│   └── install_josh.sh           # Installs Josh CLI inside the image
├── config/
│   ├── VERSIONS.md               # Pinned tool versions (host + image)
│   ├── requirements.txt          # Pinned Python deps
│   ├── models.yaml               # (planned) short-name → OpenRouter ID map
│   ├── opencode.template.json    # (planned) per-run opencode config template
│   ├── openshell-policy.yaml     # (planned) frozen sandbox policy
│   └── docs_categories.yaml      # (planned) URL → category tag, analysis-time
├── data/                         # Climate inputs (Tulare County, FGOALS-g3 / SSP2-4.5)
│   ├── precip_tulare_annual.nc
│   └── maxtemp_tulare_annual.nc
├── prompts/
│   ├── BASE_PROMPT.md            # Full ForeverTree spec (becomes rung 5 + spec/)
│   ├── rung1_minimal.md          # (planned)
│   ├── rung2_basic.md            # (planned)
│   ├── rung3_specified.md        # (planned)
│   ├── rung4_detailed.md         # (planned)
│   ├── rung5_master.md           # (planned)
│   └── recovery_template.md      # (planned)
├── spec/                         # (planned, phase 2) — ForeverTree spec + acceptance ranges
├── docs/                         # (planned, phase 4) — in-workspace navigation aids
├── harness/                      # (planned, phase 2) — scoring entry point + runners + validators
├── orchestration/                # (planned, phase 3+) — launch_run.sh, launch_batch.sh, etc.
└── results/                      # (planned, phase 5) — per-run JSON manifests
```

## Running it

### Prerequisites

The host needs Docker, [uv][uv], and a pinned OpenShell. The harness uses
OpenShell's [docker compute driver][openshell-docker] so the agent runs
inside our own image — the same image the scorer uses, no env drift
between them. Install each manually:

```sh
# 1. Docker daemon (system-specific).
#    https://docs.docker.com/engine/install/

# 2. uv — Astral's single-binary Python tool installer.
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. OpenShell (pinned). Treat upgrades as breaking; rerun the
#    experiment from scratch.
uv tool install openshell==0.0.36

# 4. Build the unified fortree image. Used both as the agent sandbox
#    base (via openshell sandbox create --from) and as the scorer.
docker build -t fortree:latest .
```

[uv]: https://docs.astral.sh/uv/
[openshell-docker]: https://docs.nvidia.com/openshell/latest/reference/sandbox-compute-drivers#docker-driver

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

### One-off local run *(planned, phase 5)*

```sh
./orchestration/launch_run.sh \
  --model claude \
  --rung 3 \
  --target josh \
  --run-id "$(uuidgen)"
```

`launch_run.sh` will wrap the five-step flow: it calls
`openshell sandbox create --from fortree:latest --policy
./config/openshell-policy.yaml` to spin up a sandbox on the pinned
image (via OpenShell's docker compute driver), invokes opencode
inside it for step 1, runs the conformance and validation harnesses
against the sandbox workspace, invokes opencode again for the
recovery step if needed, and runs the final validation. It captures
the OpenShell access log, all opencode trajectories, and the harness
output, and appends a row to `results/manifest.jsonl`.

### Full experimental cell *(planned, phase 5)*

```sh
./orchestration/launch_batch.sh \
  --model claude \
  --rung 3 \
  --target josh \
  --runs 3
```

`launch_batch.sh` will spawn `RUNS` parallel orchestrated runs (each
covering all five steps) with fresh `RUN_ID`s and wait for all to
complete.

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
| `OPENROUTER_API_KEY`     | phase 1    | yes      | API key for the OpenRouter gateway. Single key, all models. |
| `MODEL`                  | phase 3    | yes      | Short name from `config/models.yaml`. |
| `RUNG`                   | phase 3    | yes      | Prompt rung, 1–5. |
| `TARGET`                 | phase 3    | yes      | `josh` or `mesa`. |
| `RUN_ID`                 | phase 3    | yes      | Unique identifier for this generation. UUID preferred. |
| `RESULTS_BUCKET`         | phase 5    | yes      | S3 (or compatible) URI for artifact upload. |
| `AWS_ACCESS_KEY_ID`      | phase 5    | yes      | Object-store auth. |
| `AWS_SECRET_ACCESS_KEY`  | phase 5    | yes      | Object-store auth. |
| `WALL_CLOCK_BACKSTOP_SEC`| phase 3    | no       | Hard ceiling on agent wall time per phase. Default 1800. |
| `TOKEN_BACKSTOP`         | phase 3    | no       | Completion-token cap per phase. Default 100000. |
| `SKIP_FUZZY_CONFORMANCE` | phase 5    | no       | Skip the optional LLM-judge target check. Default false; set true for cost-sensitive runs. |

## Authentication

A single OpenRouter API key covers the entire model panel.
`OPENROUTER_API_KEY` is passed to the sandbox at creation time by
the orchestrator *(planned, phase 3)* and reaches opencode via its
rendered `opencode.json`. Today the variable is loaded from a
gitignored `.env` and passed to the image with `docker run --env-file`;
phase 3 wires it through `openshell sandbox create`.

opencode will be configured to use OpenRouter as its only provider;
the rendered config will pin the resolved OpenRouter model slug per
run. The OpenShell network policy *(planned, phase 4)* will allowlist
`openrouter.ai` alongside the documentation hosts so the inference
call can be made out of the sandbox. No other provider hosts are
reachable.

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
        }
      }
    }
  }
}
```

The opencode tool-restriction schema is the soft layer; the
OpenShell policy is the hard layer. If a model is ever added that
isn't on OpenRouter, the orchestrator will need to render a
different provider block *and* update the OpenShell network policy
to allowlist that provider's host. Doing both is a deliberate,
visible change in two version-controlled files.

## Reproducibility

- Pinned OpenShell, opencode, Josh, and Python-stack versions in
  [`config/VERSIONS.md`](config/VERSIONS.md). Upgrade requires a fresh
  experimental batch.
- Pinned unified [`Dockerfile`](Dockerfile) (used both as agent sandbox
  base via OpenShell's docker driver and as the `--network=none`
  scorer), tagged per batch.
- The Dockerfile inherits from a SHA-pinned tag of NVIDIA's community
  sandbox base (`ghcr.io/nvidia/openshell-community/sandboxes/base`)
  so the OpenShell supervisor's readiness contract stays in lockstep
  with whatever OpenShell version we pin.
- Josh CLI sha256 captured at image build time (no tagged releases
  upstream).
- Pinned model IDs in `config/models.yaml` *(planned, phase 3)*. Drift
  will be logged when a provider returns a different `model_id` than
  requested.
- Frozen `config/openshell-policy.yaml` for the headline experiment
  *(planned, phase 4)*.
- Pre-registered acceptance ranges committed to Git before any runs
  *(planned, phase 2)*.
- Per-run manifests in `results/manifest.jsonl` will be append-only
  and committed *(planned, phase 5)*. Each entry will record the
  prompt rung, model, target, resolved model ID, OpenRouter cost,
  all metrics, OpenShell access log URI, and S3 URIs for the
  artifacts.
- Prompt files versioned in Git; any change forces a new batch tag.

## License

BSD 3-Clause. See [`LICENSE`](LICENSE).

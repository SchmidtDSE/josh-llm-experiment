# ForeverTree LLM Experiments

A controlled experiment harness for measuring how prompt detail affects
LLM-generated code when targeting a constrained domain-specific
language (Josh) versus a general-purpose agent-based framework (Mesa).

This repository runs the AI-evaluation experiments reported in our
USRSE'26 submission on the [Josh][josh] vegetation modeling platform.
This README covers installation and execution; see
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) for the hypothesis,
prompt-detail ladder, scoring methodology, sandbox-policy rationale, and
threats to validity. For unresolved methodological questions, see
[`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md).

[josh]: https://joshsim.org/

## Repository layout

```
.
├── README.md                     # This file (install + run)
├── EXPERIMENTAL_DESIGN.md        # Methodology, scoring, threats to validity
├── OPEN_QUESTIONS.md             # Unresolved design questions
├── Dockerfile.sandbox            # Base image for the OpenShell sandbox
├── Dockerfile.scorer             # Scoring container (plain Docker, no network)
├── entrypoint-scorer.sh          # Scorer-container entrypoint
├── config/
│   ├── models.yaml               # Short-name → OpenRouter ID map
│   ├── opencode.template.json    # Rendered per run with model + tool palette
│   ├── openshell-policy.yaml     # Frozen sandbox policy (network + fs + process)
│   ├── docs_categories.yaml      # URL → category tag map (analysis-time only)
│   └── requirements.txt          # Pinned Python deps
├── spec/
│   ├── ForeverTree.md            # Full task specification (reference)
│   ├── acceptance_ranges.json    # Pre-registered output ranges (frozen)
│   ├── harness_contract.md       # What the generated code must produce
│   └── environment_sidecar.md    # Env description shown to the agent
├── prompts/
│   ├── rung1_minimal.md
│   ├── rung2_basic.md
│   ├── rung3_specified.md
│   ├── rung4_detailed.md
│   ├── rung5_master.md
│   └── recovery_template.md      # Used in step 4; renders with validation results
├── docs/                         # In-workspace navigation aids only
│   └── INDEX.md                  # Pre-built entry-point list to whitelisted hosts
├── data/                         # Climate inputs
│   ├── precip_tulare_annual.nc
│   └── maxtemp_tulare_annual.nc
├── harness/
│   ├── run_metrics.py            # Top-level scoring entry point (steps 3, 5)
│   ├── conformance.py            # Step 2 mechanical target-conformance check
│   ├── conformance_fuzzy.py      # Step 2 optional LLM-judge wrapper
│   ├── loc.py                    # Relevant-code-length computation
│   ├── entropy.py                # Tokenizer + entropy calc
│   ├── docs_log.py               # Parse OpenShell access log → docs_* metrics
│   ├── runners/
│   │   ├── josh_runner.py        # Invoke generated .josh model
│   │   └── mesa_runner.py        # Invoke generated Mesa model
│   └── validators/
│       ├── output_schema.py      # CSV shape check
│       └── acceptance.py         # Range checks against spec
├── orchestration/
│   ├── launch_run.sh             # Full 5-step orchestrated run
│   ├── launch_batch.sh           # Fan out N runs across a cell
│   ├── render_recovery_prompt.py # Builds step-4 prompt from step-3 results
│   └── collect_results.py        # Pull manifest entries into a DataFrame
└── results/                      # Per-run JSON manifests (committed)
    └── manifest.jsonl
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

### One-off local run

```sh
./orchestration/launch_run.sh \
  --model claude \
  --rung 3 \
  --target josh \
  --run-id "$(uuidgen)"
```

`launch_run.sh` wraps the five-step flow: it calls
`openshell sandbox create --policy ./config/openshell-policy.yaml`
to spin up a sandbox on the pinned image, invokes opencode inside
it for step 1, runs the conformance and validation harnesses
against the sandbox workspace, invokes opencode again for the
recovery step if needed, and runs the final validation. It
captures the OpenShell access log, all opencode trajectories, and
the harness output, and appends a row to
`results/manifest.jsonl`.

### Full experimental cell

```sh
./orchestration/launch_batch.sh \
  --model claude \
  --rung 3 \
  --target josh \
  --runs 3
```

`launch_batch.sh` spawns `RUNS` parallel orchestrated runs (each
covering all five steps) with fresh `RUN_ID`s and waits for all to
complete.

### Full sweep

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

### Pilot sweep

Before the headline run, a small pilot validates the loop and
factors. See [OPEN_QUESTIONS.md item 9](OPEN_QUESTIONS.md#9):

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

| Variable                 | Required | Purpose |
| ------------------------ | -------- | ------- |
| `OPENROUTER_API_KEY`     | yes      | API key for the OpenRouter gateway. Single key, all models. |
| `MODEL`                  | yes      | Short name from `config/models.yaml`. |
| `RUNG`                   | yes      | Prompt rung, 1–5. |
| `TARGET`                 | yes      | `josh` or `mesa`. |
| `RUN_ID`                 | yes      | Unique identifier for this generation. UUID preferred. |
| `RESULTS_BUCKET`         | yes      | S3 (or compatible) URI for artifact upload. |
| `AWS_ACCESS_KEY_ID`      | yes      | Object-store auth. |
| `AWS_SECRET_ACCESS_KEY`  | yes      | Object-store auth. |
| `WALL_CLOCK_BACKSTOP_SEC`| no       | Hard ceiling on agent wall time per phase. Default 1800. |
| `TOKEN_BACKSTOP`         | no       | Completion-token cap per phase. Default 100000. |
| `SKIP_FUZZY_CONFORMANCE` | no       | Skip the optional LLM-judge target check. Default false; set true for cost-sensitive runs. |

## Authentication

A single OpenRouter API key covers the entire model panel.
`OPENROUTER_API_KEY` is passed to the sandbox at creation time by
the orchestrator and reaches opencode via its rendered
`opencode.json`.

opencode is configured to use OpenRouter as its only provider; the
rendered config pins the resolved OpenRouter model slug per run.
The OpenShell network policy allowlists `openrouter.ai` (alongside
the documentation hosts) so the inference call can be made out of
the sandbox. No other provider hosts are reachable.

Sample rendered `opencode.json`:

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

- Pinned model IDs in `config/models.yaml`. Drift is logged when a
  provider returns a different `model_id` than requested.
- Pinned OpenShell version. Upgrade requires a fresh experimental
  batch.
- Pinned sandbox image (`Dockerfile.sandbox`) and scorer image
  (`Dockerfile.scorer`), tagged per batch.
- Frozen `config/openshell-policy.yaml` for the headline experiment.
- Pre-registered acceptance ranges committed to Git before any
  runs.
- Per-run manifests in `results/manifest.jsonl` are append-only and
  committed. Each entry records the prompt rung, model, target,
  resolved model ID, OpenRouter cost, all metrics, OpenShell access
  log URI, and S3 URIs for the artifacts.
- Prompt files versioned in Git; any change forces a new batch tag.

## License

BSD 3-Clause. See [`LICENSE`](LICENSE).

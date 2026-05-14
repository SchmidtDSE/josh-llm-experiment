# ForeverTree LLM Experiments

A controlled experiment harness for measuring how prompt detail affects
LLM-generated code when targeting a constrained domain-specific
language (Josh) versus a general-purpose agent-based framework (Mesa).

This repository runs the AI-evaluation experiments reported in our
USRSE'26 submission on the [Josh][josh] vegetation modeling platform.

[josh]: https://joshsim.org/

## Hypothesis

A constrained DSL target reduces variance in LLM-generated
implementations relative to a general-purpose target, and recovers
faster from initial failures when given feedback. Two specific
predictions:

1. **One-shot quality.** A higher fraction of first-attempt Josh
   implementations will run and pass validation than first-attempt
   Mesa implementations, especially at lower prompt-detail rungs.
2. **Recovery quality.** When a first attempt fails, Josh
   implementations will more often reach a passing state after one
   round of feedback than Mesa implementations.

Each prediction is measured separately. The headline figure reports
both.

## Task

Every run, regardless of model or prompt rung, implements the same
fixed task: the **ForeverTree** model from the Josh tutorial series.
A grid of patches; ten trees per patch; growth driven by external
temperature and precipitation data with a quadratic temperature
response, a logistic precipitation response, and a small Gaussian
multiplicative noise term. Full specification in
[`spec/ForeverTree.md`](spec/ForeverTree.md).

ForeverTree was chosen because it is small enough to be tractable for
small models, includes external geospatial data (which exercises the
declarative-data-binding feature of Josh and forces Mesa
implementations to write alignment code), and has well-defined
expected outputs.

## Stack

Three off-the-shelf pieces, chosen to minimize what the harness
itself has to own:

- **[NVIDIA OpenShell][openshell]** provides the agent sandbox.
  Policy-driven network filtering, Landlock-enforced filesystem
  isolation, an unprivileged process namespace, and a logging proxy
  with host- and path-level matching. opencode is a first-class
  supported agent. Alpha software, pinned by version.
- **[opencode][opencode]** runs the agent inside the sandbox.
  `opencode run` is invoked non-interactively per phase.
- **[OpenRouter][or]** is the inference gateway. A single API key
  covers the model panel; the OpenRouter slug pins the model.

[openshell]: https://docs.nvidia.com/openshell/
[opencode]: https://opencode.ai/
[or]: https://openrouter.ai/

## Design

### Prompt-detail ladder

Five rungs of increasing specificity. The prompts describe the model
in *domain terms only* — they contain no Josh-specific or
Mesa-specific implementation guidance. The LLM is told *which* tool
to use (Josh or Mesa) so we can measure tool-conformance separately,
but is given no guidance on *how* to use it. All five rungs live in
[`prompts/`](prompts/).

| Rung | Name        | Roughly |
| ---- | ----------- | ------- |
| 1    | Minimal     | "Simulate a forest of trees growing over time." |
| 2    | Basic       | Adds counts, growth rate, mortality, duration. |
| 3    | Specified   | Adds grid dimensions, initial conditions, output format. |
| 4    | Detailed    | Adds stochastic distributions, age tracking, export structure. |
| 5    | Master      | Adds edge cases, units, the full ForeverTree spec. |

Every prompt shares a fixed boilerplate footer describing the runtime
harness: filename conventions, expected output CSV structure, how the
runner will invoke the generated code. This is what anchors the
"did it run" measurement.

### Models

Configured via `MODEL` environment variable. All models are accessed
through OpenRouter using a single API key.

| Short name | OpenRouter ID                         |
| ---------- | ------------------------------------- |
| claude     | `anthropic/claude-sonnet-4.5`         |
| gemma      | `google/gemma-3-27b-it`               |
| kimi       | `moonshotai/kimi-k2`                  |
| minimax    | `minimax/minimax-m2`                  |
| mistral    | `mistralai/mistral-large-latest`      |

The mapping lives in [`config/models.yaml`](config/models.yaml).
Model IDs should be pinned to specific versions for reproducibility
and noted in the run manifest. The orchestrator logs the resolved
`model_id` returned by OpenRouter alongside the requested one and
flags drift.

### Targets

Two implementation targets per run, set via `TARGET` environment
variable:

- `josh` — generate a `.josh` model file plus any required `.jshd`
  preprocessing config.
- `mesa` — generate a Python module implementing the model using the
  Mesa 3.x framework.

The harness contract (input data location, expected output CSV path
and columns) is identical for both.

### Sample size

Default `RUNS=3` per (model × rung × target) cell. The full headline
experiment is 5 models × 5 rungs × 2 targets × 3 runs = **150
generations** (× 2 phases — see [Run flow](#run-flow) — so 300 agent
invocations total). N can scale up freely within OpenRouter cost
budget; the practical ceiling is set by the cost of any downstream
manual review rather than the runs themselves. See
[OPEN_QUESTIONS.md item 6](OPEN_QUESTIONS.md#6) for sample-size
considerations.

Each run is one container invocation for the one-shot phase and one
follow-up container invocation for the recovery phase (see flow
below).

## Run flow

A full **run** (one (model × rung × target × run_id) cell) consists
of five orchestrated steps spanning two opencode invocations inside
a single OpenShell sandbox, plus two validation passes by a separate
scoring container.

The sandbox is created with `openshell sandbox create --policy
./config/openshell-policy.yaml` at step 1 and kept alive across
steps 1–5 so that step 4's recovery prompt sees step 1's
workspace. It is torn down after step 5.

The scoring container (`fortree-scorer`) is plain Docker — it does
not use OpenShell. It runs with `--network=none` and a read-only
mount of the sandbox workspace.

### Step 1: Initial prompt

The orchestrator validates env vars (`OPENROUTER_API_KEY`, `MODEL`,
`RUNG`, `TARGET`, `RUN_ID`), creates an OpenShell sandbox with the
pinned policy, and invokes opencode inside it.

`opencode run` is invoked non-interactively with the prompt for the
selected rung. The prompt **names the target framework** ("implement
this using Josh" or "implement this using Mesa") so that
tool-conformance can be measured as a separate signal in step 2.

The agent reads, writes, edits, greps, and may invoke `./run.sh` to
self-validate. Documentation is available locally at `./docs/`.
Installed Python and Java package source is readable on disk. The
agent has no network access.

**The agent's stopping condition is its own.** When opencode signals
completion, the orchestrator captures the trajectory log and shuts
down the agent container. There is no iteration counter — the agent
may invoke `./run.sh` as many times as it likes during this phase.
What we measure is the *final state* the agent submitted, not the
number of internal attempts. (See "What the agent sees vs. what we
measure" below.)

A wall-clock backstop (default 30 min) and a total-token budget
backstop (default 100k completion tokens) terminate the container
if the agent loops indefinitely. Both are safety nets, not primary
metrics.

### Step 2: Tool-conformance check

Before invoking the agent's code, the orchestrator runs a
**tool-conformance check** on the generated workspace. This catches
the failure mode in which an agent told to use Mesa silently
implements the task in plain Python, or where a Josh prompt
produces a Mesa-like Python module instead of `.josh` and `.jshd`
files.

Two layers:

- **Mechanical check.** Grep-based. For Mesa targets, look for
  `import mesa`, `from mesa`, and instantiation of Mesa base
  classes. For Josh targets, look for files matching `*.josh` and
  `*.jshc`, valid Josh syntax tokens (via `josh parse`), and
  `.jshd` files where expected.
- **Fuzzy check (optional, post-hoc).** A capable model (Claude or
  similar) is shown the generated workspace and asked: "Does this
  implementation use $TARGET as its primary modeling framework?
  Answer yes / no / partial with one sentence." Recorded as a
  separate metric, not used to gate downstream steps.

The mechanical check's result is recorded as `target_conformance`
(bool) in the run manifest. **Non-conformance is data, not a
failure to discard.** Runs proceed to step 3 regardless. The
fuzzy-check field is `target_conformance_fuzzy` and may be
populated post-hoc.

### Step 3: Validation (one-shot scoring)

The orchestrator starts `fortree-scorer` against the workspace
volume, mounted read-only except for a writable `./results/`
directory. The scoring harness
([`harness/run_metrics.py`](harness/run_metrics.py)) runs:

- Invoke `./run.sh` with the standard input data. Capture exit
  code, stdout, stderr, wall time.
- Validate that `./output/results.csv` exists and has the expected
  schema.
- Compute Tier 1 (did_run) and Tier 3 (reference-match) metrics.
- Compute static metrics on the generated code: `relevant_loc`,
  `entropy_bits`.

All metric outcomes are written to a JSON record. The scorer
container exits.

### Step 4: Feedback prompt and recovery attempt

The orchestrator invokes opencode a second time inside the **same**
OpenShell sandbox used in step 1, this time with a **recovery
prompt**. The recovery prompt:

- References the same workspace (the agent sees its prior
  implementation, exactly as it left it).
- Includes the validation results from step 3 — what ran, what
  failed, what was missing.
- Does **not** include the acceptance ranges or any new
  information about correctness criteria. Only the binary /
  structural outcomes from validation are surfaced.
- Uses the same prompt-style guidance as the original rung,
  preserving the rung's detail level.

The agent has the same sandbox policy and constraints as step 1.
OpenShell's `network_policies` are hot-reloadable, but we do not
modify them between phases — the policy is locked at step 1 and
left alone.

If step 3 produced a passing result, step 4 is skipped entirely.
The recovery phase is only triggered when there is something to
recover from.

### Step 5: Validation (post-recovery scoring)

Same as step 3, run against the post-recovery workspace. Same
metrics, same harness, same constraints.

The full run record contains both step-3 results (one-shot) and
step-5 results (post-recovery). The headline figure compares Josh
vs. Mesa on both axes.

### What the agent sees vs. what we measure

The agent's view of "did it work" is its own — whether `./run.sh`
exits cleanly during the agent phase. The orchestrator's
view is whether validation passes in steps 3 and 5. These are not
necessarily the same.

This separation is deliberate. We want to measure first-attempt
quality against external validation, not against the agent's own
confidence. An agent that calls `./run.sh`, sees exit 0, and
declares done has produced a one-shot result — even if the CSV
turns out to be malformed when validated in step 3.

## Scoring tiers

Two tiers of metrics, applied at both step-3 and step-5 validation.

**Tier 1: Ran-to-completion.** Did `./run.sh` exit 0 and produce
`./output/results.csv` with the expected schema? Universal across
all rungs and targets.

**Tier 3: Reference match.** Did mean tree height and mean
occupancy at year 10 fall in the pre-registered acceptance ranges?
Meaningful only at rungs where the relevant parameters were
specified in the prompt. Reported unconditionally; readers can
slice by rung.

(Tier 2 was previously reserved for parameter recovery; that
analysis has been deferred to manual post-hoc review since all
generated artifacts are archived.)

## Metrics

All scoring is mechanical at experiment time. Manual review of the
archived artifacts can supplement post-hoc.

| Metric                       | Phase        | Type    | Source |
| ---------------------------- | ------------ | ------- | ------ |
| `target_conformance`         | step 2       | bool    | Mechanical check that the agent used the named target framework. |
| `target_conformance_fuzzy`   | step 2 (opt) | enum    | yes/no/partial from a post-hoc LLM judge. |
| `oneshot_did_run`            | step 3       | bool    | Step 3 `./run.sh` exited 0 and produced valid output. |
| `oneshot_height_in_range`    | step 3       | bool    | Mean tree height at year 10 within pre-registered range. |
| `oneshot_occupancy_in_range` | step 3       | bool    | Mean tree count per cell at year 10 within pre-registered range. |
| `recovery_attempted`         | step 4       | bool    | Whether step 4 ran (true iff step 3 did not pass). |
| `recovery_did_run`           | step 5       | bool    | Step 5 `./run.sh` exited 0 and produced valid output. |
| `recovery_height_in_range`   | step 5       | bool    | Same as oneshot, after recovery. |
| `recovery_occupancy_in_range`| step 5       | bool    | Same as oneshot, after recovery. |
| `agent_self_invocations`     | step 1       | int     | Count of `./run.sh` invocations during the agent phase (informational, not used for scoring). |
| `prompt_tokens`              | step 1       | int     | OpenRouter response. |
| `completion_tokens`          | step 1       | int     | OpenRouter response. |
| `recovery_prompt_tokens`     | step 4       | int     | OpenRouter response for recovery phase. |
| `recovery_completion_tokens` | step 4       | int     | OpenRouter response for recovery phase. |
| `relevant_loc_oneshot`       | step 3       | int     | Lines of generated code, all-inclusive. |
| `relevant_loc_recovery`      | step 5       | int     | Same, post-recovery. |
| `entropy_bits_oneshot`       | step 3       | float   | Token-level Shannon entropy of the generated code, computed with a fixed generic BPE tokenizer. |
| `entropy_bits_recovery`      | step 5       | float   | Same, post-recovery. |
| `wall_time_seconds`          | step 1 / 4   | float   | End-to-end agent time per phase. Reported, not used for scoring (provider latency confounds). |
| `docs_bytes_total`           | step 1 / 4   | int     | Total bytes served by the egress proxy during the agent phase. |
| `docs_distinct_paths`        | step 1 / 4   | int     | Count of distinct doc URLs the agent fetched. |
| `docs_paths_josh`            | step 1 / 4   | int     | Distinct paths within `joshsim.org`. |
| `docs_paths_mesa`            | step 1 / 4   | int     | Distinct paths within `mesa.readthedocs.io`. |
| `docs_paths_python_stdlib`   | step 1 / 4   | int     | Distinct paths within `docs.python.org`. |
| `docs_paths_scientific`      | step 1 / 4   | int     | Distinct paths within numpy/scipy/pandas/xarray/netCDF4 docs (combined). |
| `docs_paths_by_category`     | step 1 / 4   | dict    | Counts per category tag (`reference`, `example`, `tutorial`, `api`, `general`) per host. |

The pre-registered acceptance ranges live in
[`spec/acceptance_ranges.json`](spec/acceptance_ranges.json) and were
committed before any experimental runs. **Do not modify this file
after experiments begin.** Git history is the audit trail.

## Sandbox policy

All agent-phase isolation lives in a single OpenShell policy file at
[`config/openshell-policy.yaml`](config/openshell-policy.yaml). The
policy pins three concerns:

- **Filesystem isolation** via Landlock. Read-only mounts of `/usr`,
  `/lib`, `/etc`; read-write access only to `/workspaces/josh-llm-experiment` (the agent
  workspace) and `/tmp`.
- **Process isolation.** The agent runs as an unprivileged
  `sandbox` user/group. Root is rejected by OpenShell on principle.
  Seccomp filters block dangerous syscalls automatically.
- **Network policy.** All outbound traffic from the sandbox is
  forced through OpenShell's gateway proxy, which auto-detects TLS
  and applies host- and path-level matching from the policy.

The static (filesystem, process) and dynamic (network) sections are
both committed to Git and frozen for the headline experiment.

### Pre-installed environment

Pinned at sandbox image build time. No `pip install`, no
`apt-get install` during the agent phase:

- Python 3.11 with pinned versions of: mesa, numpy, pandas, scipy,
  xarray, netCDF4, rasterio.
- OpenJDK 17.
- The Josh CLI at a pinned version.

Exact versions in [`config/requirements.txt`](config/requirements.txt)
and the sandbox image definition.

### Network policy: doc access

The `network_policies` block in
[`config/openshell-policy.yaml`](config/openshell-policy.yaml)
encodes the allowlist. One entry per documentation host, all using
`protocol: rest` so OpenShell's gateway terminates TLS and produces
per-request log entries:

| Host                                | Purpose |
| ----------------------------------- | ------- |
| `joshsim.org`                       | Josh tutorials and language reference. |
| `mesa.readthedocs.io`               | Mesa documentation, including the examples gallery. |
| `docs.python.org`                   | Python standard library reference. |
| `numpy.org`                         | numpy documentation. |
| `docs.scipy.org`                    | scipy documentation. |
| `pandas.pydata.org`                 | pandas documentation. |
| `docs.xarray.dev`                   | xarray documentation. |
| `unidata.github.io`                 | netCDF4 documentation. |

Specifically not on the list: github.com (including issue threads,
gists, and source links from doc pages), Stack Overflow, Reddit,
blog hosts, PyPI's metadata pages. The agent can read installed
package source on disk, which is a strict superset of what
github.com source-browse would provide.

The gateway logs every request — host, full path, response size,
timestamp, calling binary — and emits OCSF JSON. The orchestrator
captures these logs per run.

### Network policy: model API

A separate `network_policies` entry whitelists `openrouter.ai` so
that opencode can reach the inference gateway. This entry is
distinguished from the docs allowlist in the policy file and
excluded from `docs_*` metrics; OpenRouter traffic is recorded
under its own metric (`api_request_count`, `api_bytes`).

### Category tags for documentation hosts

OpenShell's policy schema does not carry a category-tag field per
host or path. The mapping from URL to category — `reference`,
`example`, `tutorial`, `api`, `general` — lives in
[`config/docs_categories.yaml`](config/docs_categories.yaml), a
sidecar file consumed only at analysis time. The orchestrator
joins it to the OpenShell access log post-hoc to produce
`docs_paths_by_category` in the run manifest.

### Asymmetry, acknowledged

Mesa has a mature documentation corpus with an extensive examples
gallery; Josh has tutorial pages and `llms-full.txt`. This is not
an apples-to-apples comparison. The asymmetry is a real reflection
of where each tool is in its lifecycle, and the run-level traffic
logs make the imbalance visible in the data rather than hiding it.
The discussion section of the paper will acknowldege this directly.

### Scoring phase network

The scoring phase runs in a separate plain-Docker container with
`--network=none`. OpenShell is not used for scoring — there is no
agent to sandbox.

### Reading installed package source

The agent can `cat $(python -c "import mesa; print(mesa.__file__)")`
and follow imports. The OpenShell policy permits read access to
`/usr/lib/python3.11/site-packages` as part of the baseline
read-only mount. This mirrors realistic RSE behavior and is
symmetric across targets. The sidecar does not call this out
explicitly; the agent discovers it if it tries.

## Tool palette

opencode's tool palette is configured in
[`config/opencode.template.json`](config/opencode.template.json),
which the orchestrator renders per run. The configured tools are:

| Tool        | Scope |
| ----------- | ----- |
| `read`      | Workspace, installed package source, and `./docs/INDEX.md`. |
| `write`     | Workspace only. |
| `edit`      | Workspace only. |
| `glob`      | Workspace and `./docs/`. |
| `grep`      | Workspace, `./docs/`, and installed package source. |
| `bash`      | Read-only inspection commands and `./run.sh`. The agent may invoke `./run.sh` freely to self-test. |

The OpenShell policy file backs these with kernel-level enforcement.
opencode's tool restrictions are the soft layer; OpenShell's
Landlock + network policy + seccomp + unprivileged user is the
hard layer. If opencode were ever to leak — e.g., by invoking
`python` directly despite the bash whitelist — the OpenShell layer
would still block disk writes outside the workspace, network calls
outside the policy, and any privileged syscall.

Explicitly blocked at the OpenShell layer:

- Package installation: any binary attempting to reach `pypi.org`
  or `files.pythonhosted.org` is denied by the network policy
  (neither host is in the allowlist).
- Direct internet access outside the documentation and OpenRouter
  allowlists.

## Stopping conditions

No iteration budget. The agent decides when to stop. Two backstops
protect against runaway loops:

- **Wall-clock backstop.** Default 30 min per agent phase. Safety
  net, not a primary metric.
- **Token budget backstop.** Default 100k completion tokens per
  agent phase. Same role.

A run that hits either backstop is recorded with a `backstop_hit`
flag and proceeds to validation against whatever state the agent
left in the workspace.

## Repository layout

```
.
├── README.md                     # This file
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

```sh
# Pinned OpenShell version. Treat upgrades as breaking; rerun the
# experiment from scratch.
uv tool install openshell==<pinned-version>

# Build the sandbox base image and the scoring image.
docker build -f Dockerfile.sandbox -t fortree-sandbox .
docker build -f Dockerfile.scorer  -t fortree-scorer  .
```

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

## Threats to validity

This experiment is small and targeted. Known limits, repeated in the
paper:

- Single task (ForeverTree). Results may not generalize to harder
  modeling targets.
- The "Mesa must reinvent boilerplate" effect is partly an artifact
  of Mesa being a general framework; a hypothetical
  vegetation-specific Mesa extension would close some of the gap.
- The five-model panel is a snapshot; the model field moves fast.
- Pre-registered acceptance ranges were author-set, not empirically
  derived from a reference simulator. Ranges are chosen to be
  permissive — they catch obvious failures, not subtle ones.
- "Relevant LOC" definition is judgement-encoded once, in
  [`harness/loc.py`](harness/loc.py); a different operationalization
  could shift numbers.
- OpenShell is alpha software; behavior may differ between versions.
  The pinned version is recorded in the manifest and in this README.
  If a breaking change forces an upgrade mid-experiment, that batch
  is invalidated. A fallback to a frozen local docs mirror without
  OpenShell is documented in
  [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md) as a contingency.
- Several methodological choices are still open and will affect
  what the experiment can claim. See
  [`OPEN_QUESTIONS.md`](OPEN_QUESTIONS.md).

## License

BSD 3-Clause. See [`LICENSE`](LICENSE).

## Citation

If you use this harness or its outputs, please cite the USRSE'26
paper (TODO once accepted) and the Josh platform.
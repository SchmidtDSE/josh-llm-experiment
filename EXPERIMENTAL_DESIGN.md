# Experimental design — ForeverTree LLM Experiments

The methodology behind the experiment described in [README.md](README.md).
For installation and how to run, see the README. The scoring methodology —
axes, metric definitions, LLM-judge spec, re-analysis recipe, and open
scoring questions — lives in [§Scoring](#scoring) below (previously a
separate `SCORING.md`).

This is the AI-evaluation experiment reported in our USRSE'26 submission on the [Josh][josh] vegetation modeling platform. Two design simplifications are worth flagging up front: (1) the rung-detail prompt ladder is collapsed to a single master prompt — the task at full detail is already hard enough to be a useful Josh-vs-Mesa differentiator, and a second variation axis would dilute the statistical power available within budget; (2) the separate recovery-loop hypothesis is folded into the multi-invocation flow, whose todos already include validate-and-cleanup iterations.

[josh]: https://joshsim.org/

## Hypothesis

A constrained DSL target produces better LLM-generated implementations
than a general-purpose target. Concretely: under identical prompt and
harness conditions, a higher fraction of Josh implementations will run
and pass validation than Mesa implementations.

"Better" here is structural: the DSL's narrower target space means
fewer free decisions for the model to get wrong, fewer scaffolding
files to write, and a more direct mapping from spec to executable code.
The fewer-degrees-of-freedom effect should be visible in both
first-attempt success rate and in the regression-based ecology gate
that catches silent spec violations (a wrong growth ceiling, a missed
temperature clamp, etc. — see §Scoring).

H1 is the headline. The design also carries a second, controlled
contrast: a constrained-environment Josh arm (`josh-mcp`) that holds
the target fixed and removes the agent's shell, measuring the **cost
of constraint** within Josh. That arm and the partial-factorial
framing it creates are described in §Targets; it is pre-registered as
a question (a delta vs `josh`), not a competing hypothesis.

Earlier rounds of this design distinguished a separate "recovery
quality" hypothesis measured via a second-shot prompt with structural
feedback. That hypothesis is now folded into H1: the multi-invocation
flow (phase 5c) bakes plan-write → stub → implement → validate →
cleanup into a single cell, so what the scorer sees at the end is
already "the agent's best attempt given a chance to self-correct."
Distinguishing one-shot from recovery would require a second prompt
infrastructure that doesn't pay for itself in interpretability now
that the in-cell loop exists.

## Task

Every run, regardless of model, implements the same fixed task: the
**ForeverTree** model from the Josh tutorial series.
A grid of patches; ten trees per patch; growth driven by external
temperature and precipitation data with a quadratic temperature
response, a logistic precipitation response, and a small Gaussian
multiplicative noise term. Full specification in
[`prompts/BASE_PROMPT.md`](prompts/BASE_PROMPT.md) (the rung-5 master)
plus the boilerplate footer at [`prompts/SIDECAR.md`](prompts/SIDECAR.md).

ForeverTree was chosen because it is small enough to be tractable for
small models, includes external geospatial data (which exercises the
declarative-data-binding feature of Josh and forces Mesa
implementations to write alignment code), and has well-defined
expected outputs.

## Stack

Two off-the-shelf pieces, chosen to minimize what the harness itself
has to own:

- **[opencode][opencode]** runs the agent inside the `fortree` Docker
  container. `opencode run` is invoked non-interactively per phase.
  The tool surface offered to the model is `read`, `write`, `edit`,
  `glob`, `grep`, `bash`, and `webfetch`. **The `task` sub-agent tool
  is explicitly disabled** (`"task": false` in the rendered config) —
  it was the failure mode in the prior pilot where gemma issued 800
  malformed `task` calls in a single cell and where claude routed
  bash through it as a workaround for an earlier per-pattern bash
  allowlist that turned out to suppress the bash tool entirely. With
  `task` disabled and `permission.bash: "allow"` (string form, no
  per-command allowlist), bash surfaces normally to the model. The
  bash surface itself is therefore unconstrained inside the container
  — the real policy boundary is at the network layer (next bullet).
  The `josh-mcp` arm overrides this palette — `bash` off, plus the
  Josh pipeline delivered through an MCP server opencode spawns (see
  §Targets and §Tool palette).
- **[OpenRouter][or]** is the inference gateway. A single API key
  covers the model panel; the OpenRouter slug pins the model.

Each cell runs as a single k8s Job on GKE Autopilot — one Pod, with
an agent initContainer (`fortree:agent`) followed by a scorer
container (`fortree:scorer`). Egress is **monitored, not enforced**:
opencode's per-tool `trajectory.jsonl` records every `webfetch` URL
the model invoked, and that file ends up in the bucket via the
scorer's `mc mirror`. This is the sole egress observation layer.
The earlier kernel-enforced allowlist (per-run docker bridge +
dnsmasq + iptables + ipset) was retired in Phase 6 PR4; see §Egress
observability for the methodology consequences. A mirror-sidecar
runs alongside the agent container and continuously mirrors the
shared `/cell-data` volume to the bucket so an OOM'd cell still
leaves forensic state.

The same image is used for the scoring pass, so the agent's
`./run.sh` and the scoring re-run see byte-identical Python, Java,
Josh, and library versions. See
[`config/VERSIONS.md`](config/VERSIONS.md) for what's pinned, and
[`Dockerfile`](Dockerfile) for the image layering.

[opencode]: https://opencode.ai/
[or]: https://openrouter.ai/

## Design

### Prompt

A single master prompt describing the model in *domain terms only* —
no Josh-specific or Mesa-specific implementation guidance. The LLM is
told *which* tool to use (Josh or Mesa) so target-conformance can be
measured as a separate signal, but is given no guidance on *how* to
use it. The prompt body is [`prompts/BASE_PROMPT.md`](prompts/BASE_PROMPT.md);
the per-target directive is at
[`prompts/targets/{josh,mesa,josh-mcp}.md`](prompts/targets/); the operational
footer (AI environment, inputs, output contract, working-document
contract) is at [`prompts/SIDECAR.md`](prompts/SIDECAR.md). The `./run.sh`
contract ([`prompts/RUNSH.md`](prompts/RUNSH.md) — the agent authors and
executes `run.sh` as the measured unit of work) is appended only for the
full-tools arms (`josh`, `mesa`); the constrained `josh-mcp` agent has no
shell and never authors `run.sh` (the scorer runs its model), so it is
omitted there to avoid contradicting that arm's directive.

Earlier rounds of this design included a 1–5 rung prompt-detail
ladder. We collapsed it to the single master prompt: at full detail
the task is already hard enough to be a useful Josh-vs-Mesa
differentiator, and a second variation axis would dilute the
statistical power available within the budget. The rung-ladder
directory (`prompts/rungs/`) was deleted in Phase 6 PR6; headline
runs use the master prompt only.

The agent phase splits this single prompt into **8 sequential opencode
invocations against the same workspace**, one per todo from a fixed
list in `prompts/plans/<env>/PLAN_TEMPLATE.md` (seeded into
`/sandbox/PLAN.md`). The plan comes in two environment-specific variants
— `bash` for the full-tools arms (todos author and invoke `./run.sh`) and
`mcp` for the constrained `josh-mcp` arm (todos build and self-test the
model through the MCP tools, never `run.sh`). The
working document `PLAN.md` is both an output artefact and a working
reference re-read at the start of every sub-invocation.

**Why force this structure rather than trust the agent framework to
decompose the task?** Pre-phase-5c trials with both commercial
(claude) and open (gemma, minimax) models surfaced a recurring
failure mode: agents got caught up in orchestration concerns —
making `run.sh` executable, parsing the netCDFs, getting filenames
right — and skipped the actual ecological-modelling step. They'd
declare done when `run.sh` exited 0 and produced a CSV, even if the
CSV's growth values were nonsensical. opencode's native sub-agent
dispatch (the `task` tool) was the obvious affordance for
decomposition but proved unreliable — gemma issued malformed `task`
calls in tight loops; claude routed bash through it as a workaround
for the earlier per-pattern bash allowlist. We disabled the `task`
tool entirely and force-decompose orchestrator-side via the 8-step
external loop, taking some agency away from opencode in exchange for
a more controllable evaluation. The hypothesis is about how well the
models build broadly-correct *ecological* models, not how well they
overcome environment quirks; forcing the decomposition isolates the
variable we care about.

### Models

Configured via `MODEL` environment variable. All models are accessed
through OpenRouter using a single API key. The headline panel is nine
models spanning commercial and open weights:

| Short name | OpenRouter ID                            |
| ---------- | ---------------------------------------- |
| sonnet     | `anthropic/claude-sonnet-4.6`            |
| gemma      | `google/gemma-4-26b-a4b-it`              |
| kimi       | `moonshotai/kimi-k2.6`                   |
| minimax    | `minimax/minimax-m2.7`                   |
| mistral    | `mistralai/mistral-medium-3-5`           |
| glm        | `z-ai/glm-5.1`                           |
| qwen       | `qwen/qwen3.7-max`                        |
| nemotron   | `nvidia/nemotron-3-super-120b-a12b`      |
| deepseek   | `deepseek/deepseek-v4-pro`               |

The mapping lives in [`config/models.yaml`](config/models.yaml).
Pins are versioned slugs (not rolling `*-latest` aliases) so
re-running a batch later resolves to the same weights the headline
saw. The orchestrator logs the resolved `model_id` returned by
OpenRouter alongside the requested one and flags drift. Bump the
batch tag if any entry changes.

`olmo` (`allenai/olmo-3.1-32b-instruct`) was on the panel through the
pilot but **dropped before the headline reading** — it failed across
all of its cells under the multi-invocation flow (no usable work), so
it is excluded from `config/models.yaml`'s active panel and from the
analysis (`classify_no_scorer.py`'s `PANEL_MODELS` filters it out).
`claude` (opus) remains wired for sensitivity runs but is not in the
headline panel; `sonnet` is the commercial reference point.

The post-hoc LLM judge (§Scoring) is pinned to a **different model
family than any agent** — `codex` (`openai/gpt-5-codex`) — so no
agent cell is judged by its own model. This is what retires the
same-model self-judging concern from earlier design rounds.

### Targets

Three implementation targets per run, set via `TARGET` environment
variable:

- `josh` — generate a `.josh` model file plus any required `.jshd`
  preprocessing config, with the full tool palette (bash, webfetch,
  the Josh CLI).
- `mesa` — generate a Python module implementing the model using the
  Mesa 3.x framework, with the full tool palette.
- `josh-mcp` — generate Josh DSL exactly like `josh`, but from a
  **constrained** agent environment: no `bash`, and the Josh pipeline
  reachable only through an MCP server opencode spawns as a stdio
  subprocess. The agent authors `.josh` source and builds its `.jshd`
  through typed MCP tool calls instead of poking at a shell. See
  §Tool palette for the constrained runtime.

The harness contract (input data location, expected output CSV path
and columns) is identical for all three.

#### A second, orthogonal factor: tool environment

`josh` and `mesa` hold the agent's *environment* constant (full tool
palette) and vary only the implementation target — the clean 1-factor
design behind H1. `josh-mcp` introduces a second factor,
**environment ∈ {full-tools, mcp-constrained}**, holding the target
(Josh) fixed. There is no `mesa-mcp` — there is no Mesa MCP server —
so this is not a clean 2×2 but an **L-shaped partial factorial**:
three of the four `{josh, mesa} × {full, mcp}` cells are populated;
`(mesa, mcp)` is empty by design.

That geometry supports three pairwise contrasts, and it matters which
claim each one backs:

| Contrast | Holds fixed | Varies | Claim it backs |
|---|---|---|---|
| `josh` ↔ `mesa` | environment = full-tools | target | **H1** — DSL vs framework. **This is the headline number.** |
| `josh` ↔ `josh-mcp` | target = Josh | environment | **Cost-of-constraint** — what removing the shell buys or costs, within Josh. Controlled. |
| `josh-mcp` ↔ `mesa` | — | target *and* environment | **Product** claim only — the Josh-MCP workflow vs a generic Mesa dev loop. *Not* controlled (Mesa keeps bash). |

The rule that protects the science: **never let `josh-mcp` ↔ `mesa`
become the headline DSL-vs-framework figure.** It is a legitimate and
arguably more compelling *product* story, but it confounds target with
environment and is labeled as such wherever it appears.

`josh-mcp` is pre-registered as a **question, reported as a delta vs
`josh`**, not as a winner. Removing bash removes the agent's
debug/iterate loop (no live `./run.sh`, no ad-hoc inspection), so the
constrained arm could score *worse*. A small gap is the strong product
result ("you don't need to hand the agent a shell"); a large gap is
itself the interesting finding ("the MCP surface must expose more —
e.g. an output-shape inspector — to be viable"). Either outcome is
informative.

Because `josh-mcp` reuses the Josh target, scoring treats it
identically to `josh` (same conformance check, same ecology gate); the
only thing that differs is the agent's runtime and how its `run.sh` is
produced (§Run flow, §Tool palette).

### Sample size

The headline target is a **flat N per (model × target) combo**. The
launch matrix [`orchestration/matrix.csv`](orchestration/matrix.csv)
fires 9 models × 3 targets × 5 reps = 135 cells as the first wave;
the **idempotent re-rep loop** in
[`analysis/00_apply_scoring.ipynb`](analysis/00_apply_scoring.ipynb)
then tops *every* combo up to the target N by launching fresh cells for
the deficit and re-scoring deadline-killed partials in place — no combo
is advanced-or-dropped on stage-1 performance. The realised headline
panel is **8 reps per combo** (a few combos reached 10 from extra fill
waves), so the aggregated record analysed in
[`analysis/01_analysis.ipynb`](analysis/01_analysis.ipynb) is ~216
active cells across the 27 combos (the 9-model panel; olmo's cells are
excluded — it was dropped after failing across the board).

Each individual cell (one rep) is itself run as the fixed 8-step
multi-invocation flow — eight sequential opencode invocations against
the same workspace (§Run flow) — plus a separate scoring container
pass. (The two "8"s are unrelated: 8 *reps* per combo, each rep run as
8 *opencode steps*.) The
headline DSL-vs-framework figure remains the `josh`↔`mesa` contrast
over the full-tools cells; the `josh-mcp` cells add the
cost-of-constraint and product contrasts (§Targets). See §Open
methodology questions for `target_conformance`-denominator accounting.

Each run is a single agent container hosting the 8-step
multi-invocation flow, plus a separate scoring container pass (see
flow below).

### External climate inputs

The spec requires per-cell, per-step temperature and precipitation
forcings. Two design constraints conflict for the pilot phase:

1. **Realism.** Real climate data is what we'd use in production, so
   the experiment should look like a realistic data-binding task.
2. **Interpretability.** With real data we can't separate "the agent
   implemented the spec correctly" from "the agent decoded a quirky
   upstream-pipeline convention correctly", and we can't pre-compute
   the spec's predicted output to compare against.

The synthetic dataset (committed at
[`data/maxtemp_synthetic.nc`](data/maxtemp_synthetic.nc) and
[`data/precip_synthetic.nc`](data/precip_synthetic.nc), regenerated
from [`data/generate_synthetic_climate.py`](data/generate_synthetic_climate.py))
prioritises (2). It is CF-1.8 compliant (validated by the IOOS
`compliance-checker`), uses standard physical units (`K` for
temperature, `kg m⁻² s⁻¹` for precipitation flux convertible to
mm/year via `× 31_536_000`), has no NaN cells, and is deterministic
(byte-identical netCDFs across regen runs). The gradient is calibrated
to the spec's growth equation so a faithful implementation produces a
clear spatial pattern in year-100 heights: tallest where the
combination of temperature and precipitation puts both response curves
near their peaks, shortest where either driver is unfavourable. The
empirical year-100 distribution from the reference simulator
([`data/reference_sim.py`](data/reference_sim.py)) drives the
acceptance bands in [`harness/acceptance_ranges.json`](harness/acceptance_ranges.json);
see [§Scoring](#scoring) for the regression-based gate built on top of
those bands.

Because the `josh-mcp` agent has no shell, it cannot inspect the
netCDFs interactively (`ncdump`, `xarray.open_dataset`, …). So the
input contract is stated **descriptively** in
[`prompts/SIDECAR.md`](prompts/SIDECAR.md) §AI Inputs — file paths,
variable names and units, grid dimensions and ranges (`calendar_year`
101, `lat` 31, `lon` 50), the 2024–2123 simulation window, and the
separable gradients — enough for any arm to bind the data without
opening the files. The spec sheet is **descriptive-only**: it omits
the spatial-aggregation recipe and any data-binding code, which are
part of the task the experiment measures. It is shared by **all**
arms, not just `josh-mcp` — if only the constrained arm received it,
the `josh`↔`josh-mcp` contrast would differ in both tool access *and*
information and become uninterpretable. Giving it to every arm keeps
that contrast a clean tools-only comparison; the cost is that headline
numbers are not comparable to pre-spec-sheet batches, so the batch tag
is bumped and all three arms are re-baselined together.

For the headline run, the plan is to swap back to a real-world
dataset once a well-labeled source is available — at which point the
agent contract (variable names, units, file paths) is the only thing
that needs updating in `SIDECAR.md`.

## Run flow

A full **run** (one (model × target × run_id) cell) consists of one
agent phase + one scoring phase, executed as a single k8s Job: an
`fortree:agent` initContainer followed by an `fortree:scorer` main
container, sharing a `/cell-data` emptyDir volume. A mirror-sidecar
runs alongside and continuously syncs `/cell-data` to the bucket for
OOM forensics.

Both containers run the same `fortree` base image; using one image
for both roles guarantees the agent's `./run.sh` and the scoring
re-run see byte-identical Python, Java, Josh, and library versions.

### Step 1: Agent invocation (multi-invocation planning flow)

The Job manifest sets `OPENROUTER_API_KEY`, `MODEL`, `TARGET`,
`RUN_ID` from k8s Secrets / ConfigMap. The agent initContainer
renders `prompt_body.md` (target directive + SIDECAR appended to
BASE_PROMPT, plus the `run.sh` contract for the full-tools arms — see
§Prompt), seeds `workspace/PLAN.md` from the per-environment
`prompts/plans/<env>/PLAN_TEMPLATE.md`, and runs `agent-entrypoint.sh` which
invokes `opencode run` eight times in a row — one per pre-committed
step injection in `prompts/steps/step_NN_*.md` — with the per-step
prompt assembled as `prompt_body + step_NN`. Each invocation uses a
fresh opencode session; cross-step state lives entirely on disk in
`/sandbox/PLAN.md` and the workspace.

The prompt **names the target framework** ("implement this using
Josh" or "implement this using Mesa") so that tool-conformance can
be measured as a separate signal in step 2.

The agent reads, writes, edits, greps, and may invoke `./run.sh` to
self-validate at any point within or across the 8 sub-invocations.
Installed Python and Java package source is readable on disk.
Network access is constrained by opencode's `webfetch` allowlist;
the realised URL set is recorded in `trajectory.jsonl` and is the
sole egress observation layer (see §Egress observability).

For `josh-mcp` cells the agent runs under the constrained palette
(§Tool palette): no `bash`, the Josh pipeline via MCP. It self-tests
by calling `josh_run_simulation` rather than executing `./run.sh`
(which it cannot), and its deliverable is Josh source plus a
`mcp_calls.json` file listing the exact MCP tool calls that
reproduce the simulation end-to-end. At scoring time, a
harness-shipped generic Python runner (`runner.py`, installed
alongside a one-line `run.sh` shim by the setup initContainer) reads
`mcp_calls.json` and forwards every entry to the same `josh mcp`
stdio server via the Python MCP client — so the scoring run uses the
same MCP pathway the agent used during iteration, and the wall-clock
cost metric stays measured the same way across all arms.

A cell-total wall-clock backstop (`WALL_CLOCK_BACKSTOP_SEC`, default
1800s; bump to 3600s for headline runs given 27–39 min observed cell
times) and a per-invocation idle watcher (`IDLE_THRESHOLD_SEC`)
terminate the container if it loops or stalls. The behavior on
per-step failure is gated by `FAIL_FAST_ON_STEP_ERROR` (default
false: log and continue; true: abort on first non-zero step).

### Step 2: Tool-conformance check

Before invoking the agent's code, the orchestrator runs a
**tool-conformance check** on the generated workspace. This catches
the failure mode in which an agent told to use Mesa silently
implements the task in plain Python, or where a Josh prompt
produces a Mesa-like Python module instead of `.josh` files.

Two layers:

- **Mechanical check.** Grep-based. For Mesa targets, look for
  `import mesa`, `from mesa`, and instantiation of Mesa base
  classes. For Josh targets, look for files matching `*.josh`,
  validate them via `josh validate`, and check separately for
  `.jshd` preprocessing artefacts.
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

### Step 3: Validation (scoring)

The orchestrator starts a plain `docker run --network=none` of the
`fortree` image against the workspace volume, mounted read-only
except for a writable `./results/` directory. The scoring harness
([`harness/run_metrics.py`](harness/run_metrics.py)) runs:

- Invoke `./run.sh` with the standard input data. Capture exit
  code, stdout, stderr, wall time.
- Validate that `./output/results.csv` exists and has the required
  schema (data columns plus a cell-identifier; see §Scoring axes).
- Compute the substantive metrics: did_run, schema, internal
  consistency, spec-parameter conformance.
- Compute static metrics on the generated code: `src_loc`,
  `entropy_bits`.

All metric outcomes are written to a JSON record. The scorer
container exits. The per-cell `report.md` and the per-batch
`batch_report.md` aggregate these alongside the per-step
multi-invocation diagnostics (todos checked in PLAN.md, per-step
exit codes).

The run record carries the scoring outcome from step 3 plus the
per-step multi-invocation diagnostics from step 1
(`steps[*].exit_code`, `plan_todos.checked/.total`). The headline
figure compares Josh vs. Mesa on the scoring outcome.

### What the agent sees vs. what we measure

The agent's view of "did it work" is its own — whether `./run.sh`
exits cleanly during the agent phase. The orchestrator's
view is whether validation passes in step 3. These are not
necessarily the same.

This separation is deliberate. We want to measure the cell's quality
against external validation, not against the agent's own confidence.
An agent that calls `./run.sh` at the end of its multi-invocation
loop, sees exit 0, and marks the cleanup todo done has produced its
final attempt — even if the CSV turns out to be malformed when
validated in step 3. Self-correction across the 8 sub-invocations is
the agent's responsibility; the scorer judges only the final state.

## Scoring

How the harness decides what a completed agent run is "worth", what
each scorer field means, and the open scoring choices. All scoring is
mechanical at experiment time; the current scorer JSON schema is
`phase6-v2` (`harness/run_metrics.py:SCHEMA_VERSION`). The scorer
gates on schema, fits a per-cell `observed ~ predicted` regression
against the spec's deterministic prediction (the headline ecology
gate), captures real wall-clock under a 100-replicate × 100-year
`./run.sh` contract, and reports style metrics on the generated code.
The acceptance bands are derived from a spec-faithful Python reference
simulator ([`data/reference_sim.py`](data/reference_sim.py)) run
against the committed synthetic climate netCDFs, not hand-picked. The
reference simulator and the Mesa-target directive both run growth
dynamics under `decimal.Decimal` to match Josh's native `BigDecimal`
precision; the scorer surfaces both a mechanical
`conformance.uses_decimal` field and a fuzzy-judge Q4 to verify Mesa
cells honoured this.

The internal-consistency block (negative-growth fraction, age-step ≠
1, climate Spearmans) that earlier schemas carried is **gone** —
diagnostic during methodology-building, redundant once cells
consistently clear regression at β ≈ 1.

Multi-invocation diagnostics (`steps[*].exit_code`,
`plan_todos.checked/.total`) are reported per-cell alongside the
scoring metrics so the in-cell self-correction is inspectable.

### Scoring axes

Three axes evaluated against the workspace the agent container left
behind after the 8-step multi-invocation flow:

**1. Target conformance.** Did the agent use the named framework, or
sidestep it? Mechanical greps for `import mesa` / Mesa-class
subclassing on Mesa runs; `josh validate` exit zero on `.josh` files
for Josh runs. Catches the "agent produces a valid CSV via a plain-
Python fallback instead of using Josh" loophole.

**2. Spec-parameter conformance (ecology).** Did the agent's
year-100 outputs reproduce the spec's deterministic prediction under
a linear-regression fit? For each `(cell, replicate)` pair in the
agent's CSV, the scorer computes a deterministic *predicted* total
growth from the spec equation using the agent's own reported climate
values:

```
predicted(cell, rep) = Σ_{y = year_1 .. year_target}  Δh_max · %_T(T(y)) · %_P(P(y))
```

where `%_T` and `%_P` are the parabolic and logistic response curves
from [BASE_PROMPT.md §Growth Model](prompts/BASE_PROMPT.md). The
*observed* is the meanHeight at year == `target_year`. An OLS fit
`observed ~ predicted` yields slope β, intercept α, and R². Under a
faithful implementation **β → 1.0** (the O ~ N(1, 0.05) noise has mean
1 → no bias), **α → 0.0**, **R² → 1.0** (the spec equation is
deterministic; only noise causes residuals).

The acceptance gate is **β ∈ [0.95, 1.05]**, **α ∈ [−0.5, 0.5] m**,
**R² > 0.95** — loose enough to absorb implementation differences
(grid choice, climate interpolation, RNG seed), tight enough to catch
systematic dynamics errors like "agent used Δh_max=0.5" (β=0.5) or
"agent missed the temperature clamp" (α drifts, R² drops). The
reference's own fit values (β=1.000013, α=−0.000249, R²=0.999993,
n=155 000) are persisted in `acceptance_ranges.json` so the gate is
calibrated against the empirical noise floor of the reference
simulator. A coarse secondary check — mean meanHeight at year 100
within a 3σ band from the same reference run (`height_in_range`) —
catches totally-broken outputs that happen to hit β=1 by accident
(e.g. constant zeros). Sanity check, not the headline gate.

The chain producing the bands is fully deterministic, and the math
primitives live in [`harness/spec_model.py`](harness/spec_model.py),
shared between the reference simulator and the scorer's validator, so
band derivation and gate enforcement cannot drift:

```
data/generate_synthetic_climate.py   # writes the netCDFs
    ↓
data/reference_sim.py                # runs spec dynamics in Decimal,
                                     # writes harness/acceptance_ranges.json
    ↓
reference/regenerate_fixtures.py     # samples one replicate for the
                                     # smoke-test golden fixtures
    ↓
docker build --target scorer         # bakes acceptance_ranges.json
                                     # into fortree:scorer
```

**3. Style / code metrics.** Source LOC, comment LOC, imports LOC,
and token-level Shannon entropy of the generated code — the
conciseness-of-implementation signals directly relevant to H1 (DSL
targets should produce less scaffolding than general-framework
targets). Reported even when the cell fails to run, since code-shape
doesn't depend on execution success.

The **schema gate** sits underneath the ecology axis as a
precondition: without `csv_schema_ok=true` the ecology metrics are
undefined and recorded as null with `height_in_range=false`.

### Metrics

| Metric | Type | Source |
| --- | --- | --- |
| `target_conformance` | bool | Mechanical: Mesa imports + class subclassing, or `josh validate` exit zero. |
| `conformance.{imports_mesa, subclasses_model, uses_decimal, has_josh_files, has_jshd_files, josh_validate_exit_code}` | mixed | Per-target evidence fields backing the `target_conformance` rollup. `uses_decimal` is a Mesa-only signal (regex on `import decimal`); for Josh / josh-mcp it is always `false` (Josh runtime is `BigDecimal`-backed natively, not source-detectable). |
| `target_conformance_fuzzy` | enum | LLM-judge variant — see §LLM-judge passes. |
| `substantive_conformance` *(analysis-time, in `aggregated.csv`)* | bool | Rollup combining mechanical `target_conformance` with the fuzzy judge's Q1 verdict. True iff `target_conformance=True AND (Q1='yes' OR Q1 missing)`. Catches the "near-empty `.josh` shell + Python sidecar does the real work" failure mode the mechanical grep alone passes. Computed in [`analysis/aggregate.py`](analysis/aggregate.py); the entry gate for the cascade in [`analysis/01_analysis.ipynb`](analysis/01_analysis.ipynb)'s Panel A / B. |
| `csv_exists` | bool | `./output/results.csv` was written. |
| `csv_schema_ok` | bool | Subset-match required columns + target year present + required cols numeric-coercible. |
| `csv_schema_errors` | list | Failure messages when `csv_schema_ok=false`. |
| `csv_row_count` / `csv_rows_dropped_nan` | int | Total rows / rows dropped during NaN-filtering. |
| `script_was_executable` | bool | `True` if the agent self-chmod'd; `False` if the scorer's runner had to. |
| `did_run` | bool | `exit_code == 0 AND csv_exists AND csv_schema_ok`. |
| `exit_code` | int | `./run.sh` exit status. |
| `wall_time_seconds` | float | End-to-end wall-clock for `./run.sh`. Under the phase-6 contract `run.sh` carries preprocess + `--replicates 100` × 100 simulated years, so this is the real Josh-vs-Mesa execution-cost comparison. |
| `timed_out` | bool | True when `./run.sh` was killed by the scorer's per-invocation timeout. |
| `height_year100_mean` / `occupancy_year100_mean` | float | Mean `meanHeight` / `nTrees` across `(cell, replicate)` at `target_year`. Secondary sanity checks. |
| `height_in_range` / `occupancy_in_range` | bool | Within the 3σ mean band (secondary check). |
| `regression_fit.{beta, alpha, r2, n_observations}` | float/int | OLS `observed ~ predicted`. Spec-faithful targets: β=1.0, α=0.0, R²≈1.0. |
| `regression_fit_ok` | bool | **Headline ecology gate.** True iff β, α, R² all inside the bands in `acceptance_ranges.json`. |
| `regression_fit_reasons` | list[str] | Failure messages when `regression_fit_ok=false`. |
| `acceptance_ranges_used` | dict | The full `acceptance_ranges.json` as parsed — frozen-evidence record so a re-score is self-describing. |
| `src_loc`, `comment_loc`, `imports_loc` | int | Lines of generated code per category (`.jshd` binary excluded). |
| `entropy_bits` | float | Token-level Shannon entropy via `tiktoken` `cl100k_base`. |

The pre-registered acceptance ranges in
[`harness/acceptance_ranges.json`](harness/acceptance_ranges.json) were
committed before any experimental runs. **Do not modify this file
after experiments begin.** Git history is the audit trail; revisions
land via the re-scoring path (§Re-analysing completed runs) so
headline runs and re-analyses sit side-by-side.

### The `./run.sh` contract

A cell's `./run.sh` is the unit of work the scorer executes — and
under phase-6, the unit whose wall-clock counts as the headline cost
metric. The prompt ([BASE_PROMPT.md](prompts/BASE_PROMPT.md) + the
per-target directives) instructs the agent to produce a `run.sh` that:

1. Performs any preprocessing the target needs (Josh's `.jshd` binary
   preprocessing, Mesa's netCDF-to-Pandas conversion) **inside**
   `run.sh`.
2. Invokes the simulation with **`--replicates 100`** (or the
   framework equivalent — for Mesa, a 100-iteration loop emitting the
   same CSV schema) over **100 simulated years** from 2024 (target
   year 2123).
3. Writes `output/results.csv` with one row per `(cell, year,
   replicate)` tuple.

The fuzzy judge's Q3 asks whether `run.sh` honours this contract; the
wall-clock comparison is apples-to-apples only when Q3 = `yes`. Cells
where Q3 ∈ {`no`, `partial`} are reported but flagged in analysis.

**`josh-mcp` arm.** The constrained agent doesn't author `run.sh`. The
setup initContainer installs a one-line `run.sh` shim (`exec python
/sandbox/runner.py`) and a generic MCP-call forwarder (`runner.py`,
immutable) that reads agent-authored `/sandbox/mcp_calls.json` and
forwards every entry to the `josh mcp` stdio server via the Python MCP
client. The runner overrides `arguments.replicates` from the
`N_REPLICATES` env var (scorer sets it to 100), so the agent never
threads env through the JSON. Q3 walks into `mcp_calls.json` for this
arm.

### LLM-judge passes (post-hoc)

Four free-form questions answered against each completed cell's
workspace + transcript. Purpose: human-organisation and contract
verification, **not** headline scoring — the mechanical axes above
remain the experimental yardstick. The judge is pinned to a model
family distinct from every agent (`codex` = `openai/gpt-5-codex`, per
[`config/models.yaml`](config/models.yaml)), so no agent cell is
judged by its own model.

- **Q1 — "Did it use the right tool?"** Source + transcript; `yes` /
  `no` / `partial` with one sentence. Catches cells where the
  mechanical check passes (an `import mesa` somewhere) but the meat is
  in plain numpy.
- **Q2 — "Where did the agent get confused or devote its reasoning?"**
  Reads the transcript, names 1–3 specific steps/files/errors with
  disproportionate effort. Surfaces cross-cell failure patterns.
- **Q3 — "Does `./run.sh` carry preprocess + `--replicates 100` × 100
  years?"** The apples-to-apples verification for the wall-clock
  metric (see §The `./run.sh` contract).
- **Q4 — "Does the Mesa implementation use `decimal.Decimal` for
  growth dynamics?"** Mesa-only; verifies the Numerical-precision
  directive in [`prompts/targets/mesa.md`](prompts/targets/mesa.md).
  `yes` / `no` / `partial`, plus `n-a` for Josh / josh-mcp cells
  (Josh's runtime is `BigDecimal`-backed natively).

| Field | Value |
| --- | --- |
| Input | `workspace/` source files, `transcript.md`, `scorer.json` (mechanical outcome) |
| Output file | `<run-id>/scorer.fuzzy.json` |
| Judge model | `openai/gpt-5-codex` (the `codex` short name; `JUDGE_MODEL` default in [`containers/run-judge.sh`](containers/run-judge.sh)) |
| Schema | `{"q1", "q2", "q3", "q4", "judge_model_id", "schema_version": "fuzzy-v3"}` |
| When run | Post-hoc — never gates the cell from completing |
| Cost | ~$0.05–0.20 per cell |

The judge runs in-Pod inside the scorer container
([`containers/run-judge.sh`](containers/run-judge.sh)) against the
preserved `workspace/`, writing `scorer.fuzzy.json` back to the bucket
alongside the rest of the cell artefacts.

### Re-analysing completed runs

The scoring container is target-agnostic and stateless against an
agent workspace, so re-scoring against any completed run's
`workspace/` is one container invocation. As long as a cell's
`workspace/` is preserved (source files + their
`./output/results.csv`) and the synthetic-climate netCDFs in `data/`
are unchanged, scoring is fully reproducible — the `fortree:scorer`
image pins all harness Python / Java / Josh versions. The local
recipe (against an already-downloaded run dir):

```sh
docker run --rm --network=none \
  -v runs/<batch-tag>/<run-id>/workspace:/sandbox \
  -v $(pwd)/data:/sandbox/data:ro \
  fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa> \
  > runs/<batch-tag>/<run-id>/scorer.rescored.json
```

Persist re-scored output to a distinct filename rather than
overwriting — the original is the headline-batch evidence, the rescore
is a methodology delta.

#### Recovering excluded cells: the no_scorer rescore pass

The headline panel initially excluded cells that never produced a
`scorer.json` (`engagement_status=no_scorer`). A deterministic
classifier ([`orchestration/classify_no_scorer.py`](orchestration/classify_no_scorer.py))
labels every such cell from its saved artifacts into three
populations, so the right remedy is applied to each:

- **`RUNTIME_KILL`** — the agent ran cleanly (≥1 step exit 0) and was
  killed mid-step by the 2 h `activeDeadlineSeconds` (the slow
  josh-mcp loop), leaving a partial workspace. A **legitimate timeout
  failure**, not an exclusion → **re-score the partial** as
  `did_run=False` so it becomes an honest failure row.
- **`THROTTLE`** — ≥1 `AI_APICallError` (provider 429 / free-tier
  lockout) with zero clean steps. No scorable work → **re-run** the
  agent under clean conditions.
- **`INFRA_NONSTART`** — no `agent_meta/` at all (the agent container
  never started) → **re-run**.

The re-score runs only the scorer (no agent) against the saved
workspace, **in the same k8s Pod spec** so `sim_wall_seconds` and any
JVM/Decimal timing stay drawn on the same `Performance`/`n2` node as
the rest of the panel. The agent-phase metrics (`agent_wall_seconds`,
`agent_cost_usd`, tool calls, tokens) come from the original run's
preserved `agent_meta/`, so they stay authentic to the real agent run.
Mechanism, template, and operational flow are in [§Run flow](#run-flow)
and the [`orchestration/k8s_rescore.sh`](orchestration/k8s_rescore.sh) wrapper;
`olmo` is filtered out of the classifier up front (`PANEL_MODELS`) and
reported as `SKIPPED`.

**Why this is bias-free.** No `RUNTIME_KILL` cell is re-run — its
already-recorded behavior is scored with the *same deterministic
scorer* applied to every other cell. The only re-runs are
`THROTTLE`/`INFRA_NONSTART` cells that produced **zero scorable work**
(the model never attempted the task), which is a standard
infra-failure replacement, not a selective re-roll of an outcome. Once
`scorer.json` exists a cell is no longer `no_scorer`, so a rescored
`did_run=False` row counts in the Panel-A denominator — the exclusion
that inflated pass rates is closed.

## Egress observability

One layer — opencode's tool config gates what the model can ask
`webfetch` to reach, and opencode's `trajectory.jsonl` records every
URL the model actually invoked. That trajectory is the post-hoc
evidence record for the headline batch.

> **Phase 6 methodology delta.** Earlier pilots ran behind a
> kernel-enforced egress firewall (per-run docker bridge + dnsmasq +
> iptables + ipset, REJECT on anything outside an allowlist of docs
> hosts) with a passive DNS log as the secondary observation layer.
> The Phase 6 refactor moves cell execution to k8s Pods, which can't
> express the `CAP_NET_ADMIN` + shared-netns pattern that kernel
> enforcement required, and drops the DNS-log sidecar to keep the Pod
> shape minimal. The egress boundary is now **monitored, not
> enforced**, and `trajectory.jsonl` is the sole observation record.
> Threats-to-validity implications discussed below.

### Tool-config layer (soft)

opencode's per-tool configuration in
[`config/opencode.template.json`](config/opencode.template.json) is
the only in-process constraint:

- `read`, `write`, `edit`, `glob`, `grep`, `bash`, `webfetch` enabled.
- `task` (sub-agent dispatch) **explicitly disabled** — disabling it
  was necessary to stop gemma's malformed-call loop, and surfacing
  `bash` to the model directly removed the need for `task`-routed
  workarounds.
- `permission.bash: "allow"` (string form). The per-command bash
  allowlist that earlier versions of this template carried turned out
  to suppress the `bash` tool's exposure to the model entirely
  (caught on the phase-5a pilot batch when claude's transcript
  showed it reasoning "I don't see a bash tool in my function list").
  With the string form, bash is exposed normally.
- `permission.webfetch: "allow"` for hosts in the docs allowlist
  (table below). opencode enforces this in-process; the network layer
  no longer re-enforces it at the kernel.

### Observation: opencode trajectory log

`trajectory.jsonl` (emitted by `opencode export`) records every
`webfetch` URL the model invoked, in order, with timestamps. The
analysis pipeline joins it against
[`config/docs_categories.yaml`](config/docs_categories.yaml) to
produce `docs_paths_by_category` and a `dns_unexpected_hosts` field
in the run manifest, so any URL outside the docs allowlist surfaces
clearly post-hoc.

What trajectory.jsonl does *not* see: indirect egress paths, e.g. an
agent-authored `run.sh` that shells out to `curl` or imports
`urllib`. opencode's `bash` tool is unconstrained inside the
container, so a determined run could in principle reach an arbitrary
host without it appearing in the trajectory. The validity argument
relies on (a) the pilot batches showing agents don't have a habit of
shelling out for HTTP, (b) the headline batch being inspectable
post-hoc and re-runnable if anomalies surface — see Threats to
validity.

### Documentation host allowlist

The hosts the agent is steered toward via `permission.webfetch` in
[`config/opencode.template.json`](config/opencode.template.json) (no
longer enforced at the kernel — see the methodology delta above):

| Host                                | Purpose |
| ----------------------------------- | ------- |
| `joshsim.org`                       | Josh tutorials and language reference. |
| `mesa.readthedocs.io`               | Mesa documentation, including the examples gallery. |
| `readthedocs.io`                    | Other RTD-hosted scientific docs (subdomain wildcard). |
| `docs.python.org`                   | Python standard library reference. |
| `numpy.org`                         | numpy documentation. |
| `docs.scipy.org`                    | scipy documentation. |
| `pandas.pydata.org`                 | pandas documentation. |
| `docs.xarray.dev`                   | xarray documentation. |
| `unidata.github.io`                 | netCDF4 documentation. |
| `raw.githubusercontent.com`         | Josh's `llms-full.txt` lives here. |

Specifically not on the list: github.com proper (including issue
threads, gists, and source-browse links from doc pages), Stack
Overflow, Reddit, blog hosts, PyPI's metadata pages. The agent can
read installed package source on disk, which is a strict superset of
what github.com source-browse would provide.

### Model API host

`openrouter.ai` is a separate entry in the opencode `webfetch`
allowlist so that the inference call can be made. It is excluded
from `docs_*` metrics in the post-hoc analysis; OpenRouter traffic
is recorded under its own counters (`prompt_tokens`,
`completion_tokens`) sourced from the OpenRouter response itself.

### Category tags for documentation hosts

The mapping from URL to category — `reference`, `example`,
`tutorial`, `api`, `general` — lives in
[`config/docs_categories.yaml`](config/docs_categories.yaml), a
sidecar file consumed only at analysis time. The orchestrator
joins it to opencode's trajectory log post-hoc to produce
`docs_paths_by_category` in the run manifest.

### Asymmetry, acknowledged

Mesa has a mature documentation corpus with an extensive examples
gallery; Josh has tutorial pages and `llms-full.txt`. This is not
an apples-to-apples comparison. The asymmetry is a real reflection
of where each tool is in its lifecycle, and the run-level traffic
logs make the imbalance visible in the data rather than hiding it.
The discussion section of the paper will acknowledge this directly.

### Scoring phase network

The scoring phase runs in a separate container from the agent
(`--network=none` under local orchestration; under k8s, network is
allowed only for the bucket upload via `mc`). The scorer never calls
a model and never reaches the docs hosts — there is no agent to
observe at scoring time.

### Reading installed package source

The agent can `cat $(python -c "import mesa; print(mesa.__file__)")`
and follow imports. The system Python's `site-packages` directory is
readable inside the container. This mirrors realistic RSE behavior
and is symmetric across targets.

### Pre-installed environment

Pinned at image build time in [`Dockerfile`](Dockerfile) and
[`config/requirements.txt`](config/requirements.txt). No `pip install`,
no `apt-get install` during the agent phase:

- Python 3.11 with pinned versions of: mesa, numpy, pandas, scipy,
  xarray, netCDF4, rasterio, tiktoken, jinja2, compliance-checker —
  installed into the system Python from `python:3.11-slim-bookworm`.
  (`compliance-checker` is for CF/ACDD validation of the
  synthetic-climate dataset; see §External Inputs.)
- Eclipse Temurin / OpenJDK 21.
- The Josh CLI as a wrapper around `joshsim-fat.jar`, sha256-pinned
  at build time (SchmidtDSE/josh has no tagged releases).
- opencode 1.14.50, installed via the upstream installer and
  symlinked into `/usr/local/bin`.

Exact pins in [`config/VERSIONS.md`](config/VERSIONS.md).

## Tool palette

opencode's tool palette is configured in
[`config/opencode.template.json`](config/opencode.template.json),
which the orchestrator renders per run. The configured tools are:

| Tool        | Scope |
| ----------- | ----- |
| `read`      | Workspace, installed package source. |
| `write`     | Workspace only. |
| `edit`      | Workspace only. |
| `glob`      | Workspace. |
| `grep`      | Workspace, installed package source. |
| `bash`      | Unconstrained inside the container (`permission.bash: "allow"`). The agent may invoke `./run.sh` freely to self-test, run `chmod`, etc. |
| `webfetch`  | Documentation host allowlist plus `openrouter.ai`. |
| `task`      | **Disabled.** Sub-agent dispatch produced a malformed-call loop with gemma and is unnecessary now that `bash` surfaces normally. |

The bash surface is intentionally wide because the substantive
policy boundary is the opencode `webfetch` allowlist and the
post-hoc `trajectory.jsonl` audit (Phase 6 monitored-egress model).
Disk writes and syscalls inside the unprivileged Pod are not
separately gated — the Pod is ephemeral and per-cell, and the
shared `/cell-data` volume is the only persistent surface.

Indirect egress paths (an agent-authored Python script calling
`urllib.request.urlopen`, or a `curl` shelled out from `run.sh`) are
**not** filtered by `webfetch`; they reach the network directly. The
monitored-egress trade-off is acknowledged in the Threats to
validity section above. If a future batch surfaces evidence of
indirect-egress abuse, a Pod-level `NetworkPolicy` or cluster-wide
Cloud DNS logging can be reintroduced without reverting the rest of
the refactor.

### The josh-mcp constrained palette

The `josh-mcp` arm renders a sibling opencode config,
[`config/opencode.josh-mcp.template.json`](config/opencode.josh-mcp.template.json),
that differs from the shared template in three ways:

- **`bash` is disabled.** The agent has no shell — it cannot run
  `./run.sh`, `ncdump`, or any ad-hoc command. (`webfetch` stays
  enabled, so the agent still reads the Josh docs.)
- **An MCP server block is added.** opencode spawns `josh mcp` as a
  local stdio subprocess; the Josh fat jar's `mcp` subcommand
  (from [SchmidtDSE/josh#440](https://github.com/SchmidtDSE/josh/pull/440))
  exposes four tools that surface to the agent as
  `josh_validate_simulation`, `josh_preprocess_data`,
  `josh_run_simulation`, and `josh_discover_config`.
- **`"josh*": true`** exposes those tools to the `coder` agent;
  `read`/`write`/`edit`/`glob`/`grep`/`webfetch` stay on, `task` stays
  off.

| Tool | `josh` / `mesa` | `josh-mcp` |
| ---- | --------------- | ---------- |
| `read`/`write`/`edit`/`glob`/`grep` | on | on |
| `webfetch` | on | on |
| `bash` | on | **off** |
| `task` | off | off |
| `josh_*` (MCP) | — | **on** |

So the model drives the Josh pipeline — validate, preprocess a netCDF
into a `.jshd`, run a simulation — through a typed tool surface rather
than a shell, mirroring the real Josh product direction (Josh exposed
as an MCP tool a model can call). This isolates "just the Josh parts"
of the agent's loop. Because the constrained agent cannot execute
`./run.sh`, the deliverable shape differs from the bash arms: instead
of authoring `run.sh` itself, the agent authors **`mcp_calls.json`** —
a JSON array of `{"tool": ..., "arguments": {...}}` objects mirroring
the MCP `tools/call` interface that records the exact sequence of MCP
calls the agent validated during iteration. At scoring time a
harness-shipped generic Python runner (`runner.py`) reads that JSON
and forwards every entry to the `josh mcp` stdio server via the
Python MCP client. A one-line `run.sh` shim (`exec python
/sandbox/runner.py`) makes the existing scorer path uniform across
all three arms. The runner mirrors the bash arms' replicate-count
contract: it reads `N_REPLICATES` from env (the scorer's
[harness/runner.py](harness/runner.py) sets it to 100 before invoking
`./run.sh`) and silently overrides `arguments.replicates` on any
`run_simulation` call — so the agent writes whatever count it
self-tested at without threading env through the JSON. A naming
convention the directive enforces (`simulation.josh` / simulation
`Main` / `external temperature` + `precipitation`) keeps the
`.josh`-source surface narrow; units, paths, and variable names
inside `mcp_calls.json` originate with the agent. The runner +
install wiring lives in [`harness/runner.py`](harness/runner.py) and
[`containers/josh-mcp-runner.py`](containers/josh-mcp-runner.py).

## Stopping conditions

No iteration budget. The agent decides when to stop. Three backstops
protect against runaway loops:

- **Wall-clock backstop.** `WALL_CLOCK_BACKSTOP_SEC`, default 1800s
  (30 min) but currently set to 3600s (60 min) in `.env` for headline
  runs. Safety net, not a primary metric.
- **Idle-watcher backstop.** `IDLE_THRESHOLD_SEC`, default 120s but
  currently 600s (10 min) in `.env`. Kills the agent if the opencode
  session DB hasn't been touched for that long — catches genuine
  stalls without false-positive killing of long synchronous work.
- **Token budget backstop.** `TOKEN_BACKSTOP`, default 100k
  completion tokens per agent phase.

A run that hits any backstop is recorded with a `backstop_hit` flag
and proceeds to validation against whatever state the agent left in
the workspace.

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
- **Egress is monitored, not enforced** (Phase 6 methodology delta —
  see §Egress observability). Earlier pilots ran behind a
  kernel-enforced REJECT on anything outside the docs allowlist plus
  a passive DNS log; the k8s refactor dropped both. The Pod model
  can't express the per-run docker-bridge + `CAP_NET_ADMIN` pattern
  that kernel enforcement required, and the DNS sidecar was retired
  alongside it to keep the Pod shape minimal — opencode's
  `trajectory.jsonl` already records every `webfetch` URL the model
  invoked. Indirect egress paths (e.g., an agent-authored `./run.sh`
  shelling out to `curl` or `urllib`) are no longer visible at all:
  they don't appear in `trajectory.jsonl` (the model didn't call
  `webfetch`) and there's no DNS log anymore. The validity argument
  depends on the pilot batches showing agents don't have a habit of
  shelling out for HTTP; if the headline batch surfaces concerns
  about indirect paths, a GKE Pod-level egress `NetworkPolicy` or
  cluster-wide Cloud DNS logging can be added without reverting the
  rest of the refactor.
- Several methodological choices are still open and will affect
  what the experiment can claim. See §Open methodology questions
  below.

## Open methodology questions

1. **Regression band tolerance calibration.** The β/α/R² tolerances
   in [`harness/acceptance_ranges.json`](harness/acceptance_ranges.json)
   (β ∈ [0.95, 1.05], α ∈ [−0.5, 0.5], R² > 0.95) are hand-picked to
   give implementation tolerance while catching gross dynamics errors.
   They are not derived from observed agent-implementation variance.
   With the headline batch now in hand, the tolerances can be
   re-calibrated against the empirical spread of "clearly faithful"
   vs "clearly broken" runs. The regression *targets* (β=1, α=0,
   R²→1) are derived from the reference simulator and are not in
   question.

2. **`target_conformance=False` in the denominator.** Does a run that
   produces a valid CSV without using the named framework count toward
   H1's denominator? Likely answer: report pass-rate conditional on
   conformance, plus a separate conformance-rate-per-model figure. A
   reporting convention, no code change needed; the
   `substantive_conformance` rollup (§Metrics) is the gate the
   notebooks already apply.

3. **Single task (generalisation).** ForeverTree is one task; the
   fewer-degrees-of-freedom effect may not transfer to harder modeling
   targets. Repeated in Threats to validity.

The earlier **model-panel finalisation** and **same-model
self-judging** open items are now closed: the headline ran a
nine-model panel (olmo dropped after failing across the board), and
the post-hoc judge is `gpt-5-codex` — a family distinct from every
agent — so no cell is self-judged (§Models, §Scoring).

## Citation

If you use this harness or its outputs, please cite the USRSE'26
paper (TODO once accepted) and the Josh platform.

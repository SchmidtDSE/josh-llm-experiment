# Experimental design — ForeverTree LLM Experiments

The methodology behind the experiment described in [README.md](README.md). For installation and how to run, see the README; for the engineering build state and pending work (phase 4d durable upload, phase 5b recovery loop, rungs 2–4), see [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

> **Status (post phase-5a).** Phases 1–4c are merged and the
> scoring-revision half of phase 5 (conformance, internal-consistency
> metrics, chmod self-heal, schema loosening) is also merged. What
> remains: the recovery loop (phase 5b), durable upload (phase 4d),
> rungs 2–4. The synthetic-climate dataset described in §External
> climate inputs replaces the original Cal-Adapt files for the pilot
> and headline batches.

This is the AI-evaluation experiment reported in our USRSE'26 submission on the [Josh][josh] vegetation modeling platform.

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
both. **As of phase-5a, only H1 is directly measurable** — the
recovery loop (phase 5b) is designed in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) but not implemented;
whether it lands before the headline batch is an open scoping
question (see §Open methodology questions).

## Task

Every run, regardless of model or prompt rung, implements the same
fixed task: the **ForeverTree** model from the Josh tutorial series.
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
- **[OpenRouter][or]** is the inference gateway. A single API key
  covers the model panel; the OpenRouter slug pins the model.

The agent container runs on its own Docker bridge network with a
`dnsmasq` sidecar configured both as the DNS resolver and as the
kernel-level egress allowlist (iptables + ipset, populated from
dnsmasq's `ipset=` directives). Egress is **enforced**, not merely
observed: anything outside the documentation-host allowlist plus
OpenRouter is dropped at the host's network stack. The DNS log
records every query the agent makes — this is the evidence record
for what hosts the agent reached, regardless of which process inside
the container initiated the request. Combined with opencode's
per-tool trajectory log, this gives full observability of what the
agent fetched without operating a TLS-intercepting proxy.

The same image is used for the scoring pass with `--network=none`
and a read-only workspace mount, so the agent's `./run.sh` and the
scoring re-run see byte-identical Python, Java, Josh, and library
versions. See [`config/VERSIONS.md`](config/VERSIONS.md) for what's
pinned, and [`Dockerfile`](Dockerfile) for the image layering.

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

| Short name | OpenRouter ID                            | Status in current batches |
| ---------- | ---------------------------------------- | ------------------------- |
| claude     | `anthropic/claude-sonnet-4.5`            | active panel              |
| gemma      | `google/gemma-3-27b-it`                  | retained for headline run |
| gemma4     | `google/gemma-4-26b-a4b-it`              | active panel (pilots use this) |
| kimi       | `moonshotai/kimi-k2`                     | active panel              |
| minimax    | `minimax/minimax-m2`                     | active panel              |
| mistral    | `mistralai/mistral-large-2411`           | active panel              |

The mapping lives in [`config/models.yaml`](config/models.yaml).
Model IDs are pinned to specific versions for reproducibility and
noted in the run manifest. The orchestrator logs the resolved
`model_id` returned by OpenRouter alongside the requested one and
flags drift.

`gemma4` is a newer release than `gemma`; pilot batches have used
`gemma4` so its behaviour is more characterised. The headline panel
will pick one (likely `gemma4`) after pilot validation across
single-cell mini-batches per model.

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
§Open methodology questions below for sample-size and
target_conformance accounting considerations.

Each run is one container invocation for the one-shot phase and one
follow-up container invocation for the recovery phase (see flow
below).

### External climate inputs

The spec requires per-cell, per-step temperature and precipitation
forcings. Two design constraints conflict for the pilot phase:

1. **Realism.** Real climate data is what we'd use in production, so
   the experiment should look like a realistic data-binding task.
2. **Interpretability.** With real data we can't separate "the agent
   implemented the spec correctly" from "the agent decoded a quirky
   upstream-pipeline convention correctly", and we can't pre-compute
   the spec's predicted output to compare against.

The phase-5a synthetic dataset (committed at
[`data/maxtemp_synthetic.nc`](data/maxtemp_synthetic.nc) and
[`data/precip_synthetic.nc`](data/precip_synthetic.nc), regenerated
from [`data/generate_synthetic_climate.py`](data/generate_synthetic_climate.py))
prioritises (2). It is CF-1.8 compliant (validated by the IOOS
`compliance-checker`), uses standard physical units (`K` for
temperature, `kg m⁻² s⁻¹` for precipitation flux convertible to
mm/year via `× 31_536_000`), has no NaN cells, and is deterministic
(byte-identical netCDFs across regen runs). The gradient is
calibrated to the spec's growth equation so a faithful
implementation produces a visualizable diagonal pattern in year-10
heights from ~10 m at the centre to ~0 m at the cold/dry corner.

For the headline run, the plan is to swap back to a real-world
dataset once a well-labeled source is available — at which point the
agent contract (variable names, units, file paths) is the only thing
that needs updating in `SIDECAR.md`.

## Run flow

A full **run** (one (model × rung × target × run_id) cell) consists
of five orchestrated steps spanning two opencode invocations against
a single per-run Docker bridge network, plus two validation passes
by a separate scoring container.

The agent network and dnsmasq sidecar are brought up at step 1 and
kept alive across steps 1–5 so that step 4's recovery prompt sees
step 1's workspace and the full DNS log accumulates across both
agent phases. The network and sidecar are torn down after step 5.

The scoring container runs the **same** `fortree` image under plain
Docker, `--network=none`, with a read-only mount of the agent
workspace. Using one image for both roles guarantees the agent's
`./run.sh` and the scoring re-run see byte-identical Python, Java,
Josh, and library versions.

### Step 1: Initial prompt

The orchestrator validates env vars (`OPENROUTER_API_KEY`, `MODEL`,
`RUNG`, `TARGET`, `RUN_ID`), creates a per-run Docker bridge network,
starts the dnsmasq sidecar on it with query logging enabled, and
runs `opencode run` non-interactively against the rendered config
inside the `fortree` image, bound to that network.

The prompt **names the target framework** ("implement this using
Josh" or "implement this using Mesa") so that tool-conformance can
be measured as a separate signal in step 2.

The agent reads, writes, edits, greps, and may invoke `./run.sh` to
self-validate. Installed Python and Java package source is readable
on disk. Network access is constrained by opencode's `webfetch`
allowlist and observed by the dnsmasq sidecar.

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

The orchestrator starts a plain `docker run --network=none` of the
`fortree` image against the workspace volume, mounted read-only
except for a writable `./results/` directory. The scoring harness
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

The orchestrator invokes opencode a second time against the **same**
workspace and the **same** per-run bridge network used in step 1,
this time with a **recovery prompt**. The recovery prompt:

- References the same workspace (the agent sees its prior
  implementation, exactly as it left it).
- Includes the validation results from step 3 — what ran, what
  failed, what was missing.
- Does **not** include the acceptance ranges or any new
  information about correctness criteria. Only the binary /
  structural outcomes from validation are surfaced.
- Uses the same prompt-style guidance as the original rung,
  preserving the rung's detail level.

The opencode tool allowlist and the dnsmasq sidecar configuration
are the same as step 1 — the policy and observation layer are locked
at step 1 and left alone for the duration of the run.

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

## Scoring axes

Four orthogonal scoring axes, all applied at both step-3 and step-5
validation:

**1. Target conformance.** Did the agent use the named framework, or
sidestep it? Mechanical greps for `import mesa` / Mesa-class
subclassing on Mesa runs; `josh validate` exit zero on `.josh` files
for Josh runs. Caught the "agent produces a valid CSV via a Python
fallback instead of using Josh" loophole in the phase-5a pilot batch.

**2. Schema gate.** Did the agent's `./run.sh` produce
`./output/results.csv` and does the CSV carry the required columns?
The gate is subset-match (extras OK, order irrelevant), NaN-tolerant
(rows with NaN in required numeric cols are filtered and counted, not
failed), and demands the acceptance target year is present. The
scorer self-heals `chmod +x run.sh` so the agent's failure to chmod
is captured separately (`script_was_executable`) without gating the
measurement.

**3. Internal consistency.** Given the agent's own outputs, does the
simulation behave self-consistently with respect to the spec's
constraints? Hard structural checks (negative growth fraction,
growth-rate ceiling fraction, age-step != 1 fraction, nTrees-change
fraction). All four should be 0 under any faithful implementation;
non-zero values are direct spec violations the other gates can't see.
A predicted-vs-observed Δh `r²` is planned (see §Open methodology
questions below) — the within-cell-across-years Spearmans currently
in the manifest are noise on the synthetic dataset (low temporal
variance by design) and will be replaced with a spatial `r²` against
the spec's prediction.

**4. Spec-parameter conformance.** Did mean tree height and mean
occupancy at year 10 fall in the pre-registered acceptance ranges
(`height_year10` and `occupancy_year10` in
[`harness/acceptance_ranges.json`](harness/acceptance_ranges.json))?
Meaningful only at rungs where the relevant parameters were specified
in the prompt. **The methodology of these ranges is under review** —
they currently pass scientifically-broken runs (e.g. h@10 = 8e-10 m
on the prior Cal-Adapt-based pilot). The plan is to either drop them
or fold them into a derived `cell_passed` field combining
conformance, schema, consistency, and parameter signals.

## Metrics

All scoring is mechanical at experiment time. Manual review of the
archived artifacts can supplement post-hoc. Per-batch rollups
generated automatically by `orchestration/generate_batch_report.py`
into `runs/<batch-tag>/batch_report.md`.

The current scorer JSON schema is `phase5a-v1` (see
`harness/run_metrics.py:SCHEMA_VERSION`).

| Metric                          | Phase        | Type    | Source |
| ------------------------------- | ------------ | ------- | ------ |
| `target_conformance`            | step 2       | bool    | Mechanical: Mesa imports + class subclassing, or `josh validate` exit zero. |
| `conformance.{imports_mesa, subclasses_model, has_josh_files, has_jshd_files, josh_validate_exit_code}` | step 2 | mixed | Per-target evidence fields backing the `target_conformance` rollup. |
| `target_conformance_fuzzy`      | step 2 (opt) | enum    | `null` placeholder; LLM-judge variant deferred. |
| `csv_exists`                    | step 3       | bool    | `./output/results.csv` was written. |
| `csv_schema_ok`                 | step 3       | bool    | Subset-match required columns + target year present + required cols numeric-coercible. |
| `csv_schema_errors`             | step 3       | list    | Specific failure messages when `csv_schema_ok=false`. |
| `csv_row_count`                 | step 3       | int     | Total rows in the CSV (pre-filter). |
| `csv_rows_dropped_nan`          | step 3       | int     | Rows dropped during NaN-filtering on required numeric cols. |
| `script_was_executable`         | step 3       | bool    | `True` if the agent self-chmod'd; `False` if the scorer's runner had to. |
| `did_run`                       | step 3       | bool    | `exit_code == 0 AND csv_exists AND csv_schema_ok`. |
| `exit_code`                     | step 3       | int     | `./run.sh` exit status. |
| `consistency.growth_rate_{min,max,mean}_m` | step 3 | float | Descriptive stats on Δh across all `(cell, year→year+1)` transitions. |
| `consistency.growth_rate_negative_frac`    | step 3 | float | Fraction of transitions with Δh < 0. Spec value: 0. |
| `consistency.growth_rate_above_ceiling_frac` | step 3 | float | Fraction with Δh > 1.15 m/yr (Δh_max × (1+3σ)). Spec value: 0. |
| `consistency.age_step_{mean, off_one_frac}` | step 3 | float | Mean age increment and fraction != 1.0. |
| `consistency.ntrees_change_frac`           | step 3 | float | Fraction of transitions where `nTrees` changed. Spec value: 0. |
| `consistency.growth_temp_spearman`         | step 3 | float | Currently per-cell-across-years; **scheduled for replacement** (see open questions). |
| `consistency.growth_precip_spearman`       | step 3 | float | Same; same caveat. |
| `height_year10_mean`            | step 3       | float   | Mean of `meanHeight` at target year. |
| `occupancy_year10_mean`         | step 3       | float   | Mean of `nTrees` at target year. |
| `height_in_range`               | step 3       | bool    | `height_year10_mean` within `acceptance_ranges.json` bounds. |
| `occupancy_in_range`            | step 3       | bool    | Same for occupancy. |
| `prompt_tokens`, `completion_tokens` | step 1  | int     | OpenRouter response. |
| `src_loc`, `comment_loc`, `imports_loc` | step 3 | int | Lines of generated code per category (replaces planned `relevant_loc`). |
| `entropy_bits`                  | step 3       | float   | Token-level Shannon entropy of the generated code via `tiktoken` `cl100k_base`. |
| `wall_time_seconds`             | step 1 / 3   | float   | End-to-end time per phase. Reported, not used for scoring (provider latency confounds). |

**Step-4/5 recovery metrics** (`recovery_*` field family,
`oneshot_*` prefixing, etc.) are listed in `IMPLEMENTATION_PLAN.md`
phase 5b but not yet produced; H2 measurement is pending those.

**Egress / docs-traffic metrics** (`webfetch_request_count`,
`docs_*`, `dns_distinct_hosts`, `dns_unexpected_hosts`) are also
pending — they require a separate joiner (`harness/docs_log.py`,
phase 5b) over the existing per-run `trajectory.jsonl` and `dns.log`.
The raw artefacts are captured per-run today; only the aggregated
metrics await implementation.

The path-level metrics (`docs_*`) come from opencode's trajectory
log — every `webfetch` invocation records its URL. The host-level
metrics (`dns_*`) come from the dnsmasq sidecar's query log and
catch any non-`webfetch` egress attempt (e.g., a Python script the
agent wrote calling `urllib.request.urlopen`).

The pre-registered acceptance ranges live in
[`harness/acceptance_ranges.json`](harness/acceptance_ranges.json) and were
committed before any experimental runs. **Do not modify this file
after experiments begin.** Git history is the audit trail.

## Egress observability and isolation

Two layers, both committed to Git and frozen for the headline
experiment.

### Policy layer: opencode tool config

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
- `permission.webfetch: "allow"` for hosts in the egress allowlist
  (the hard policy boundary, below).

This layer is mostly a vehicle for shape: it makes the tool surface
visible to the model. The substantive policy is the next layer.

### Policy layer: hard egress allowlist (kernel-enforced)

A `dnsmasq` sidecar runs on the agent's per-run Docker bridge network
in a privileged-NET capabilities mode. Configured in
[`orchestration/dnsmasq.conf`](orchestration/dnsmasq.conf) and built
from [`Dockerfile.dnsmasq`](Dockerfile.dnsmasq). It does three things:

1. **Resolves DNS** for the agent container (sidecar shares its netns
   with the agent via `--network=container:dnsmasq-<id>`).
2. **Logs every DNS query** (host, timestamp, resolution) to a
   per-run `dns.log` — the evidence record for what hosts the agent
   reached, independent of which process inside the container
   initiated the request.
3. **Populates an `ipset` allowlist** (driven by dnsmasq's `ipset=`
   directives) that iptables consults on the OUTPUT chain. Anything
   resolving to an IP not in the ipset is **dropped** at the kernel,
   not merely logged. The smoke-test firewall probe asserts this
   enforcement (CI workflow `smoke.yml`).

Together: the in-process tool config gates what the model can ask
opencode to do; the network layer gates what any process in the
container can actually reach. The latter is the substantive boundary
for the validity argument.

### Documentation host allowlist

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

The canonical allowlist lives in
[`orchestration/dnsmasq.conf`](orchestration/dnsmasq.conf) as
`ipset=...` directives; the table above mirrors it for readability.

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

The scoring phase runs in a separate plain-Docker container with
`--network=none`. The dnsmasq sidecar is not part of the scoring
pass — there is no agent to observe.

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
policy boundary is at the network layer (dnsmasq + iptables + ipset
egress allowlist), not at the in-process tool config. Disk writes
and syscalls inside the unprivileged container are not separately
gated — the container is ephemeral and per-run, with read-only
mounts for shared data.

Indirect egress paths (e.g., an agent-authored Python script calling
`urllib.request.urlopen`) hit the same kernel-level allowlist as
opencode's `webfetch` and are dropped if the destination isn't in
the ipset. They are also visible in the per-run `dns.log` as a
record.

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
- The egress boundary is opencode's self-enforced tool allowlist
  (soft) with a dnsmasq DNS log as the passive observation layer.
  Indirect egress paths (e.g., an agent-authored `./run.sh` calling
  `urllib`) are not blocked in this design; they are observable in
  the DNS log and reported as `dns_unexpected_hosts`. The validity
  argument depends on the pilot showing these signals stay clean.
  Hard-layer filtering can be added later if observation reveals
  meaningful leaks.
- Several methodological choices are still open and will affect
  what the experiment can claim. See §Open methodology questions
  below.

## Open methodology questions

Decisions still open at the time of writing. None are blockers but
each shapes what the experiment can claim.

1. **Replace the temporal Spearman metrics with predicted-vs-observed
   correctness.** The current `consistency.growth_temp_spearman` and
   `consistency.growth_precip_spearman` correlate Δh against climate
   proxies within each cell across years, then average across cells.
   Stage-2 confirmed they are noise on the synthetic dataset by
   design: within-cell year-over-year variance is tiny (±0.4 K T,
   ±40 mm/yr P) while the cross-cell spatial gradient is huge
   (30 K T span, 0–800 mm/yr P span). The replacement: compute the
   spec's predicted Δh per (cell, year) from the netCDF and compare
   against the agent's observed Δh — Pearson `r²` and OLS slope.
   Perfect spec-faithful: r² > 0.95, slope ≈ 1.0. Random/constant
   growth: r² ≈ 0. Right structure / wrong constants: 0.5–0.9, slope
   ≠ 1. Also add a year-10 spatial-map `r²` between observed and
   predicted height fields.

2. **Should `target_conformance=False` runs count toward the
   denominator of any headline metric?** A run that produces a valid
   CSV without using the named framework is technically "did the
   task" but doesn't measure what we're trying to measure. Likely:
   report pass rates *conditional* on conformance plus a separate
   conformance-rate-per-model figure.

3. **Acceptance ranges as a gate.** `height_year10` and
   `occupancy_year10` ranges in `harness/acceptance_ranges.json`
   currently pass scientifically-broken runs (e.g. mean tree height
   of 8 × 10⁻¹⁰ m falls vacuously inside `[0, 11]`). Three options
   to settle before the headline batch: (a) drop them and rely on
   the internal-consistency block, (b) tighten to growth-equation-
   consistent bounds, (c) replace with a derived `cell_passed` field
   ANDing `did_run`, `target_conformance`, growth-rate stats, and
   climate response. The phase-5a pilot favours (c).

4. **Is the recovery loop in scope for this paper?** Phase 5b
   delivers H2's measurement infrastructure but doubles the per-cell
   cost. Decide after the model-panel reintroduction pilots: if
   one-shot pass rates are already high enough to be informative
   across the panel, recovery is gravy; if one-shot is sparse,
   recovery is the only way to reach interpretable H2 numbers.

## Citation

If you use this harness or its outputs, please cite the USRSE'26
paper (TODO once accepted) and the Josh platform.

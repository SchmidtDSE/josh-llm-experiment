# Scoring — methodology, metrics, and open questions

How the harness decides what a completed agent run is "worth", what
each scorer field means, and the open scoring choices we plan to
revisit. The companion docs are
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) (hypothesis + run
flow), [K8S_REFACTOR.md](K8S_REFACTOR.md) (the in-flight refactor that
produced this scorer shape), and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
(historical engineering record).

> **Status (2026-05-20, schema `phase6-v1`).** The scorer gates on
> schema, fits a per-cell `observed ~ predicted` regression against
> the spec's deterministic prediction (the headline ecology gate),
> captures real wall-clock under a 100-replicate × 100-year `./run.sh`
> contract, and reports style metrics on the generated code. The
> internal-consistency block (negative-growth fraction, age-step ≠ 1,
> etc.) that earlier schemas carried is gone — diagnostic during
> methodology-building, redundant once cells consistently clear
> regression at β≈1. The acceptance bands are derived from a
> spec-faithful Python reference simulator
> ([data/reference_sim.py](data/reference_sim.py)) run against the
> committed synthetic climate netCDFs, not hand-picked.

## Scoring axes

Three axes evaluated against the workspace the agent container left
behind after the 8-step multi-invocation flow:

**1. Target conformance.** Did the agent use the named framework, or
sidestep it? Mechanical greps for `import mesa` / Mesa-class
subclassing on Mesa runs; `josh validate` exit zero on `.josh` files
for Josh runs. Catches the "agent produces a valid CSV via a plain-
Python fallback instead of using Josh" loophole.

**2. Spec-parameter conformance (ecology).** Did the agent's
year-100 outputs reproduce the spec's deterministic prediction under
a linear-regression fit?

For each `(cell, replicate)` pair in the agent's CSV, the scorer
computes a deterministic *predicted* total growth from the spec
equation using the agent's own reported climate values:

```
predicted(cell, rep) = Σ_{y = year_1 .. year_target}  Δh_max · %_T(T(y)) · %_P(P(y))
```

where `%_T` and `%_P` are the parabolic and logistic response curves
from [BASE_PROMPT.md §Growth Model](prompts/BASE_PROMPT.md). The
*observed* is the meanHeight at year == `target_year`. An OLS fit
`observed ~ predicted` yields slope β, intercept α, and R². Under a
faithful implementation:

- **β → 1.0** (modulo the O ~ N(1, 0.05) noise term, which has
  mean 1 → no bias)
- **α → 0.0**
- **R² → 1.0** (the spec equation is deterministic; only stochastic
  noise causes residuals)

The acceptance gate is **β ∈ [0.95, 1.05]**, **α ∈ [−0.5, 0.5] m**,
**R² > 0.95**. These tolerances are loose enough to absorb
implementation differences (grid choice, climate interpolation, RNG
seed) but tight enough to catch systematic dynamics errors like
"agent used Δh_max=0.5" (β=0.5) or "agent missed the temperature
clamp" (α drifts, R² drops). The reference's own fit values
(β=1.000013, α=−0.000249, R²=0.999993, n=155 000) are persisted in
`acceptance_ranges.json` so the gate is calibrated against the
empirical noise floor of the reference simulator.

A coarse secondary check — mean meanHeight at year 100 within a 3σ
band derived from the same reference run — is also reported
(`height_in_range`) to catch totally-broken outputs that happen to
have β=1 by accident (e.g., constant zeros). Used as a sanity check,
not the headline gate. The full reference distribution from which
the band is derived is published in `harness/acceptance_ranges.json`
under `_reference_observed`.

The chain producing the bands is:

```
data/generate_synthetic_climate.py   # writes the netCDFs
    ↓
data/reference_sim.py                # runs spec dynamics in numpy,
                                     # writes harness/acceptance_ranges.json
    ↓
reference/regenerate_fixtures.py     # samples one replicate for the
                                     # smoke-test golden fixtures
    ↓
docker build --target scorer         # bakes acceptance_ranges.json
                                     # into fortree:scorer
```

All four steps are deterministic. The math primitives live in
[`harness/spec_model.py`](harness/spec_model.py) and are shared
between the reference simulator and the scorer's validator, so
band derivation and gate enforcement cannot drift.

**3. Style / code metrics.** Source LOC, comment LOC, imports LOC,
and token-level Shannon entropy of the generated code. These are
the conciseness-of-implementation signals — directly relevant to
the H1 hypothesis (DSL targets should produce less scaffolding code
than general-framework targets). Reported even when the cell fails
to run, since code-shape doesn't depend on execution success.

The **schema gate** sits underneath the ecology axis as a
precondition: without `csv_schema_ok=true` the ecology metrics are
undefined and recorded as null with `height_in_range=false` /
`occupancy_in_range=false`.

### What changed vs the previous (phase5a) scorer

| Axis | phase5a-v1 | phase6-v1 |
|---|---|---|
| Target conformance | mechanical + fuzzy placeholder | unchanged (fuzzy gets a real implementation in PR2) |
| Schema gate | as below | unchanged |
| Internal consistency | Δh sign, Δh ceiling, age step, nTrees change, climate Spearmans — `consistency.*` fields | **dropped entirely** — diagnostic during methodology-building, redundant once regression β catches the same failure modes |
| Spec-parameter conformance | mean-band on `height_year10` / `occupancy_year10` over an 11-year, single-replicate run | **regression band** β/α/R² on `observed ~ predicted` over a 100-year, 100-replicate run; mean-band kept as a secondary sanity check |
| Acceptance bands | hand-picked from prior pilot data | **derived empirically** from a spec-faithful Python reference simulator against the committed synthetic climate |
| Wall clock | reported, not used | **headline metric** — large enough under 100×100 to be meaningful for Josh-vs-Mesa execution-cost comparisons |
| LOC + entropy | unchanged | unchanged |

## Metrics

All scoring is mechanical at experiment time. The current scorer
JSON schema is `phase6-v1` (`harness/run_metrics.py:SCHEMA_VERSION`).

| Metric | Type | Source |
| --- | --- | --- |
| `target_conformance` | bool | Mechanical: Mesa imports + class subclassing, or `josh validate` exit zero. |
| `conformance.{imports_mesa, subclasses_model, has_josh_files, has_jshd_files, josh_validate_exit_code}` | mixed | Per-target evidence fields backing the `target_conformance` rollup. |
| `target_conformance_fuzzy` | enum | LLM-judge variant — see §LLM-judge passes below. |
| `csv_exists` | bool | `./output/results.csv` was written. |
| `csv_schema_ok` | bool | Subset-match required columns + target year present + required cols numeric-coercible. |
| `csv_schema_errors` | list | Specific failure messages when `csv_schema_ok=false`. |
| `csv_row_count` | int | Total rows in the CSV (pre-filter). |
| `csv_rows_dropped_nan` | int | Rows dropped during NaN-filtering on required numeric cols. |
| `script_was_executable` | bool | `True` if the agent self-chmod'd; `False` if the scorer's runner had to. |
| `did_run` | bool | `exit_code == 0 AND csv_exists AND csv_schema_ok`. |
| `exit_code` | int | `./run.sh` exit status. |
| `wall_time_seconds` | float | End-to-end wall-clock for the agent's `./run.sh`. Under the phase-6 contract `run.sh` carries preprocess + `--replicates 100` × 100 simulated years, so this is the real Josh-vs-Mesa execution-cost comparison. |
| `timed_out` | bool | True when `./run.sh` was killed by the scorer's per-invocation timeout. |
| `height_year100_mean` | float | Mean of `meanHeight` across all `(cell, replicate)` rows at year == `target_year`. Secondary sanity check. |
| `occupancy_year100_mean` | float | Mean of `nTrees` at year == `target_year`. |
| `height_in_range` | bool | `height_year100_mean` within the 3σ mean band (secondary check). |
| `occupancy_in_range` | bool | Same for occupancy. |
| `regression_fit.beta` | float | Slope of OLS `observed ~ predicted`. Spec-faithful target: 1.0. |
| `regression_fit.alpha` | float | Intercept of the same fit. Spec-faithful target: 0.0. |
| `regression_fit.r2` | float | R² of the same fit. Spec-faithful target: ~1.0. |
| `regression_fit.n_observations` | int | Number of `(cell, replicate)` pairs the fit was over. |
| `regression_fit_ok` | bool | **Headline ecology gate.** True iff β, α, R² all inside the bands in `acceptance_ranges.json`. |
| `regression_fit_reasons` | list[str] | Specific failure messages when `regression_fit_ok=false`. |
| `acceptance_ranges_used` | dict | The full `acceptance_ranges.json` as parsed — frozen-evidence record including the reference's own observed β/α/R² so a re-score is self-describing. |
| `src_loc`, `comment_loc`, `imports_loc` | int | Lines of generated code per category. `.jshd` (binary preprocessed data) is excluded. |
| `entropy_bits` | float | Token-level Shannon entropy of the generated code via `tiktoken` `cl100k_base`. |

**Multi-invocation diagnostics** (`steps[*].exit_code`,
`plan_todos.checked/.total`) are produced per-cell by the orchestrator
and surfaced alongside the scoring metrics, so the model's in-cell
self-correction is inspectable.

## The `./run.sh` contract

A cell's `./run.sh` is the unit of work the scorer executes — and
under phase-6, it is also the unit of work whose wall-clock counts
as the headline cost metric. The prompt (see
[BASE_PROMPT.md](prompts/BASE_PROMPT.md) and the per-target
directives, updated in PR2 of [K8S_REFACTOR.md](K8S_REFACTOR.md))
instructs the agent to produce a `run.sh` that:

1. Performs any preprocessing the chosen target needs (Josh's
   `.jshd` binary preprocessing, Mesa's netCDF-to-Pandas
   conversion, etc.) **inside** `run.sh`.
2. Invokes the simulation with **`--replicates 100`** (or the
   framework equivalent — for Mesa, a 100-iteration loop emitting
   the same CSV schema) over **100 simulated years** starting at
   year 2024 (target year 2123).
3. Writes `output/results.csv` with one row per `(cell, year,
   replicate)` tuple — schema requirements unchanged from phase5a.

The fuzzy judge gets a new Q3 (PR2) asking whether `run.sh` honours
this contract. The wall-clock comparison is only apples-to-apples
when Q3 = `yes`; cells where Q3 = `no` or `partial` are reported
but flagged in analysis.

## LLM-judge passes (post-hoc)

Three free-form questions answered by Claude against each completed
cell's workspace + transcript. Purpose: human-organisation and
contract verification, not headline scoring. The mechanical axes above
remain the experimental yardstick.

**Q1 — "Did it use the right tool?"** Looks at the generated source
files plus the agent's transcript; answers `yes` / `no` / `partial`
with one sentence of justification. Catches the failure mode where
the mechanical conformance check passes (e.g. the workspace contains
`import mesa` somewhere) but the meat of the implementation is in
plain numpy.

**Q2 — "Where did the agent get confused or devote its reasoning?"**
Reads the transcript and identifies 1–3 specific steps, files, or
errors where the agent spent disproportionate effort. Free-text,
~2–4 sentences. Surfaces cross-cell failure patterns.

**Q3 — "Does `./run.sh` carry preprocess + `--replicates 100` × 100
years?"** Looks at `./run.sh` and (if needed) the source files it
invokes; answers `yes` / `no` / `partial` with one sentence. This
is the apples-to-apples verification for the wall-clock metric —
see §The `./run.sh` contract above. Added in phase-6 (PR2 of the
k8s refactor).

### Spec

| Field | Value |
| --- | --- |
| Input | `workspace/` (source files), `transcript.md` (rendered opencode transcript), `scorer.json` (mechanical outcome) |
| Output file | `<run-id>/scorer.fuzzy.json` |
| Judge model | `anthropic/claude-opus-4.7` (same `claude` short name we use elsewhere) |
| Schema | `{"q1": {...}, "q2": {...}, "q3": {...}, "judge_model_id": "...", "schema_version": "fuzzy-v2"}` |
| When run | Post-hoc — never gates the cell from completing |
| Cost | ~$0.05–0.20 per cell |

### Execution path

The judge runs against the cell's preserved `workspace/` after the
batch completes. Under the k8s refactor this is a separate Job (one
per cell, or one for the whole batch as an Indexed Job) that pulls
the workspace from the bucket, runs opencode in read-only mode
against it, and writes `scorer.fuzzy.json` back to the bucket. See
[K8S_REFACTOR.md §2](K8S_REFACTOR.md) for the full Job shape;
implementation lands in PR5.

## Re-analysing completed runs

The scoring container is target-agnostic and stateless against an
agent workspace, so re-scoring against any completed run's
`workspace/` is one container invocation. Under the k8s refactor this
becomes a re-score Job that pulls from the bucket, re-runs the scorer
container, and writes a sibling `scorer.rescored.json` next to the
original.

The local recipe (for development against an already-downloaded run
dir) remains:

```sh
docker run --rm --network=none \
  -v runs/<batch-tag>/<run-id>/workspace:/sandbox \
  -v $(pwd)/data:/sandbox/data:ro \
  fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa> \
  > runs/<batch-tag>/<run-id>/scorer.rescored.json
```

**Invariants the scorer relies on.** As long as a cell's
`workspace/` is preserved (the agent's source files + their
`./output/results.csv`) and the synthetic-climate netCDFs in `data/`
are unchanged, scoring is fully reproducible. The `fortree:scorer`
image pins all the harness Python / Java / Josh versions.

**Provenance.** Persist re-scored output to a distinct filename
(`scorer.rescored.json`) rather than overwriting the original. The
original is the headline-batch evidence; the rescore is a methodology
delta.

## Open methodology questions

1. **Regression band tolerance calibration.** The β/α/R² tolerances
   in [`harness/acceptance_ranges.json`](harness/acceptance_ranges.json)
   (β ∈ [0.95, 1.05], α ∈ [−0.5, 0.5], R² > 0.95) are hand-picked
   to give implementation tolerance while catching gross dynamics
   errors. They are not derived from observed agent-implementation
   variance — we don't yet have a panel of agent runs under the
   phase-6 design to estimate that. Once the headline batch produces
   data, the tolerances can be re-calibrated against the empirical
   spread of "clearly faithful" runs vs "clearly broken" runs. The
   regression *targets* (β=1, α=0, R²→1) are derived from the
   reference simulator and are not in question.

2. **target_conformance=False in the denominator.** Does a run that
   produces a valid CSV without using the named framework count
   toward H1's denominator? Likely answer: report pass-rate conditional
   on conformance, plus a separate conformance-rate-per-model figure.
   Decision before paper writing; no code change needed, just a
   reporting convention.

3. **Fuzzy-judge same-model-judges-itself.** Q1–Q3 see the same
   `claude-opus-4.7` we score under, so any cell where claude is the
   agent has a same-model self-judging risk. Mitigation: report
   answers cross-tabulated by `(model, judge)` so the bias is visible
   in the data; consider a second judge family only if the first-pass
   results look suspicious.

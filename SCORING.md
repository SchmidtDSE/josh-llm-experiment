# Scoring — methodology, metrics, and open questions

How the harness decides what a completed agent run is "worth", what
each scorer field means, and the open scoring choices we plan to
revisit. The companion docs are
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) (hypothesis + run
flow), [K8S_REFACTOR.md](K8S_REFACTOR.md) (the in-flight refactor that
produced this scorer shape), and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
(historical engineering record).

> **Status (2026-05-20, schema `phase6-v1`).** The scorer is now
> deliberately narrow: it gates on schema, measures the ecology
> outcome at year 100, captures real wall-clock under a 100-replicate
> × 100-year `./run.sh` contract, and reports style metrics on the
> generated code. The internal-consistency block (negative-growth
> fraction, age-step ≠ 1, etc.) that earlier schemas carried is gone
> — the panel-batch evidence showed those checks were diagnostic during
> methodology-building but redundant once cells consistently clear
> them. The acceptance-band thresholds are *intentionally loose* (see
> Open Q #1) pending a reference-distribution recalibration.

## Scoring axes

Three axes evaluated against the workspace the agent container left
behind after the 8-step multi-invocation flow:

**1. Target conformance.** Did the agent use the named framework, or
sidestep it? Mechanical greps for `import mesa` / Mesa-class
subclassing on Mesa runs; `josh validate` exit zero on `.josh` files
for Josh runs. Catches the "agent produces a valid CSV via a plain-
Python fallback instead of using Josh" loophole.

**2. Spec-parameter conformance (ecology).** Did mean tree height
and mean occupancy at year 100 fall in the pre-registered acceptance
ranges (`height_year100` and `occupancy_year100` in
[`harness/acceptance_ranges.json`](harness/acceptance_ranges.json))?
The master prompt specifies the relevant parameters (10 trees/patch,
Δh_max = 1 m/yr, 100-year simulation length); these ranges encode
what a faithful run should produce. The methodology of these ranges
is under review — see Open Q #1.

The ecology measurement is computed across all `(cell, replicate)`
pairs at year == `target_year`. With the new `./run.sh` contract
(100 replicates × 100 simulated years, both inside the agent's own
`run.sh`), this is a large enough sample to make the year-100 mean
statistically meaningful per cell.

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
| Internal consistency | Δh sign, Δh ceiling, age step, nTrees change, climate Spearmans — `consistency.*` fields | **dropped entirely** — diagnostic during methodology-building, redundant in steady state |
| Spec-parameter conformance | `height_year10` / `occupancy_year10` over an 11-year, single-replicate run | `height_year100` / `occupancy_year100` over a 100-year, 100-replicate run |
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
| `height_year100_mean` | float | Mean of `meanHeight` across all `(cell, replicate)` rows at year == `target_year`. |
| `occupancy_year100_mean` | float | Mean of `nTrees` at year == `target_year`. |
| `height_in_range` | bool | `height_year100_mean` within `acceptance_ranges.json` bounds. |
| `occupancy_in_range` | bool | Same for occupancy. |
| `acceptance_ranges_used` | dict | The full `acceptance_ranges.json` as parsed — frozen-evidence record so a re-score using different bands stays self-describing. |
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

1. **Year-100 acceptance bands.** The bands in
   [`harness/acceptance_ranges.json`](harness/acceptance_ranges.json)
   are *placeholders* — `height_year100 ∈ [0, 100] m` is loose enough
   that any non-collapsed run passes; `occupancy_year100 ∈ [9.9, 10.1]`
   already reflects the spec (no death / reproduction). The plan is to
   derive tighter bands either (a) empirically from a known-good Josh
   reference cell at 100×100, or (b) analytically from the growth
   equation + synthetic-climate gradient. (b) is preferred for the
   "pre-registered" claim. Tracked as Open Q #1 in
   [K8S_REFACTOR.md](K8S_REFACTOR.md).

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

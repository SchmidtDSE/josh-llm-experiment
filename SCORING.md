# Scoring — methodology, metrics, and open questions

How the harness decides what a completed agent run is "worth", what
each scorer field means, and the open scoring choices we plan to
revisit before paper writing. The companion docs are
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) (hypothesis + run
flow) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
(engineering state).

> **Status (2026-05-20).** Scoring methodology is held intentionally
> permissive while the headline batch runs. The acceptance-range
> gate is known to pass scientifically-broken runs (see Open Q #1),
> the conformance-denominator question is unresolved (Open Q #2),
> and the consistency block still carries the noisy temporal
> Spearmans pending replacement (Open Q #3). The scoring container
> is fully re-runnable against any completed run's `workspace/`, so
> we are deliberately freezing the run dirs first and revisiting
> scoring choices afterwards.

## Scoring axes

Four orthogonal axes, all evaluated against the workspace the agent
container left behind after the 8-step multi-invocation flow:

**1. Target conformance.** Did the agent use the named framework, or
sidestep it? Mechanical greps for `import mesa` / Mesa-class
subclassing on Mesa runs; `josh validate` exit zero on `.josh` files
for Josh runs. Caught the "agent produces a valid CSV via a Python
fallback instead of using Josh" loophole in the phase-5a pilot batch.

**2. Schema gate.** Did the agent's `./run.sh` produce
`./output/results.csv` and does the CSV carry the required columns?
The gate is subset-match (extras OK, order irrelevant), NaN-tolerant
(rows with NaN in required numeric cols are filtered and counted, not
failed), and demands the acceptance target year is present.
Cell-identity is permissive: either a `cell_id` string column **or**
the pair `position.x` + `position.y` — `load_clean_results`
synthesises `cell_id` from the position pair when only the alt is
present, so internal-consistency code grouping by `cell_id` works
either way. The scorer self-heals `chmod +x run.sh` so the agent's
failure to chmod is captured separately (`script_was_executable`)
without gating the measurement.

**3. Internal consistency.** Given the agent's own outputs, does the
simulation behave self-consistently with respect to the spec's
constraints? Hard structural checks (negative growth fraction,
growth-rate ceiling fraction, age-step ≠ 1 fraction, nTrees-change
fraction). All four should be 0 under any faithful implementation;
non-zero values are direct spec violations the other gates can't see.
The within-cell-across-years climate-response Spearmans are noise on
the synthetic dataset (low temporal variance by design) and scheduled
for replacement — see Open Q #3.

**4. Spec-parameter conformance.** Did mean tree height and mean
occupancy at year 10 fall in the pre-registered acceptance ranges
(`height_year10` and `occupancy_year10` in
[`harness/acceptance_ranges.json`](harness/acceptance_ranges.json))?
The master prompt specifies the relevant parameters (10 trees/patch,
Δh_max = 1 m/yr, etc.) so these ranges are meaningful for every cell
in the headline panel. The methodology of these ranges is under
review — see Open Q #1.

## Metrics

All scoring is mechanical at experiment time. Manual review of the
archived artifacts can supplement post-hoc. Per-batch rollups are
generated automatically by `orchestration/generate_batch_report.py`
into `runs/<batch-tag>/batch_report.md`.

The current scorer JSON schema is `phase5a-v1`
(`harness/run_metrics.py:SCHEMA_VERSION`).

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
| `consistency.growth_rate_{min,max,mean}_m` | float | Descriptive stats on Δh across all `(cell, year→year+1)` transitions. |
| `consistency.growth_rate_negative_frac` | float | Fraction of transitions with Δh < 0. Spec value: 0. |
| `consistency.growth_rate_above_ceiling_frac` | float | Fraction with Δh > 1.15 m/yr (Δh_max × (1+3σ)). Spec value: 0. |
| `consistency.age_step_{mean, off_one_frac}` | float | Mean age increment and fraction != 1.0. |
| `consistency.ntrees_change_frac` | float | Fraction of transitions where `nTrees` changed. Spec value: 0. |
| `consistency.growth_temp_spearman` | float | Per-cell-across-years; **scheduled for replacement** (Open Q #3). |
| `consistency.growth_precip_spearman` | float | Same; same caveat. |
| `height_year10_mean` | float | Mean of `meanHeight` at target year. |
| `occupancy_year10_mean` | float | Mean of `nTrees` at target year. |
| `height_in_range` | bool | `height_year10_mean` within `acceptance_ranges.json` bounds. |
| `occupancy_in_range` | bool | Same for occupancy. |
| `prompt_tokens`, `completion_tokens` | int | OpenRouter response. |
| `src_loc`, `comment_loc`, `imports_loc` | int | Lines of generated code per category. `.jshd` (binary preprocessed data) is excluded. |
| `entropy_bits` | float | Token-level Shannon entropy of the generated code via `tiktoken` `cl100k_base`. |
| `wall_time_seconds` | float | End-to-end time per phase. Reported, not used for scoring (provider latency confounds). |

**Multi-invocation diagnostics** (`steps[*].exit_code`,
`plan_todos.checked/.total`) are produced per-cell by `launch_batch.py`
and surfaced in `batch_report.md` alongside the scoring metrics, so
the model's in-cell self-correction is inspectable.

## LLM-judge passes (post-hoc, for our convenience)

Two free-form questions answered by Claude against each completed
cell's workspace + transcript. Purpose: human-organisation, not
headline scoring. The mechanical axes above remain the experimental
yardstick. The judge answers are persisted alongside the mechanical
scoring so a reviewer can scan an entire batch's questions in one
place.

**Q1 — "Did it use the right tool?"** Asks the judge to look at the
generated source files plus the agent's transcript and answer
`yes` / `no` / `partial` with one sentence of justification. Catches
the failure mode where the mechanical conformance check passes (e.g.
the workspace contains `import mesa` somewhere) but the meat of the
implementation is in plain numpy — or the converse, where `josh
validate` passes on a near-empty `.josh` and the model did the real
work in a sidecar Python script.

**Q2 — "Where did the agent get confused or devote its reasoning?"**
Asks the judge to read the transcript and identify 1–3 specific steps,
files, or errors where the agent spent disproportionate effort or
appeared lost. Free-text answer, ~2–4 sentences. Surfaces patterns
across cells (e.g. "every Josh cell got stuck on `.jshd` preprocessing
syntax") that the mechanical metrics don't catch.

### Spec

| Field | Value |
| --- | --- |
| Input | `workspace/` (source files), `transcript.md` (rendered opencode transcript), `scorer.json` (mechanical outcome) |
| Output file | `runs/<batch>/<run-id>/scorer.fuzzy.json` |
| Judge model | `anthropic/claude-opus-4.7` (same `claude` short name we use elsewhere — keeps the credentials path uniform) |
| Schema | `{"q1": {"answer": "yes/no/partial", "justification": "..."}, "q2": {"observations": "..."}, "judge_model_id": "...", "schema_version": "fuzzy-v1"}` |
| When run | Post-hoc — never gates the cell from completing; the operator invokes a sweep across a completed batch dir |
| Cost | ~$0.05–0.20 per cell (rough estimate; transcripts run ~10–40k input tokens) |

### Implementation status

Not yet implemented. The skeleton lives in
[`harness/conformance_fuzzy.py`](harness/conformance_fuzzy.py) (stub).
Plan when picked up:

- `harness/conformance_fuzzy.py` — `judge_cell(run_dir, judge_model_id)`
  reads workspace + transcript, builds a single prompt, posts to
  OpenRouter, writes `scorer.fuzzy.json`. Idempotent: if
  `scorer.fuzzy.json` already exists and `schema_version` matches,
  skip.
- `orchestration/run_fuzzy_judge.sh` (new) — walks a batch dir, runs
  the judge per cell, accumulates a `fuzzy_summary.md` rolling up Q1
  yes/no/partial counts and Q2 cross-cell themes.
- `batch_report.md` gains a `fuzzy_q1` column (yes/no/partial glyph
  per cell) when fuzzy results are present; absent otherwise.

The judge sees the SAME `claude-opus-4.7` we score under, so there's
a same-model-judges-itself caveat for any cell where claude is the
agent. Mitigation: report Q1/Q2 cross-tabulated by `(model, judge)`
so the bias is visible in the data; consider a second judge
(`anthropic/claude-haiku` or `openai/gpt-5`) only if the first-pass
results look suspicious. This stays a convenience artefact, not a
headline metric.

## Re-analysing completed runs

Scoring methodology may evolve after the headline batch (Open Q #1
acceptance-range fix, Open Q #3 r² metric, etc.). The scoring
container is target-agnostic and stateless against an agent
workspace, so re-scoring against any completed run's `workspace/` is
a one-liner. The recipe:

```sh
# 1. Make sure the workspace is local. For an archived batch:
mc mirror s3://<bucket>/<prefix>/<batch-tag>/ runs/<batch-tag>/

# 2. Re-score one cell against its preserved workspace:
docker run --rm --network=none \
  -v runs/<batch-tag>/<run-id>/workspace:/sandbox \
  -v $(pwd)/data:/sandbox/data:ro \
  fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa> \
  > runs/<batch-tag>/<run-id>/scorer.rescored.json

# 3. To re-score the whole batch, loop and regenerate the manifest +
#    batch_report afterwards. A wrapper script
#    orchestration/rescore_batch.sh is on the to-author list once
#    we have a concrete methodology revision in hand.
```

**Invariants the scorer relies on.** As long as a cell's
`runs/<batch>/<run-id>/workspace/` is preserved (the agent's source
files + their `./output/results.csv`) and the synthetic-climate netCDFs
in `data/` are unchanged, scoring is fully reproducible. The
`fortree:scorer` image pins all the harness Python / Java / Josh
versions, so a re-score N months later sees byte-identical
infrastructure — verified in this PR by rerunning the scorer against
a phase-5c panel cell and seeing the same metrics modulo the
intentional stochastic-Gaussian term inside `./run.sh` for Mesa runs.

**Provenance.** Persist re-scored output to a distinct filename
(`scorer.rescored.json`, or include the methodology revision in the
name like `scorer.phase5b-v1.json`) rather than overwriting the
original. The original is the headline-batch evidence; the rescore
is a methodology delta. Same logic for `batch_report.md` — write
`batch_report.rescored.md` so both views live side by side.

## Open methodology questions

These are explicitly deferred — the headline batch runs against the
permissive scorer above so the question of which gate is "right" can
be settled after we see the data, against frozen workspaces.

1. **Acceptance ranges as a gate.** `height_year10` and
   `occupancy_year10` ranges in `harness/acceptance_ranges.json`
   currently pass scientifically-broken runs (e.g. mean tree height
   of 8 × 10⁻¹⁰ m falls vacuously inside `[0, 11]`). Three options to
   settle post-headline: (a) drop them and rely on the
   internal-consistency block, (b) tighten to growth-equation-
   consistent bounds, (c) replace with a derived `cell_passed` field
   ANDing `did_run`, `target_conformance`, growth-rate stats, and
   climate response. The phase-5a pilot favoured (c); re-analysis
   path means we can implement and re-score completed runs without
   re-running the agents.

2. **target_conformance=False in the denominator.** Does a run that
   produces a valid CSV without using the named framework count
   toward H1's denominator? Technically it "did the task" but didn't
   measure what we're trying to measure. Likely answer: report
   pass-rate conditional on conformance, plus a separate
   conformance-rate-per-model figure. Decision before paper writing;
   no code change needed, just a reporting convention.

3. **Predicted-vs-observed Δh r² metric.** The current
   `consistency.growth_temp_spearman` and
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
   ≠ 1. Also a year-10 spatial-map `r²` between observed and
   predicted height fields. Implementable as a scorer addition;
   re-scoreable on completed runs.

4. **LLM-judge methodology refinement.** Open whether the two-question
   judge above is the right shape, whether we need a separate judge
   model to avoid the same-model self-judging concern, and whether
   to add a third question (e.g. "estimate the time-to-correct-output
   from the transcript's first failed run.sh"). Adjustable post-hoc
   without re-running the agents.

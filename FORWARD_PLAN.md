# Forward plan — post phase-5a

Captures where the experiment harness stands after the
`chore/climate-conversion-fix` work, what remains open, and the staged
path to a defensible headline run. Supersedes the May-18 `DEBUG_PLAN.md`
(retired — every concern in it is resolved or no longer relevant).

## TL;DR

- The scoring infrastructure is solid end-to-end: schema gate, target
  conformance, internal-consistency metrics, batch-level rollup,
  chmod self-heal, kernel-level egress allowlist.
- The remaining blocker for a meaningful experiment is the **input
  climate data**: the Cal-Adapt netCDF's units are mislabeled and the
  spec's `P_low=300, P_high=500` defaults are wrong for Tulare anyway.
  Both produce uninterpretable runs.
- This document specifies a **synthetic-climate dataset** that fixes
  both issues and is fully reproducible from a single script under the
  pinned image. It exercises every part of the growth equation.
- After the climate swap, the staged sequence is: validate against the
  same 4-cell mini-batch, then reintroduce the model panel one model at
  a time, then run the headline 60-cell sweep.

## What's solid now (do not touch)

| Component | Where | Validated by |
|---|---|---|
| Scorer JSON schema (`phase5a-v1`) — `target_conformance`, `consistency`, `csv_rows_dropped_nan`, `script_was_executable` | `harness/run_metrics.py`, `harness/internal_consistency.py`, `harness/conformance.py` | 4-cell mini-batch (`runs/batch-claude-r5-both-n4-20260519T1655Z/`) — every field populates as designed; `growth_precip_spearman` discriminated 0.98 vs 0.02 between spec-faithful and non-conformant mesa runs. |
| Schema gate is subset-match, NaN-tolerant | `harness/validators/output_schema.py` | Re-scoring previously-rejected Josh CSVs from the prior pilot — they now pass schema; consistency metrics surface their behaviour. |
| Bash actually surfaces to the model | `config/opencode.template.json` (`"bash": "allow"` string form, not per-pattern object) | 4/4 cells in the mini-batch made bash calls; `x_bit=True` everywhere. |
| Scorer self-heals chmod | `harness/runner.py` | Mini-batch shows `script_was_executable=True` across all 4 cells (claude self-chmod'd), but the self-heal would have rescued any cell where it didn't. |
| Batch-level markdown rollup with drill-down links | `orchestration/generate_batch_report.py` (auto-invoked by `launch_batch.py`) | Mini-batch report renders the at-a-glance matrix, per-cell table with `x_bit` column, failure-mode tally, and "passing cells" / "target-conforming but not passing" callouts. |
| `task` subagent tool disabled | `config/opencode.template.json` | Gemma no longer falls into the 800-malformed-call loop we saw in the overnight batch. |
| Hard egress allowlist | `Dockerfile.dnsmasq`, `orchestration/sidecar-init.sh` | Smoke-test firewall probe passes; agents reach docs hosts and OpenRouter but nothing else. |

## What's pending (in priority order)

### 1. Climate dataset swap — synthetic data

The current Cal-Adapt files have two compounding issues that make any
H1/H2 measurement uninterpretable:

- `precip_tulare_annual.nc` reports `units = kg m⁻² s⁻¹` but the values
  are a sum of daily rates (per Lucia's pipeline). The correct
  conversion is `× 86_400`, not `× 31_536_000`. SIDECAR now teaches
  this explicitly, but the underlying data is still mislabeled.
- With the corrected conversion, Tulare precipitation lands in 1–75
  mm/year. The spec's defaults `P_low=300, P_high=500` mean every cell
  is below threshold and growth is essentially zero. Spec-faithful
  models produce mean heights of 8e-10 m and pass `height_in_range=[0,11]`
  vacuously. We've validated this is a real signal (`sp_P=0.98` proves
  the model responds correctly to the climate it sees) but it's not a
  useful experimental probe of LLM modeling quality.

**Decision: switch to a synthetic dataset designed for the spec.**

The dataset is committed to the repo as two netCDFs plus the generator
script that produces them:

- `data/maxtemp_synthetic.nc` — annual maximum temperature, K, shape
  `(31, 31, 50)`. Linear south-to-north gradient 315K → 285K plus +0.15
  K/year warming trend plus ±0.4K noise.
- `data/precip_synthetic.nc` — precipitation in raw `kg m⁻² s⁻¹` units
  matching the prior file's convention (multiply by 86_400 for mm/year).
  Linear west-to-east gradient ~700 → ~100 mm/year plus ±40 mm/year
  interannual noise.
- `data/generate_synthetic_climate.py` — deterministic generator. Two
  invocations produce byte-identical netCDFs (sha256 match verified).
  Re-run via:

  ```sh
  docker run --rm -v $(pwd):/repo -w /repo fortree:agent \
    python data/generate_synthetic_climate.py
  ```

- `data/validate_synthetic_climate.py` — companion validator. Asserts
  the committed netCDFs match the generator's contract: sha256
  snapshots, shapes, coordinate ranges, units, gradient directions
  (south warmer than north, west wetter than east, warming trend over
  years), no NaN cells, and a *spec-growth implication* check that
  confirms the gradient produces a non-degenerate Δh distribution
  under the spec's equation (some cells near 0, some near `Δh_max`,
  ≥ 15% in the active growth band).

  **CF-1.8 conformance** is checked via the IOOS
  [`compliance-checker`](https://github.com/ioos/compliance-checker) —
  the standard tool for CF/ACDD validation. The generator emits
  netCDFs with proper `Conventions`, coordinate `standard_name`,
  `axis`, `units`, `cell_methods`, and global metadata, so both files
  pass `cf:1.8` at criteria=`normal` with zero issues. This means the
  files would load cleanly in any CF-aware tool (QGIS, Panoply, cdo,
  ncks, xarray's `decode_cf=True`). `compliance-checker` is pinned in
  `config/requirements.txt` and baked into `fortree:agent`, so the CF
  check runs by default — no separate install step.

  Run:

  ```sh
  docker run --rm -v $(pwd):/repo -w /repo fortree:agent \
    python data/validate_synthetic_climate.py
  ```

  Exits non-zero on first failed assertion. If the generator changes
  intentionally, re-snap the sha256s at the top of the validator from
  the generator's fresh output.

**Why these specific gradients.** With the spec's growth equation
(`T_min=270, T_max=330, P_low=300, P_high=500, k=12, Δh_max=1.0`):

- Centre of the grid: T ≈ 300K (parabolic peak `%_T=1.0`), P ≈ 400
  mm/year (`%_P≈0.5`) → Δh ≈ 0.5 m/year.
- South-west corner: T ≈ 315K, P ≈ 700 mm/year → T too warm
  (`%_T≈0.56`) but P saturated (`%_P≈1.0`) → Δh ≈ 0.56 m/year.
- North-east corner: T ≈ 285K, P ≈ 100 mm/year → cold (`%_T≈0.75`)
  and dry (`%_P≈10⁻¹²`) → Δh ≈ 0.
- Predicted year-10 heights span roughly **[0, 10] m**, centred on a
  band of fast-growing mid-latitude / wet-west cells, with corners at
  zero.

This is visualizable — a heatmap of year-10 height across the grid
should show a clear ridge running diagonally NW→SE, brightest at
mid-latitude / western-edge, dim at the dry east and the cold north.
Agents whose implementations match the spec should produce visually
similar maps; deviations are interpretable as specific spec violations.

**No NaN cells. No edge masking.** All 1550 cells are valid.

**The SIDECAR will need a small update** to point at the new file
paths and to drop the Tulare-specific sanity-check paragraph (which
becomes misleading on the synthetic data). The `× 86_400` instruction
stays — the synthetic precip uses the same units convention so the
agent still converts the same way.

### 2. Acceptance-range methodology

The current `height_in_range=[0,11]` and `occupancy_in_range=[9.9,10.1]`
ranges pass scientifically broken runs (e.g. 8e-10 m heights). After
the climate swap they'll start carrying real signal again, but the
problem is structural: the ranges can't distinguish "model implemented
correctly" from "model did nothing and happened to land in the wide
interval". Three options:

- **A.** Drop them entirely. Use `consistency.*` fields as the ground
  truth. Simpler. Loses the "did the agent use the spec's default
  parameters" signal.
- **B.** Replace with growth-equation-consistent ranges. E.g. require
  `growth_rate_mean_m ∈ [0.05, 1.15]` and
  `|growth_precip_spearman| > 0.3`. Captures "model is genuinely
  responding to inputs" as a hard gate.
- **C.** Add a derived `cell_passed` field that ANDs `did_run`,
  `target_conformance`, a growth-rate band, and a climate-response
  threshold. Keeps the existing ranges as informational but the
  headline pass/fail comes from the derived field.

Recommend **C** — it's the most defensible methodologically (every
component is a separate axis the reader can inspect) and the smallest
change to the existing scorer.

Land this *after* the climate swap is validated, so the synthetic-data
mini-batch produces baseline numbers we can choose thresholds from
empirically.

### 3. Reintroduce the model panel one at a time

The overnight batch surfaced two model-specific issues that aren't
about the harness:

- **Mistral** (6/12 cells across both targets): 0 doc fetches, 0
  framework files, no engagement. Either the model variant
  (`mistralai/mistral-large-2411`) doesn't ship with usable tool-use
  behaviour for opencode 1.14.50, or our prompting isn't reaching it.
  Needs investigation before spending more API credit.
- **Kimi** (10/12 cells): often writes no framework files even on
  mesa. Pattern looks like the model bails out early without producing
  artifacts. Worth a single-cell experiment with `--print-logs`
  trajectory inspection to see what it's doing instead.

Suggested order:
1. Claude full panel (rungs 1+5, both targets, 3 replicates) — baseline.
2. Gemma4 full panel — already showed it can engage post task-disable.
3. Minimax full panel — also engaged on the overnight batch (target
   conformance on Mesa).
4. Kimi single-cell investigation, then full panel if behaviour clears
   up.
5. Mistral single-cell investigation; possibly drop if it can't engage.

### 4. Josh-specific gaps

Two patterns observed on the 4-cell mini-batch and earlier:

- One Josh cell (`24970aac`) produced a CSV missing `lat`/`lon` columns
  — claude's `exportFiles.patch` config in the `.josh` model omitted
  position. We catch this with the new schema validator (cleanly
  errored with the precise missing columns) but agents have to
  rediscover the contract per-run. Possibility: extend the target
  directive to list the required output columns explicitly. Punt for
  now unless it persists with the synthetic data.
- 0/30 Josh cells in the overnight batch produced `.jshd` preprocessing
  files. The one cell that managed both .josh AND .jshd in the
  4-cell run (`24970aac`) was claude rung=5 josh — so we know it's
  achievable. Whether other models can write valid `.jshd` is an
  experimental question, not a harness one. Leave as-is.

## Staged execution

### Stage 1 — Land the synthetic-climate swap (1 PR, no agent runs)

1. Commit the synthetic dataset + generator (`data/generate_synthetic_climate.py`,
   `data/maxtemp_synthetic.nc`, `data/precip_synthetic.nc`).
2. Update `prompts/SIDECAR.md`:
   - Point file paths at `data/maxtemp_synthetic.nc` and `data/precip_synthetic.nc`.
   - Drop the Tulare-specific sanity-check paragraph.
   - Keep the `× 86_400` conversion instruction (same units convention).
3. Decide on acceptance-range option **A / B / C** above and apply.
4. CI smoke green; no batch needed at this stage.

### Stage 2 — Validate against the 4-cell mini-batch

1. `experimental_cells.csv` → `claude × r5 × {mesa, josh} × 2 replicates`.
2. Launch:
   ```sh
   uv run orchestration/launch_batch.py --cells experimental_cells.csv --jobs 4 \
     --batch-tag synthetic-validate-$(date -u +%Y%m%dT%H%MZ)
   ```
3. Expected outcomes:
   - All 4 cells `script_was_executable=True` (bash exposure stays
     fixed across the data swap).
   - At least 1 mesa cell `target_conformance=True AND
     growth_rate_mean_m ∈ [0.1, 1.0] AND growth_precip_spearman > 0.5`.
     This is the strongest single signal that the synthetic data is
     producing a working experimental probe.
   - Year-10 mean heights distributed in roughly [0, 10] m across cells
     (per the calibration above).

### Stage 3 — Re-introduce the model panel

1. Gemma4 mini-batch (4 cells, same shape as Stage 2). Pass criterion:
   target_conformance=True on at least 1/4. Mesa likely; Josh stretch.
2. Minimax mini-batch. Same criteria.
3. Kimi single-cell debug: trajectory dive, then mini-batch if the
   behaviour clears.
4. Mistral single-cell debug; report findings, decide inclusion.

### Stage 4 — Headline 60-cell sweep

Once Stages 1–3 are done, run the full panel:

- 5 models × 2 rungs (1, 5) × 2 targets (mesa, josh) × 3 replicates = 60 cells.
- ~6–8 hours at `--jobs 4`.
- Tag the batch with the date and the climate-data SHA so reanalysis is
  unambiguous.

## Open methodology questions

None are blockers, but each deserves an answer before the headline
batch:

1. **Should `target_conformance=False` runs count toward the
   denominator of any headline metric?** A claude run that produces a
   valid CSV without using Mesa is technically "did the task" but
   doesn't measure what we're trying to measure. Recommend reporting
   pass rates *conditional* on conformance, plus a separate
   conformance-rate-per-model figure.
2. **Should rungs 2–4 be authored before the headline batch?** Phase 5
   left them as deferred. The 4-cell mini-batch above will tell us
   whether rung 5 produces clean signals; rungs 2–4 give the
   "increasing detail → decreasing variance" axis the paper claims.
   Recommend writing them after Stage 2 validates rung 5.
3. **Is the recovery loop in scope for this paper?** Phase 5b (recovery
   prompts) is built mentally but not implemented. With synthetic data
   producing real failures we'd actually want to see if agents can
   recover, but that's a 2× cost multiplier on every batch. Decide
   based on Stage 2 results — if one-shot quality is already high
   enough to be informative, recovery is gravy; if one-shot is sparse,
   recovery is the only way to get to interpretable H1/H2 numbers.

## Status of resolved concerns (for the record)

The May-18 DEBUG_PLAN.md catalogued 11 concerns; all are now in one of
the categories below.

- **Resolved by harness changes**: firewall blocking
  raw.githubusercontent.com; idle-watcher killing real work;
  scorer-side timeout too tight; `external_directory` permission ask
  stalling; schema rejecting Josh's native column order; schema
  rejecting NaN cells.
- **Resolved by config changes**: `task` tool looping in gemma;
  `permission.bash` syntax not surfacing bash to the model.
- **Resolved by SIDECAR changes**: precipitation unit conversion factor
  (× 86_400 vs × 31_536_000); dropping the FGOALS-g3 attribution.
- **Superseded** (no longer applicable): `did_run=rc=0` gating
  (replaced by `script_was_executable` + cleaner derived fields);
  Mesa NaN systematic pattern (schema now filters; synthetic data
  removes the underlying coordinate-mask issue); height-out-of-range
  as a "real result" signal (acceptance ranges themselves are being
  redesigned).
- **Not seen recently** (probably one-offs): josh OOM in
  `HaversineUtil`; gemma cell_id tuple-repr.

# Implementation plan — engineering build state

Engineering-side state of the ForeverTree LLM experiment harness:
what's built, how it's structured, and what remains. For the
experimental methodology (hypothesis, run flow, threats to validity),
see [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md). For the scoring
axes, metric definitions, LLM-judge spec, re-analysis recipe, and
open scoring questions, see [SCORING.md](SCORING.md). For installation
and how to run, see [README.md](README.md).

Per-PR detail lives in `git log` and the merged PR descriptions; this
document is a navigation map, not a complete change history.

## Architecture

One Docker base image, three roles, wired together by a Python
orchestrator on the host:

```
host:
├── docker daemon
└── uv (Python tool installer, manual install per README)

fortree base image (Dockerfile):
├── Python 3.11 + scientific stack (mesa, numpy, pandas, scipy,
│                                   xarray, netCDF4, rasterio,
│                                   tiktoken, jinja2, compliance-checker)
├── Eclipse Temurin 21 JRE
├── /usr/local/bin/josh             ← wrapper around joshsim-fat.jar
└── /usr/local/bin/opencode         ← pinned 1.14.50

stages:
- fortree:agent   — base only; runs `opencode run` for the model
- fortree:scorer  — base + /opt/harness/ (scoring code)
- fortree:dnsmasq — separate alpine image with iptables + ipset; the
                    egress-allowlist sidecar (Dockerfile.dnsmasq)

invocation pattern per cell (orchestrated by orchestration/launch_*.sh):
1. Bring up a per-run Docker bridge network.
2. Start `fortree:dnsmasq` on it with `--cap-add NET_ADMIN`;
   sidecar-init.sh writes the iptables OUTPUT rules (default DROP +
   carve-outs for lo/conntrack/upstream-DNS/bridge-gateway/allowlist).
3. Start `fortree:agent` joining the sidecar's netns
   (`--network=container:dnsmasq-<run-id>`); agent's egress is hard-
   filtered at the kernel. Inside the container, agent-entrypoint.sh
   invokes opencode **eight times in a row**, one per todo, against
   a shared `/sandbox/PLAN.md` working document (see "Phase 5c" below).
4. After the multi-invocation chain finishes, run `fortree:scorer`
   against the workspace under `--network=none` with a writable
   `results/` and read-only data/ + workspace mounts.
5. Generate per-cell `report.md`. Tear down network + sidecar.
6. Per-batch driver aggregates all cells into `manifest.jsonl` and
   renders `batch_report.md`.

Pinned versions in [config/VERSIONS.md](config/VERSIONS.md). Python
deps in [config/requirements.txt](config/requirements.txt). Host
deps in [pyproject.toml](pyproject.toml) (`pyyaml`, `rich`).

## Build history

### Phase 1 — Environment bootstrap (PR #2) ✓

Single Docker image with Python 3.11, JDK, the Josh CLI as a wrapper
around `joshsim-fat.jar` (sha256-pinned), and opencode 1.14.50
installed via upstream. Scorer entrypoint stub that defers to the
harness in later phases. Initial pinned `requirements.txt`,
`.env.example`, and the README "Host prerequisites" section.

### Phase 2 — Scoring harness end-to-end on static fixtures

**Phase 2a (PR #3) ✓** — JRE upgraded from 17 to 21 (Temurin),
`josh` wrapper exec's `java -jar` correctly. BASE_PROMPT bbox and
year range pinned. SIDECAR boilerplate (env, External Inputs,
`./run.sh` + `./output/results.csv` schema). Initial v0
`acceptance_ranges.json` (height 0–11 m, occupancy 9.9–10.1).

**Phase 2b-core (PR #4) ✓** — Multistage Dockerfile splitting into
`base` / `agent` / `scorer`. Scoring harness: `run_metrics.py`
(orchestrator), `runner.py` (invokes `./run.sh` with process-group
timeout), `validators/output_schema.py`, `validators/acceptance.py`,
`loc.py`, `entropy.py`, `_files.py` (target-aware file enumeration).
Scorer JSON schema v1 (`phase2-v1`).

**Phase 2b-fixtures (PR #5) ✓** — Static CSV fixtures under
`reference/golden/` and `reference/broken/{schema,nan-heights,
missing-year,nan-precip}/`. Each fixture is a tiny `run.sh` that
emits a canned CSV; the scorer is asserted against each fixture's
expected outcome in `.github/scripts/smoke-fixtures.sh` (run on
every push by `smoke.yml`).

**Phase 2 polish**: PR #7 added the `nTrees` column to the schema so
the occupancy check could count trees directly rather than rows.
PR #8 added the idle-stream heartbeat (later refactored in PR #17 to
poll the opencode session-DB mtime instead of trajectory.jsonl size).

### Phase 3 — Single agent call, end-to-end (PR #6) ✓

`prompts/rung1_minimal.md`, `prompts/rung5_master.md`, derived from
BASE_PROMPT plus the SIDECAR footer. `config/models.yaml` (short
name → OpenRouter slug). `config/opencode.template.json` rendered per
run with API key + model + workspace substitutions. `config/
docs_categories.yaml` for post-hoc URL categorisation. The driver:
[orchestration/launch_run.sh](orchestration/launch_run.sh).

### Phase 4 — Observation, parallelism, CI

**Phase 4a — CI + local-LLM (Ollama) path (PR #9, #14) ✓** —
Two GitHub Actions workflows: `smoke.yml` (deterministic fixture +
firewall checks on every push) and `integration.yml`
(`workflow_dispatch` only; one workflow, two provider paths via
[.github/scripts/setup-provider.sh](.github/scripts/setup-provider.sh)).
opencode 1.14.50's Ollama support is wired through
`@ai-sdk/openai-compatible`; `config/models.yaml` carries
`ollama-qwen-coder-{1_5b,7b}` for key-free CI replication.

**Phase 4b — Observation + kernel-level egress enforcement
(PR #10, #13) ✓** — Sidecar built from
[Dockerfile.dnsmasq](Dockerfile.dnsmasq) (alpine + dnsmasq + iptables
+ ipset). [orchestration/sidecar-init.sh](orchestration/sidecar-init.sh)
installs iptables OUTPUT rules (default DROP + carve-outs).
[orchestration/dns_sidecar.sh](orchestration/dns_sidecar.sh) manages
per-run network + sidecar lifecycle, gates on HEALTHCHECK before
returning. Agent joins via `--network=container:dnsmasq-<id>`. The
`firewall-probe` job in `smoke.yml` asserts allow vs reject on four
hosts every push; reaches into the sidecar's ipset + iptables
counters to prove enforcement.

**Mid-phase polish landed alongside 4a/4b**: PR #11 rewrote
`orchestration/generate_run_report.py` around `opencode export` +
Jinja2 ([orchestration/templates/report.md.j2](orchestration/templates/report.md.j2)).
PR #12 added the SIDECAR self-test contract (agent must `chmod +x
run.sh` AND run it once before declaring done). PR #15 publishes
`report.md` to `$GITHUB_STEP_SUMMARY` for inline GH-UI viewing. PR #16
consolidated `integration-ollama.yml` and an OpenRouter path into
one `integration.yml`. PR #17 switched the idle watcher from
`trajectory.jsonl` size to opencode session-DB mtime (catches
sub-agent activity).

**Phase 4c — Local parallelism (PR #19, #20) ✓** —
[orchestration/launch_cell.sh](orchestration/launch_cell.sh) factors
agent → scorer → report into one unit (same code path under CI and
the local driver). [orchestration/launch_batch.py](orchestration/launch_batch.py)
fans out cells via `concurrent.futures.ThreadPoolExecutor`. Two CLI
forms: `--model M --rung R --target T --runs N` and `--cells cells.csv`.
Per-batch metadata in `runs/<batch-tag>/` (`worklist.tsv`,
`joblog.tsv`, `manifest.jsonl`, `summary.txt`, `cell-logs/<run_id>.log`).
Rich live panel with per-cell lifecycle phase + ETA; auto-degrades to
line-oriented output in non-TTY. Orphan-resource pre-sweep handles
SIGKILL recovery. Host deps (`pyyaml`, `rich`) in
[pyproject.toml](pyproject.toml); `uv sync` provisions them.

**Phase 4c polish**: PR #21 allowed `external_directory` in the
opencode permission block to fix non-interactive permission stalls.
PR #22 enhanced the per-cell report. PR #23 distinguished
presumed-done from genuine stall in the heartbeat. PR #24 grouped
all per-run dirs under `runs/<batch-tag>/`.

**Phase 4d — Durable upload — DONE (host-side mc).** See *Pending
engineering work* below for the implementation summary.

### Phase 5 — Scoring revision (5a); recovery loop (5b, retired); multi-invocation flow (5c)

**Phase 5a — Scoring revision (PR #25, #26, #27) ✓** — Three PRs
that together delivered the post-pilot scoring infrastructure:

- PR #25: scorer JSON bumped to `phase5a-v1`. Added
  `harness/conformance.py` (mechanical target-conformance check:
  Mesa imports + Model/Agent subclassing; Josh `*.josh`/`*.jshd`
  presence + `josh validate` exit zero). Added
  `harness/internal_consistency.py` (per-(cell, year→year+1)
  growth-rate stats, age-step, nTrees-change, climate-response
  Spearmans). Schema validator loosened to subset-match required
  columns, NaN-tolerant via row filtering (counted as
  `csv_rows_dropped_nan`). Added `harness/conformance_fuzzy.py` stub.
  Smoke fixtures updated for new NaN behaviour.
- PR #26: discovered and fixed that
  `permission.bash` as a per-pattern object suppressed the bash
  tool's exposure to the model entirely. Switched to
  `permission.bash: "allow"` (string form) so bash actually surfaces.
  Disabled `task` to stop gemma's malformed-call loop. Added
  `script_was_executable` field; runner self-heals chmod so the agent
  failing to chmod doesn't gate the measurement. SIDECAR rewritten
  around the climate-conversion clarification.
- PR #27: replaced the original Cal-Adapt netCDFs with a synthetic
  CF-1.8 dataset (`data/maxtemp_synthetic.nc`,
  `data/precip_synthetic.nc`) generated deterministically from
  [`data/generate_synthetic_climate.py`](data/generate_synthetic_climate.py)
  (seed=42, byte-identical across regen runs). Added IOOS
  `compliance-checker` to `config/requirements.txt` and the
  [`data/validate_synthetic_climate.py`](data/validate_synthetic_climate.py)
  validator (31 checks: shape/coord/unit/gradient/no-NaN, spec-growth
  implication, CF-1.8 conformance). The precip data is a true flux
  in `kg m⁻² s⁻¹` convertible to mm/year via the standard
  `× 31_536_000`. Also added
  [`orchestration/generate_batch_report.py`](orchestration/generate_batch_report.py)
  (auto-invoked by `launch_batch.py`) producing per-batch
  `batch_report.md` with at-a-glance matrix, per-cell drill-down,
  failure-mode tally.

**Phase 5b — Recovery loop — RETIRED.** The multi-invocation flow's
todos 5–8 (stub → implement → validate → cleanup) bake the iterative
self-correction into every cell's run, so a separate recovery-prompt
mechanism is no longer needed. EXPERIMENTAL_DESIGN's H2 hypothesis
folds into H1.

### Phase 5c — Multi-invocation planning flow ✓

The agent phase now invokes opencode **eight times in a row** against
the same per-cell workspace, one invocation per todo, with a shared
`/sandbox/PLAN.md` working document carrying state across steps.
Sessions are fresh per invocation (no `--continue`) — all continuity
lives on disk in `PLAN.md` and the workspace.

Motivation: small / local models struggle without explicit planning
scaffolding. Splitting the work into discrete, plan-anchored steps
gives them a structured "read PLAN.md → list actions → do one thing →
mark `[x]` → exit" rhythm and makes their planning artefacts
inspectable.

**Files**:
- [prompts/PLAN_TEMPLATE.md](prompts/PLAN_TEMPLATE.md) — seed for
  `/sandbox/PLAN.md`. Carries the 8 fixed todos and an empty `## Plan`
  section.
- [prompts/steps/step_NN_*.md](prompts/steps/) — 8 pre-committed
  per-step injection files. Static across all runs.
- [prompts/SIDECAR.md](prompts/SIDECAR.md) — rewritten around the
  procedure narrative + AI environment / inputs / success criteria /
  working-document sections.
- [prompts/](prompts/) — reorganised into `rungs/`, `steps/`,
  `targets/` subfolders. `prompts/rung5_master.md` retired (rung 5
  now reads `prompts/BASE_PROMPT.md` directly).
- [agent-entrypoint.sh](agent-entrypoint.sh) — loops over
  `/opt/steps/step_*.md`, builds each per-step prompt in-memory as
  `prompt_body + step_file`, runs opencode, exports per-step session,
  writes `step_meta.json`. Honours `FAIL_FAST_ON_STEP_ERROR`.
- [orchestration/launch_run.sh](orchestration/launch_run.sh) — renders
  `prompt_body.md` once and seeds `workspace/PLAN.md` from the
  template. No per-step rendering at runtime — the 8 step files are
  bind-mounted straight from the repo.
- [orchestration/run_agent.sh](orchestration/run_agent.sh) — new
  bind mounts (`/opt/prompt_body.md`, `/opt/steps/`); passes
  `FAIL_FAST_ON_STEP_ERROR` into the agent container.

**Knob (`.env`)**:
- `FAIL_FAST_ON_STEP_ERROR=false` (default, production) — log per-step
  failures and continue. Partial completion is data.
- `FAIL_FAST_ON_STEP_ERROR=true` (dev / CI) — first non-zero step
  aborts the loop. Used to surface broken plumbing fast.

**Per-step artefacts under `runs/<batch>/<id>/agent_artifacts/steps/step_NN/`**:
- `trajectory.jsonl` — that step's opencode events
- `agent_stderr.log` — that step's stderr
- `session_export.json` — that step's opencode export
- `step_meta.json` — `{step_n, step_name, started_at, ended_at, exit_code}`

The cell-level rollups (`trajectory.jsonl`, `agent_stderr.log`,
`agent_artifacts/session_export.json`) remain at their legacy paths
so downstream consumers (`generate_run_report.py`,
`extract_transcript.py`, `extract_time_breakdown.py`) keep working;
they now see the in-order concatenation of all 8 steps and the
final step's export respectively. Cross-step token / timing
aggregation is a follow-up.

**Example multi-invocation pattern (operator-level reference)**:

```sh
# What agent-entrypoint.sh effectively runs, inside one container:
opencode run "<prompt_body>\n\n<step_01_make_plan_section>"
opencode run "<prompt_body>\n\n<step_02_describe_geospatial>"
...
opencode run "<prompt_body>\n\n<step_08_cleanup_code>"
```

Each `<step_NN_*>` file tells the model "your assigned todo is N: …;
read /sandbox/PLAN.md; list your actions; complete only this todo;
mark [x]; exit." Cross-step state lives entirely in `PLAN.md` and
the workspace files.

**Follow-on fixes (same PR):**

- **Permissive cell-identity schema.** `harness/validators/output_schema.py`
  no longer requires `lat`/`lon`/`cell_id` specifically. Cell identity
  accepts either `cell_id` (string) OR `position.x` + `position.y`
  (numeric, Josh's default). `load_clean_results` synthesises `cell_id`
  from the position pair when only the alt is present, so
  `internal_consistency.py` is unchanged. Removes the "model must
  rename Josh's native export to match our spec" gymnastics that
  bricked two recent Josh-target cells. New CI-gated fixture
  [reference/golden-josh-defaults/](reference/golden-josh-defaults/)
  exercises the alt path.
- **`.jshd` LOC bugfix.** `harness/_files.py` no longer counts `.jshd`
  binary preprocessed data as source. The byte stream contained
  newlines, so a 112 KB binary was being read as ~2000 lines of code —
  inflated `src_loc` by 400× on cells that ran `josh preprocess`
  against the full grid. Conformance still detects `.jshd` presence
  via the new `find_workspace_files` helper.
- **Batch-report multi-invocation diagnostics.** `manifest.jsonl` rows
  gain `steps` (per-step exit codes from `step_meta.json`) and
  `plan_todos` (count of `[x]` boxes in `workspace/PLAN.md`).
  `batch_report.md` renders a new "Multi-invocation step status"
  section: 8-glyph per-cell status string (`✓✗·`), todos-checked
  count, and links to each cell's `PLAN.md` + `agent_artifacts/steps/`.
- **Prompt-procedure tightening (gemma nudge).** SIDECAR's Procedure
  paragraph caps the planning preamble at 1–2 sentences and adds
  "Then carry them out by calling the available tools — listing the
  plan is a preamble, not the task itself." Targets the failure mode
  observed in the panel batch where gemma listed actions and stopped
  without invoking any tool.
- **Targets renamed.** `prompts/target_directive_{josh,mesa}.md` →
  `prompts/targets/{josh,mesa}.md` to match the new subfolder layout.
- **Review-driven polish.** `launch_batch.py --upload` flag for
  opportunistic auto-archive after batch completion (non-fatal on
  failure; the standalone `upload_batch.sh` remains the
  crash-recovery path). README sweep examples reframed around a
  committed CSV panel rather than nested bash for-loops. SIDECAR's
  cell-identity prose tightened to a single legal-identifier
  sentence (no framework-defaults exposition). EXPERIMENTAL_DESIGN
  gained the "why force decomposition" methodology paragraph
  capturing the pre-phase-5c observation that models were getting
  stuck on orchestration concerns and skipping the
  ecological-modelling step.

## Current state

Scorer JSON schema: `phase5a-v1` (`harness/run_metrics.py:SCHEMA_VERSION`).

Per-cell artefacts under `runs/<batch-tag>/<run_id>/`:
- `workspace/` — agent-authored files (including `PLAN.md`, the
  multi-invocation working document)
- `prompt_body.md` — rendered shared body (rung + target + SIDECAR);
  `prompt.md` is a back-compat symlink to it
- `trajectory.jsonl` — in-order concatenation of all 8 steps' opencode
  events
- `agent_stderr.log` — in-order concatenation of all 8 steps' stderr
- `agent_artifacts/session_export.json` — final attempted step's
  opencode export
- `agent_artifacts/steps/step_NN/` — per-step `trajectory.jsonl`,
  `agent_stderr.log`, `session_export.json`, `step_meta.json`
- `dns.log` — every DNS query the agent container made
- `scorer.json` — full scoring record (`phase5a-v1` schema)
- `report.md` — Jinja2-rendered per-cell report
- `transcript.md` — human-readable opencode transcript (from the
  final step's export)
- `time_breakdown.json` — phase timings (from the final step's export)
- `run_meta.json`, `run_meta.cell.json`, `run_meta.final.json` — orchestration metadata

Per-batch artefacts under `runs/<batch-tag>/`:
- `worklist.tsv` — input cells in seq order
- `joblog.tsv` — per-cell runtime + exit code
- `manifest.jsonl` — aggregated `run_meta` + `cell` + `scorer.json`
- `summary.txt` — totals
- `batch_report.md` — at-a-glance markdown rollup
- `cell-logs/<run_id>.log` — per-cell stdout+stderr capture

## Headline-run readiness

What's blocking vs nice-to-have for the headline batch, in order of
materiality:

| Item | Required? | Status |
|---|---|---|
| Multi-invocation flow end-to-end | yes | ✓ verified on claude × {josh,mesa} and minimax × {josh,mesa} (32/32 step exits clean per cell, PLAN.md updated as expected) |
| Permissive cell-identity schema | yes | ✓ |
| `.jshd` LOC fix | yes | ✓ |
| Batch-report diagnostics | yes | ✓ |
| Durable upload to GCS | yes | ✓ host-side `orchestration/upload_batch.sh` (mc, no container path); `launch_batch.py --upload` auto-invokes it post-batch |
| `WALL_CLOCK_BACKSTOP_SEC` bump to 3600s | yes | ✓ set in `.env` |
| Model panel pinned to versioned slugs | yes | ✓ `config/models.yaml` pins the five-family panel (claude-opus-4.7, gemma-4-26b-a4b-it, kimi-k2.6, minimax-m2.7, mistral-medium-3.5) — verified `resolve_model.py` on each short name. The single open item: a one-cell gemma-4 sanity probe under the multi-invocation flow before the headline batch (gemma-3 failed it 0/4 in the phase-5c panel) |
| Re-scoreable on completed runs | yes | ✓ verified by rescoring a panel-batch cell against its preserved workspace — same metrics modulo Mesa's stochastic O term. Recipe documented in [SCORING.md §Re-analysing completed runs](SCORING.md#re-analysing-completed-runs); a `rescore_batch.sh` wrapper is on the to-author list once a concrete methodology revision is in hand |
| Acceptance-range methodology (SCORING.md Open Q #1) | post-headline | 📋 intentionally deferred — re-score path lets us revise the gate against frozen workspaces |
| Predicted-vs-observed r² metric (SCORING.md Open Q #3) | post-headline | 📋 nice-to-have; same re-score-on-completed-runs path |
| LLM-judge passes (SCORING.md §LLM-judge passes) | post-headline | 📋 not implemented; convenience-tier, not in the experimental yardstick |

## Phase decisions (post-pilot)

Three phase items that were previously listed as "pending" have all
resolved — kept here so the record of *why* the decision went the
way it did is preserved for future contributors.

### Phase 4d — Durable upload to GCS via S3 interop ✓

Implemented host-side rather than in the agent container — the agent
image stays free of `mc` and of any object-storage credentials. The
upload runs after a batch completes, against the per-batch run dir.

- [orchestration/upload_batch.sh](orchestration/upload_batch.sh) —
  reads `MINIO_*` env vars from `.env`, runs `mc alias set` then
  `mc mirror --overwrite` against `runs/<batch-tag>/`. Idempotent
  (re-mirroring only re-uploads changed objects).

- [orchestration/launch_batch.py](orchestration/launch_batch.py)
  `--upload` flag — auto-invokes `upload_batch.sh` against the
  completed batch dir after report generation. Failure writes
  `upload.log` under the batch dir but does not fail the batch
  (the artefacts are still on local disk and the standalone script
  is idempotent, so a host crash or transient upload error is
  recoverable by rerunning `./orchestration/upload_batch.sh
  runs/batch-<tag>` directly). Two invocation modes:

  ```sh
  # auto: upload runs at end of batch driver
  uv run orchestration/launch_batch.py --cells panel.csv \
    --batch-tag head-2026-05 --upload

  # manual: any time after the batch finishes / for crash recovery
  ./orchestration/upload_batch.sh runs/batch-head-2026-05
  ```

- [.env.example](.env.example) — documents `MINIO_ENDPOINT`,
  `MINIO_BUCKET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`,
  `MINIO_PREFIX`.

- Host-side `mc` install is a one-liner; not pinned at the image
  level since the image doesn't carry it.

**Object layout under the bucket:**
- `<prefix>/<batch-tag>/<run-id>/…` — per-cell artefacts
- `<prefix>/<batch-tag>/batch_report.md` — per-batch report
- `<prefix>/<batch-tag>/manifest.jsonl` — aggregated manifest

### Recovery loop — retired

H2 ("Recovery quality") and the phase-5b recovery-prompt mechanism
have been retired. The multi-invocation flow's todos 5–8 (stub →
implement → validate → cleanup) bake the same iterative
self-correction into every cell's run, so a separate second-invocation
recovery pass is no longer needed. EXPERIMENTAL_DESIGN reflects the
hypothesis simplification.

### Prompt rungs

The rung-ladder is retired operationally — `RUNG` defaults to 5 in
both `launch_run.sh` and `launch_batch.py`, and the headline panel is
`model × target × replicates` only. `prompts/rungs/rung1_minimal.md`
stays on disk in case a future variant wants to vary prompt detail,
but is not used in the headline run.

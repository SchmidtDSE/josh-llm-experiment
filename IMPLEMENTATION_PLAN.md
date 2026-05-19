# Implementation plan — engineering build state

Engineering-side state of the ForeverTree LLM experiment harness:
what's built, how it's structured, and what remains. For the
experimental methodology (hypothesis, prompt rungs, metrics, threats
to validity), see [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md).
For installation and how to run, see [README.md](README.md).

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
   filtered at the kernel.
4. After agent exits, run `fortree:scorer` against the workspace
   under `--network=none` with a writable `results/` and read-only
   data/ + workspace mounts.
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

**Phase 4d — Durable upload — PENDING.** See *Pending engineering
work* below.

### Phase 5 — Scoring revision (5a) and recovery loop (5b)

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

**Phase 5b — Recovery loop — PENDING.** See *Pending engineering
work* below.

## Current state

Scorer JSON schema: `phase5a-v1` (`harness/run_metrics.py:SCHEMA_VERSION`).

Per-cell artefacts under `runs/<batch-tag>/<run_id>/`:
- `workspace/` — agent-authored files
- `prompt.md` — rendered prompt
- `trajectory.jsonl` — opencode's per-tool log
- `agent_artifacts/` — opencode session export
- `agent_stderr.log` — opencode stderr
- `dns.log` — every DNS query the agent container made
- `scorer.json` — full scoring record (`phase5a-v1` schema)
- `report.md` — Jinja2-rendered per-cell report
- `transcript.md` — human-readable opencode transcript
- `time_breakdown.json` — phase timings
- `run_meta.json`, `run_meta.cell.json`, `run_meta.final.json` — orchestration metadata

Per-batch artefacts under `runs/<batch-tag>/`:
- `worklist.tsv` — input cells in seq order
- `joblog.tsv` — per-cell runtime + exit code
- `manifest.jsonl` — aggregated `run_meta` + `cell` + `scorer.json`
- `summary.txt` — totals
- `batch_report.md` — at-a-glance markdown rollup
- `cell-logs/<run_id>.log` — per-cell stdout+stderr capture

## Pending engineering work

### Phase 4d — Durable upload to GCS via S3 interop

Per-batch run dirs are currently host-local. The plan: ship them to a
GCS bucket via its S3 interoperability API using the `mc` client.

**Files to author**
- [orchestration/upload_run.sh](orchestration/upload_run.sh) — runs
  `mc alias set` from `MINIO_*` env vars then `mc cp --recursive`
  per run dir. Opportunistic: on failure, record `upload_status=failed`
  in `run_meta.json` and continue.
- [Dockerfile](Dockerfile) — install the `mc` static binary in the
  `base` stage, sha256-pinned.
- [config/VERSIONS.md](config/VERSIONS.md) — pin `mc` version.
- [.env.example](.env.example) — add `MINIO_ENDPOINT=https://storage.googleapis.com`,
  `MINIO_BUCKET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `BATCH_TAG`.
  Naming is for project consistency (the bucket is GCS via S3 interop,
  not a MinIO server).

**Validation gate**
- A single run completes, `mc ls $alias/${MINIO_BUCKET}/${BATCH_TAG}/<RUN_ID>/`
  lists every file under the local `runs/<RUN_ID>/`.
- A run with broken HMAC credentials records `upload_status=failed`
  and the run itself still completes (upload is opportunistic).

### Phase 5b — Recovery loop

H2 in the experimental design ("Recovery quality") is currently
unmeasurable. The recovery flow is designed but not implemented.

**Files to author**
- [prompts/recovery_template.md](prompts/recovery_template.md) —
  Markdown skeleton with `{{ORIGINAL_RUNG_PROMPT}}`,
  `{{ORIGINAL_TARGET_DIRECTIVE}}`, `{{BINARY_OUTCOMES_BLOCK}}`,
  `{{SIDECAR}}` placeholders. The binary-outcomes block surfaces only
  structural fields (`did_run`, `exit_code`, `timed_out`,
  `csv_exists`, `csv_schema_ok`, `csv_schema_errors`, `stderr_tail`
  truncated). Explicitly excluded per EXPERIMENTAL_DESIGN's recovery
  contract: `height_*`, `occupancy_*`, `acceptance_ranges_used`,
  `src_loc`, `entropy_bits`.
- [orchestration/render_recovery_prompt.py](orchestration/render_recovery_prompt.py) —
  strict whitelist over `scorer.json`. Asserts the expected
  `schema_version` and copies only the named fields through, so new
  fields added to scorer.json never automatically leak into recovery
  prompts.
- [harness/docs_log.py](harness/docs_log.py) — joins opencode's
  trajectory `WebFetch` URLs with `dns.log` to produce the `docs_*`
  metric fields, categorised via `config/docs_categories.yaml`.
- Update [orchestration/launch_run.sh](orchestration/launch_run.sh)
  to implement the full 5-step flow end-to-end:
  1. One-shot scorer runs (already done in phase 3).
  2. If `did_run AND height_in_range AND occupancy_in_range`,
     record `recovery_attempted=false`, skip recovery.
  3. Else: render recovery prompt; invoke opencode a second time
     against the same workspace; record trajectory to
     `trajectory_recovery.jsonl`.
  4. Re-run the scorer against the post-recovery workspace, writing
     `scorer_recovery.json`.
  5. Manifest row records both `oneshot_*` and `recovery_*` field
     families.

**Validation gate**
- A recovery-triggering rung-1 run produces `recovery_attempted=true`
  and `recovery_*` fields populate.
- Grep gate: rendered `recovery_prompt.md` contains no occurrence of
  `height_year10_mean`, `occupancy_year10_mean`, `height_in_range`,
  `occupancy_in_range`, `acceptance_ranges_used`, `src_loc`,
  `entropy_bits` — confirms the strict whitelist holds.
- `scorer_recovery.json` carries the same `schema_version` as the
  one-shot `scorer.json`.

### Prompt rungs 2–4

The phase-3 prompts are `rung1_minimal.md` and `rung5_master.md`
only. Rungs 2–4 are deferred until the headline-batch authoring pass;
their content is straightforward (interpolating detail between the
two endpoints) but the wording is paper-bearing and should be drafted
once 5b is in place so the recovery contract is settled first.

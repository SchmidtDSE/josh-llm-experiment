# Implementation plan — Build out the ForeverTree LLM experiment harness (phases 1–5)

## Context

[README.md](README.md) describes an experiment harness for measuring how prompt-detail affects LLM-generated code on a fixed task (ForeverTree), comparing a constrained DSL target (Josh) against a general framework (Mesa). The README is aspirational: only itself, [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md) (the ForeverTree spec content), and the two netCDF climate inputs under [data/](data/) currently exist. ~95% of the repo listed in the README's "Repository layout" must be built.

We will stair-step the build so that each phase produces a demonstrable artifact that validates the foundation for the next phase, rather than building monolithically and running once at the end. **Scope of this plan is phases 1–5 only** — env bootstrap through a single successful 5-step orchestrated run for one (model × rung × target) cell. The pilot sweep and headline sweep get their own subsequent plans, after pilot data is in hand and prompts can be frozen.

The plan validates the end-to-end opencode path first under opencode's own tool allowlists plus passive observation (trajectory log + DNS tripwire). A hard-layer egress proxy (OpenShell or equivalent) is out of scope for phases 1–5.

## Architecture

One Docker image, used two ways, wired together by a bash orchestrator:

```
host:
├── docker daemon
└── uv (manual install per README)

fortree image (Dockerfile):
├── Python 3.11 + scientific stack (mesa, numpy, pandas, scipy,
│                                   xarray, netCDF4, rasterio, tiktoken)
├── OpenJDK 17 (distro openjdk-17-jre-headless)
├── /usr/local/bin/josh             ← wrapper around joshsim-fat.jar
├── /usr/local/bin/opencode         ← pinned agent binary
├── /opt/entrypoint-scorer.sh       ← invoked in scorer mode
└── (later phases) /opt/harness/    ← scoring code

invocation modes:
- agent:    docker run --rm --env-file .env
              -v ./runs/<run-id>:/sandbox
              --dns <dnsmasq-sidecar-ip>
              fortree:<tag> opencode run --config /opt/opencode.json ...
            (opencode's WebFetch + bash allowlists are the soft policy;
             a dnsmasq sidecar on the docker bridge logs all DNS queries
             as a passive tripwire for non-allowlisted hosts)
- scorer:   docker run --rm --network=none
              -v ./runs/<run-id>:/sandbox
              fortree:<tag> /opt/entrypoint-scorer.sh --target <josh|mesa>
```

Decisions:
- **Version pins.** Python 3.11 / OpenJDK 17 / `opencode==1.14.50` inside the image. See [config/VERSIONS.md](config/VERSIONS.md).
- **Single image, no host-side runtime install.** Python, Java, Josh, scientific deps, opencode all in the image. The host only needs Docker and uv.
- **Acceptance ranges.** User authors [harness/acceptance_ranges.json](harness/acceptance_ranges.json) themselves and commits before any agent runs.

## Branching workflow

- `main` — stable. Only merges from `dev` after batches of phases land.
- `dev` — integration branch. Phase feature branches PR into `dev`.
- `phase-N-<short-name>` — one branch per phase (e.g. `phase-1-env-bootstrap`). Closes via PR into `dev` once the phase's validation gate passes.

## Phase 1 — Env bootstrap *(complete)*

Landed on `phase-1-env-bootstrap` (PR #2 → `dev`).

**What's in the repo**
- [`Dockerfile`](Dockerfile) — unified image, `FROM ghcr.io/nvidia/openshell-community/sandboxes/base:db19652` + `openjdk-17-jre-headless` + Josh CLI + Python 3.11 venv at `/opt/fortree-venv` with the pinned scientific stack + opencode 1.14.50 (override of base's 1.2.18) + scorer entrypoint stub. Build: `docker build -t fortree:latest .`.
- [`scripts/install_josh.sh`](scripts/install_josh.sh) — downloads the prod fat jar, records its sha256, drops the `/usr/local/bin/josh` wrapper. Invoked from the Dockerfile but callable standalone.
- [`entrypoint-scorer.sh`](entrypoint-scorer.sh) — stub that defers to `harness/run_metrics.py` (lands in phase 2); exits `64` with a clear error until then.
- [`config/requirements.txt`](config/requirements.txt) — pinned Python deps.
- [`config/VERSIONS.md`](config/VERSIONS.md) — pin record (host + image split).
- `README.md` "Host prerequisites" section — manual install steps for Docker and uv. No install script.
- `.gitignore` and [`.env.example`](.env.example) — secret-passing pattern (`docker run --env-file .env ...`) tested.

**Validation gate**
- `docker build -t fortree:dev .` succeeds.
- `docker run --rm fortree:dev python -c "import sys; print(sys.version_info[:2]); import mesa, numpy, pandas, scipy, xarray, netCDF4, rasterio, tiktoken; print('ok')"` → `py (3, 11)` + `ok`.
- `docker run --rm fortree:dev josh --version` → pinned sha256 `ef5f7ef9…`.
- `docker run --rm fortree:dev opencode --version` → `1.14.50`.
- `docker run --rm --network=none fortree:dev python -c "print('offline')"` → `offline`.
- `docker run --rm --env-file .env fortree:dev printenv OPENROUTER_API_KEY` returns the value.

## Phase 2 — Scorer-only loop (no agents)

Split three ways for reviewability: 2a (Java + bbox + spec, complete), 2b-core (Docker + harness, in PR #4), 2b-fixtures (static CSV fixtures exercising the scorer end-to-end).

Hand-written reference implementations in Mesa and Josh are deferred. The scorer can be validated end-to-end against static CSV fixtures (a hand-crafted "golden" output plus four broken variants); a real model implementation isn't on the critical path until pilot agent data in phase 3+ tells us whether spec implementability needs separate verification.

### Phase 2a — Java upgrade, bbox fix, spec authoring *(complete)*

Landed on `phase-2a-java-bbox-spec` (PR #3 → `dev`, merged as `8f447d4`).

- [scripts/install_java.sh](scripts/install_java.sh) (Adoptium Temurin 21 JRE).
- [Dockerfile](Dockerfile) updated to use Temurin 21; `josh --help` now invokes the JVM end-to-end.
- [scripts/install_josh.sh](scripts/install_josh.sh) wrapper exec's `java -jar` for all subcommands; sha256 is still captured on disk at install time.
- [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md) bbox + year-range corrected (Tulare actual coords; 2024–2034).
- [prompts/SIDECAR.md](prompts/SIDECAR.md) — boilerplate footer appended to every rung. Single source of truth for the contract; covers env, External Inputs, `./run.sh` + `./output/results.csv` schema.
- [harness/acceptance_ranges.json](harness/acceptance_ranges.json) — user-authored v0 ranges (height_year10 = [0, 11] m, occupancy_year10 = [9.9, 10.1]).
- [config/VERSIONS.md](config/VERSIONS.md) — Java pin updated.

### Phase 2b-core — Multistage Dockerfile + harness *(PR #4)*

**Harness code to author**
- [harness/run_metrics.py](harness/run_metrics.py) — top-level scorer entry point; invokes runner, validators, computes loc + entropy, emits JSON record on stdout AND writes `/sandbox/results/scorer.json`.
- [harness/runner.py](harness/runner.py) — invokes `./run.sh`; captures exit code, stdout/stderr tails (4 KiB each), wall time, timeout flag. `preexec_fn=os.setsid` + `os.killpg(SIGKILL)` on timeout. Single file, target-agnostic.
- [harness/validators/output_schema.py](harness/validators/output_schema.py) — CSV existence + columns + dtypes + row count + NaN-in-numeric-columns check (NaN flips `csv_schema_ok=false`).
- [harness/validators/acceptance.py](harness/validators/acceptance.py) — range check against [harness/acceptance_ranges.json](harness/acceptance_ranges.json).
- [harness/loc.py](harness/loc.py) — relevant-LOC computation.
- [harness/entropy.py](harness/entropy.py) — token-level Shannon entropy with `tiktoken` `cl100k_base`.
- [harness/_files.py](harness/_files.py) — shared file-enumeration helper used by loc.py + entropy.py.

**Image: multistage Dockerfile**
- `base` stage: Python 3.11 + Java 21 + Josh + opencode + scientific stack (everything common to agent and scorer).
- `agent` stage (`FROM base`): nothing further. Tagged `fortree:agent`. Used for the agent invocation; does NOT contain `/opt/harness/`.
- `scorer` stage (`FROM base`): `COPY harness/ /opt/harness/` + `COPY entrypoint-scorer.sh /opt/`. Tagged `fortree:scorer`. The acceptance ranges live inside `/opt/harness/` so the single COPY brings both validator code and ranges.
- `entrypoint-scorer.sh` rewritten as `exec python /opt/harness/run_metrics.py "$@"`.

**Validation gate**
- `docker build --target agent -t fortree:agent .` and `docker build --target scorer -t fortree:scorer .` both succeed.
- `! docker run --rm fortree:agent test -e /opt/harness` (structural separation: agent has no `/opt/harness/`).
- `docker run --rm fortree:scorer test -e /opt/harness/run_metrics.py` (positive structural check on the scorer).
- Smoke test: scorer against an empty workspace returns valid JSON with `did_run=false`, `csv_exists=false`, `relevant_loc=0`, `harness_errors=[]`.
- The phase-2a image gates still pass on `fortree:agent`.

### Phase 2b-fixtures — Static CSV fixtures *(after 2b-core)*

Exercises the scorer end-to-end against hand-crafted CSVs. No model implementation; each fixture is a tiny `run.sh` that emits a canned CSV to `./output/results.csv`.

- `reference/golden/run.sh` — happy path. Emits a small valid CSV (e.g. 3×3 grid × 11 years = 99 rows) with hand-picked values inside the v0 acceptance ranges (height ∈ [0, 11] m, occupancy ≈ 10).
- `reference/broken/schema/run.sh` — CSV with wrong column names. Expected: `csv_schema_ok=false`.
- `reference/broken/nan-heights/run.sh` — CSV with NaN in `meanHeight`. Expected: `csv_schema_ok=false` (per the NaN-as-schema-violation rule).
- `reference/broken/missing-year/run.sh` — CSV missing the 2034 row. Expected: `csv_schema_ok=false` via row-count check.
- `reference/broken/nan-precip/run.sh` — CSV with NaN in `precipitation`. Expected: `csv_schema_ok=false`.

Validation gates:
- Scorer on `reference/golden/` returns `did_run=true`, `height_in_range=true`, `harness_errors=[]`. `occupancy_in_range=false` is expected under the v0 schema: the validator computes `df.groupby("cell_id").size().mean()` filtered to year 10, which is trivially 1.0 (one row per cell at the target year) and falls outside the user-authored range `[9.9, 10.1]`. The mismatch surfaces a v0 design gap to resolve before agent runs — either redefine the occupancy metric, change the schema to carry tree-count, or relax the range.
- Scorer on each `reference/broken/*/` returns `csv_schema_ok=false` with a matching entry in `csv_schema_errors`, and `did_run=false`.

**Why before agents (entire phase 2)**: validates spec wording, acceptance numbers, and the entire scoring chain with zero LLM variance. If the scorer disagrees with a known-good fixture, no agent run is interpretable.

### Deferred (not on the critical path)

- Hand-written Mesa and Josh reference implementations. Useful as a sanity check that the spec is implementable and as a regression test for harness changes, but neither is needed to validate the LLM scoring workflow end-to-end. Revisit if pilot agent runs (phase 3+) reveal that an independent baseline would clarify a methodological question.

## Phase 3 — Single agent call, end-to-end

**Files to author**
- [prompts/rung1_minimal.md](prompts/rung1_minimal.md) and [prompts/rung5_master.md](prompts/rung5_master.md), derived from [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md). Rungs 2–4 deferred until pilot phase. Both include the fixed boilerplate footer (run.sh contract, CSV schema) per README's "Every prompt shares a fixed boilerplate footer".
- [config/models.yaml](config/models.yaml) — short-name → OpenRouter ID map per the README table (claude, gemma, kimi, minimax, mistral).
- [config/opencode.template.json](config/opencode.template.json) — rendered per run with `${OPENROUTER_API_KEY}`, `${RESOLVED_MODEL_ID}`, `${WORKSPACE}` substituted. Tool palette per README: `read`, `write` (workspace-scoped), `edit` (workspace-scoped), `glob`, `grep`, `bash` (whitelist: `./run.sh`, `ls`, `cat`, `head`, `tail`, `find`, `wc`, `tree`), and `WebFetch` with an explicit host allowlist (the documentation hosts from README's network-policy table plus `openrouter.ai`).
- [config/docs_categories.yaml](config/docs_categories.yaml) — URL→category map; used post-hoc by phase-5 manifest builder, but commit now.
- [orchestration/launch_run.sh](orchestration/launch_run.sh) — `docker run` the `fortree` image with `--env-file .env`, bind-mounting `./runs/<run-id>/` into `/sandbox`, invoking opencode against the rendered config. Captures opencode's trajectory log to `./runs/<run-id>/trajectory.jsonl`.

**Validation gate**
- `MODEL=claude RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) ./orchestration/launch_run.sh` produces a workspace containing a Mesa Python module + `run.sh`. Manual eyeball: prompt rendered, OpenRouter call succeeded, files present, trajectory log captured.
- Feed that workspace to the scorer (`docker run --network=none ... fortree:latest /opt/entrypoint-scorer.sh --target mesa`); end-to-end agent→score works.
- Repeat with `TARGET=josh` to exercise the Josh path.

This is the de-risk gate: prove a single (model, rung, target) cell can be driven prompt → opencode → workspace → scorer → metric record without any sandboxing layer in the way.

## Phase 4 — Observation layer, local parallelism, durable upload, CI + local-LLM path

**Files to author**

*Observation + parallelism + upload (as before)*
- [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf) — dnsmasq config with query logging enabled; runs as a sidecar container on a per-run docker bridge network.
- Update [orchestration/launch_run.sh](orchestration/launch_run.sh): create a per-run docker network, start the dnsmasq sidecar, point the agent container at it via `--dns`, copy the DNS log to `./runs/<run-id>/dns.log` on teardown. **Also**: after the scorer step, invoke `orchestration/upload_run.sh` to archive the run dir.
- [orchestration/launch_batch.sh](orchestration/launch_batch.sh) — fan out N concurrent `launch_run.sh` invocations with fresh `RUN_ID`s via `xargs -P` or GNU `parallel`. Each run gets its own bridge network, sidecar, and workspace dir.
- [orchestration/upload_run.sh](orchestration/upload_run.sh) — uploads every file under `runs/<RUN_ID>/` to `${MINIO_BUCKET}/${BATCH_TAG}/<RUN_ID>/<relative-path>` via the configured S3-compatible endpoint. The orchestrator runs `mc alias set` from the `MINIO_*` env vars at the top of the script, then `mc cp --recursive` from the run dir. Target backend is the existing GCS bucket via its S3 interoperability API (`MINIO_ENDPOINT=https://storage.googleapis.com`); no MinIO server runs anywhere — `mc` is just the client. Paths under the run dir are preserved so the remote layout mirrors the local layout. Upload is opportunistic: on failure, log to stderr, record `upload_status=failed` in `run_meta.json`, leave the local copy intact, and do NOT fail `launch_run.sh`.
- [Dockerfile](Dockerfile) — install `mc` (single static binary from `dl.min.io`, sha256-pinned) in the `base` stage so both agent and scorer images can call it.
- [config/VERSIONS.md](config/VERSIONS.md) — pin the `mc` version (sha256).
- [.env.example](.env.example) — add `MINIO_ENDPOINT=https://storage.googleapis.com`, `MINIO_BUCKET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `BATCH_TAG`. Required from Phase 4 onward; supersedes the placeholder `RESULTS_BUCKET` from earlier README drafts. `MINIO_*` naming is for project consistency — the bucket itself is GCS via S3 interop, not a MinIO server.

*CI + local-LLM (Ollama) path*

Motivation: OpenRouter integration tests cost real money per run and gate replication behind a paid API; a reviewer wanting to inspect the methodology should be able to run an end-to-end agent test locally and in CI without an account or key. opencode 1.14.50 supports Ollama natively via its OpenAI-compatible wrapper — no new agent dependency, only config + a workflow.

- [.github/workflows/smoke.yml](.github/workflows/smoke.yml) — runs on every push and every PR to `dev`/`main`. Builds `fortree:scorer`, then for each fixture under `reference/golden/` and `reference/broken/*/`: bind-mounts into a temp dir, runs `/opt/entrypoint-scorer.sh --target mesa`, parses stdout JSON, asserts the expected `csv_schema_ok` value and a substring match in `csv_schema_errors[0]`. Fully deterministic, no model. ~3–5 min wall-clock dominated by the cached docker build.
- [.github/workflows/integration-ollama.yml](.github/workflows/integration-ollama.yml) — `workflow_dispatch` only. Inputs: `model` (default `ollama-qwen-coder-7b`), `rung` (default `5`), `target` (default `mesa`). Steps: build `fortree:agent` + `fortree:scorer`, start `ollama/ollama:latest` as a service container, `docker exec ollama ollama pull qwen2.5-coder:7b`, run `MODEL=$model RUNG=$rung TARGET=$target OLLAMA_HOST=http://ollama:11434 ./orchestration/launch_run.sh`, score the workspace, render a Markdown report via `orchestration/generate_run_report.py`, upload `runs/$RUN_ID/` as a workflow artifact. Expected wall-clock 5–10 min.
- [orchestration/generate_run_report.py](orchestration/generate_run_report.py) — reads a finished `runs/<RUN_ID>/` directory and emits a single Markdown `report.md`. Sections: run metadata (model/target/rung/wall_time), the full rendered prompt inside a `<details>` block, a workspace inventory with each file's content (`<details>`-wrapped, truncated to ~2 KB; full files are in the artifact tarball), trajectory summary as an ordered list of tool calls with their inputs, the last assistant text, and a scorer-results table. The canonical artifact for reviewers wanting to inspect what a model produced without re-running.
- [config/opencode.template.json](config/opencode.template.json) — add an `ollama` entry to the `provider` block alongside `openrouter` (`{ "options": { "baseURL": "{env:OLLAMA_HOST}/v1" } }`). opencode auto-selects the provider from the model slug's prefix (`ollama/...` → ollama). No orchestrator change.
- [config/models.yaml](config/models.yaml) — add ollama-prefixed short names: `ollama-qwen-coder-7b: ollama/qwen2.5-coder:7b` and `ollama-qwen-coder-1_5b: ollama/qwen2.5-coder:1.5b` (fallback for resource-constrained environments). Underscore inside numeric tokens because YAML coerces `1.5` to float.
- [.env.example](.env.example) — add `OLLAMA_HOST=http://localhost:11434` (default for host-local ollama; CI overrides to `http://ollama:11434`).
- [README.md](README.md) — "Running the integration test locally" subsection covering: install ollama, `ollama pull qwen2.5-coder:7b`, `MODEL=ollama-qwen-coder-7b ... ./orchestration/launch_run.sh`, and platform-specific `OLLAMA_HOST` overrides (`host.docker.internal` for Docker Desktop on macOS, sidecar container on Linux).

**Validation gate**
- A single agent run produces a non-empty `dns.log` containing the allowlisted host(s) the agent reached during code generation.
- A deliberately-broken opencode config that lets the agent reach `stackoverflow.com` produces a `dns.log` entry for it — confirms the tripwire fires.
- `./orchestration/launch_batch.sh --model claude --rung 5 --target josh --runs 4` runs four concurrent agent invocations to completion, each with its own isolated workspace and DNS log.
- After a single agent run, `mc ls $alias/${MINIO_BUCKET}/${BATCH_TAG}/<RUN_ID>/` lists every file from the local `runs/<RUN_ID>/` directory.
- A run with deliberately-broken HMAC credentials records `upload_status=failed` in `run_meta.json` and the run itself still completes (upload is opportunistic, not gating).
- `gh workflow run smoke.yml` (or pushing any branch) completes green; all five fixture assertions pass.
- `gh workflow run integration-ollama.yml -f model=ollama-qwen-coder-1_5b -f rung=5 -f target=mesa` completes regardless of the `did_run` outcome, produces an artifact bundle including `report.md`, and `harness_errors=[]` in the scorer record.
- Local-dev regression: `MODEL=ollama-qwen-coder-7b RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) ./orchestration/launch_run.sh` against a host ollama produces a populated workspace with no OpenRouter API key set.

## Phase 5 — Full 5-step orchestrated run, one cell

**Files to author**
- [prompts/recovery_template.md](prompts/recovery_template.md) — Markdown skeleton with these placeholders, filled by `render_recovery_prompt.py`:
  - `{{ORIGINAL_RUNG_PROMPT}}` — the same rung-N body the agent originally received.
  - `{{ORIGINAL_TARGET_DIRECTIVE}}` — the same "Implement this using Mesa / the Josh DSL" line.
  - `{{BINARY_OUTCOMES_BLOCK}}` — a Markdown block built from the step-3 scorer JSON, carrying only the binary / structural fields: `did_run`, `exit_code`, `timed_out`, `csv_exists`, `csv_schema_ok`, `csv_schema_errors` (the exact validator messages, which already name columns and row counts), and `stderr_tail` (truncated to ~500 chars — the agent's own runtime errors are fair feedback).
  - `{{SIDECAR}}` — same SIDECAR footer as the original prompt, unchanged.
  - **Explicitly excluded** (per EXPERIMENTAL_DESIGN.md "does not include the acceptance ranges or any new information about correctness criteria"): `height_year10_mean`, `occupancy_year10_mean`, `height_in_range`, `occupancy_in_range`, `acceptance_ranges_used`, `src_loc`, `comment_loc`, `imports_loc`, `entropy_bits`.
- [orchestration/render_recovery_prompt.py](orchestration/render_recovery_prompt.py) — strict whitelist over the scorer JSON. Asserts the expected `schema_version`, copies only the named fields through. Anything new added to scorer.json in the future does NOT automatically leak into recovery prompts.
- [harness/conformance.py](harness/conformance.py) — step 2 mechanical check: greps for `import mesa` / `from mesa` / Mesa base-class instantiation on Mesa runs; for Josh, looks for `*.josh` files, `*.jshd`, and `josh parse` exit zero on the produced files.
- [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py) — optional, gated by `SKIP_FUZZY_CONFORMANCE`; deferable but stub it so the manifest schema is complete.
- [harness/docs_log.py](harness/docs_log.py) — joins opencode's trajectory log (WebFetch URLs) with the dnsmasq DNS log to produce the `docs_*` metric fields per README's metrics table, categorized via `config/docs_categories.yaml`.
- [results/manifest.jsonl](results/manifest.jsonl) — empty, committed; the orchestrator appends per-run JSON records.
- Update [orchestration/launch_run.sh](orchestration/launch_run.sh) to implement all five steps end-to-end and append the manifest row. The recovery flow:
  1. Step 3 scorer runs (already in scope from Phase 3).
  2. If `did_run=true AND height_in_range=true AND occupancy_in_range=true` → record `recovery_attempted=false`, skip steps 4-5.
  3. Else: render the recovery prompt via `render_recovery_prompt.py` into `runs/<RUN_ID>/recovery_prompt.md`.
  4. Invoke opencode a second time against the **same workspace** (the agent sees its prior implementation exactly as it left it, plus the recovery prompt). Record trajectory to `runs/<RUN_ID>/trajectory_recovery.jsonl`, stderr to `agent_stderr_recovery.log`.
  5. Re-run the scorer container against the post-recovery workspace, writing to `runs/<RUN_ID>/scorer_recovery.json`.
  6. Manifest row records both step-3 and step-5 outcomes side by side (`oneshot_*` and `recovery_*` field families).

**Validation gate (end-to-end)**
1. `MODEL=claude RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) ./orchestration/launch_run.sh`.
2. Inspect the appended row in [results/manifest.jsonl](results/manifest.jsonl): every metric listed in README's metrics table populated, including `target_conformance`, `oneshot_did_run`, `oneshot_height_in_range`, `oneshot_occupancy_in_range`, `recovery_attempted`, `prompt_tokens`, `completion_tokens`, `relevant_loc_oneshot`, `entropy_bits_oneshot`, `docs_*`, `wall_time_seconds`.
3. Force the recovery path: run a deliberately weakened rung1 prompt that almost-certainly fails one-shot validation; confirm `recovery_attempted=true` and `recovery_*` fields populate.
4. Grep gate on the rendered recovery prompt: `runs/<RUN_ID>/recovery_prompt.md` contains no occurrence of `height_year10_mean`, `occupancy_year10_mean`, `height_in_range`, `occupancy_in_range`, `acceptance_ranges_used`, `src_loc`, `entropy_bits` — confirms the strict whitelist in `render_recovery_prompt.py` holds.
5. `scorer_recovery.json` carries the same `schema_version` as the step-3 `scorer.json`.
6. Run once with `TARGET=josh` to exercise the Josh runner path through the full flow.

## Verification (end-of-plan acceptance)

The plan is complete when:
- `./orchestration/launch_run.sh` runs to completion for at least three cells: (claude, rung5, mesa), (claude, rung5, josh), and a recovery-triggering (claude, rung1, mesa).
- Each writes a manifest row with all metric fields populated.
- All artifacts (workspace, opencode trajectory log, dnsmasq DNS log, scorer JSON, manifest row) are recoverable post-hoc from `./runs/<run-id>/`.
- The static fixtures under `reference/golden/` and `reference/broken/*/` still produce the expected scorer outcomes (regression check that no harness change broke the scorer).

## Critical files to be created (summary)

Phase 1: [Dockerfile](Dockerfile), [scripts/install_josh.sh](scripts/install_josh.sh), [entrypoint-scorer.sh](entrypoint-scorer.sh), [config/requirements.txt](config/requirements.txt), [config/VERSIONS.md](config/VERSIONS.md), `.env`, `.gitignore`, README "Host prerequisites" section.

Phase 2: [prompts/SIDECAR.md](prompts/SIDECAR.md), [harness/acceptance_ranges.json](harness/acceptance_ranges.json), [harness/run_metrics.py](harness/run_metrics.py), [harness/runner.py](harness/runner.py), [harness/validators/output_schema.py](harness/validators/output_schema.py), [harness/validators/acceptance.py](harness/validators/acceptance.py), [harness/loc.py](harness/loc.py), [harness/entropy.py](harness/entropy.py), [harness/_files.py](harness/_files.py), `reference/golden/`, `reference/broken/{schema,nan-heights,missing-year,nan-precip}/`.

Phase 3: [prompts/rung1_minimal.md](prompts/rung1_minimal.md), [prompts/rung5_master.md](prompts/rung5_master.md), [config/models.yaml](config/models.yaml), [config/opencode.template.json](config/opencode.template.json), [config/docs_categories.yaml](config/docs_categories.yaml), [orchestration/launch_run.sh](orchestration/launch_run.sh).

Phase 4: [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf), [orchestration/launch_batch.sh](orchestration/launch_batch.sh), [orchestration/upload_run.sh](orchestration/upload_run.sh), [orchestration/generate_run_report.py](orchestration/generate_run_report.py), updates to [orchestration/launch_run.sh](orchestration/launch_run.sh), [Dockerfile](Dockerfile) (mc install), [config/VERSIONS.md](config/VERSIONS.md) (mc pin), [.env.example](.env.example) (MinIO / HMAC envs + `OLLAMA_HOST`), [config/opencode.template.json](config/opencode.template.json) (ollama provider entry), [config/models.yaml](config/models.yaml) (ollama-prefixed short names), [.github/workflows/smoke.yml](.github/workflows/smoke.yml), [.github/workflows/integration-ollama.yml](.github/workflows/integration-ollama.yml).

Phase 5: [prompts/recovery_template.md](prompts/recovery_template.md), [orchestration/render_recovery_prompt.py](orchestration/render_recovery_prompt.py), [harness/conformance.py](harness/conformance.py), [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py), [harness/docs_log.py](harness/docs_log.py), [results/manifest.jsonl](results/manifest.jsonl).

## Deferred to subsequent plans (explicitly out of scope)

- Prompt rungs 2–4 (write during the pilot phase, after rung1+rung5 prove the template).
- `orchestration/collect_results.py` (pilot tooling).
- Hard-layer egress enforcement (OpenShell policy, mitmproxy, or equivalent). Phases 1–5 rely on opencode's tool allowlists plus the dnsmasq tripwire.
- [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) and [LICENSE](LICENSE) — referenced by README but not on the critical path for phases 1–5.
- The headline 150-generation sweep — needs post-pilot prompt freeze and a tagged batch.

## Open items the user owns

- Provide `OPENROUTER_API_KEY` (used from phase 3 onward).
- Author [harness/acceptance_ranges.json](harness/acceptance_ranges.json) before phase 3 begins (technically before any agent runs). _Initial v0 ranges committed in PR #3._
- (Deferred) Decide whether the experiment needs hand-coded Mesa / Josh reference implementations as an independent baseline. Not on the phase 2 critical path; revisit after pilot agent runs.

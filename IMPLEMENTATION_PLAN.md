# Implementation plan — Build out the ForeverTree LLM experiment harness (phases 1–5)

## Context

[README.md](README.md) describes an experiment harness for measuring how prompt-detail affects LLM-generated code on a fixed task (ForeverTree), comparing a constrained DSL target (Josh) against a general framework (Mesa). The README is aspirational: only itself, [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md) (the ForeverTree spec content), and the two netCDF climate inputs under [data/](data/) currently exist. ~95% of the repo listed in the README's "Repository layout" must be built.

We will stair-step the build so that each phase produces a demonstrable artifact that validates the foundation for the next phase, rather than building monolithically and running once at the end. **Scope of this plan is phases 1–5 only** — env bootstrap through a single successful 5-step orchestrated run for one (model × rung × target) cell. The pilot sweep and headline sweep get their own subsequent plans, after pilot data is in hand and prompts can be frozen.

Decisions already made:
- **Version pins.** Keep README's Python 3.11 / OpenJDK 17 inside Docker. The host's own Python/Java versions are irrelevant — nothing scientific runs on the host.
- **Sandbox.** OpenShell is load-bearing. No plain-Docker fallback — block until OpenShell installs cleanly.
- **Acceptance ranges.** User authors [spec/acceptance_ranges.json](spec/acceptance_ranges.json) themselves and commits before any agent runs.
- **All tooling lives inside Docker images.** The host needs *only* Docker and bash. `uv`, `opencode`, `openshell`, the Josh CLI, Python, Java, and the scientific stack are installed into images, never onto the host. The host orchestrator is a thin shell script that does `docker run` and reads/writes files in a workspace directory bind-mounted into the containers.

## Branching workflow

- `main` — stable. Only merges from `dev` after batches of phases land.
- `dev` — integration branch. Phase feature branches PR into `dev`.
- `phase-N-<short-name>` — one branch per phase (e.g. `phase-1-env-bootstrap`). Closes via PR into `dev` once the phase's validation gate passes.

## Phase 1 — Env bootstrap (Docker images, zero host installs)

**Host work**
- Confirm `docker --version` works.
- Create `.env` containing `OPENROUTER_API_KEY=...`; add `.env` to `.gitignore`.
- Pin and document version numbers for: opencode, openshell, Josh CLI. The user signs off on these before the images are built.

**Build [Dockerfile.scorer](Dockerfile.scorer)**
- Base: `python:3.11-slim`.
- Add OpenJDK 17.
- Install pinned [config/requirements.txt](config/requirements.txt): mesa, numpy, pandas, scipy, xarray, netCDF4, rasterio, tiktoken (for entropy.py).
- Install the pinned Josh CLI (jar + wrapper script on PATH).
- No agent tooling — this image must run `--network=none`.
- [entrypoint-scorer.sh](entrypoint-scorer.sh) dispatches to `harness/run_metrics.py`.
- `docker build -f Dockerfile.scorer -t fortree-scorer .`.

**Build [Dockerfile.sandbox](Dockerfile.sandbox)**
- Same base as scorer (3.11 + JDK 17 + Josh CLI + requirements.txt) so reference and agent code execute identically.
- Plus: `uv`, `opencode`, `openshell`, all installed via `uv tool install` inside the image build.
- Container entrypoint takes a flag `--sandbox=on|off`:
  - `off` (used in phase 3): just `opencode run` against the bind-mounted workspace.
  - `on` (used in phases 4+): first `openshell sandbox create --policy /policy/openshell-policy.yaml`, then `openshell exec` opencode under it.
- `docker build -f Dockerfile.sandbox -t fortree-sandbox .`.
- The orchestrator launches this image with `docker run` plus whatever caps OpenShell needs for Landlock/seccomp/userns (likely `--cap-add` rather than `--privileged`; pinned in the orchestrator script). Phase 4 surfaces this — if OpenShell needs caps the host can't grant, that is the "block until OpenShell works" moment we agreed on.

Both images build in phase 1. The OpenShell *behavior* (policy enforcement, network proxy) is what waits until phase 4 — the *image* containing OpenShell is ready earlier so phase 3 can use it in `--sandbox=off` mode.

**Validation gate (phase 1 closes when all pass)**
- `docker build -f Dockerfile.scorer -t fortree-scorer .` succeeds.
- `docker build -f Dockerfile.sandbox -t fortree-sandbox .` succeeds.
- `docker run --rm fortree-scorer python -c "import mesa, numpy, pandas, scipy, xarray, netCDF4, rasterio, tiktoken; print('ok')"` prints `ok`.
- `docker run --rm fortree-scorer josh --version` prints a pinned version.
- `docker run --rm fortree-sandbox opencode --version` and `docker run --rm fortree-sandbox openshell --version` both print pinned versions.
- `docker run --rm --network=none fortree-scorer python -c "print('offline')"` confirms the scorer runs with no network — that is its production posture.
- `.env` exists, is gitignored, and `docker run --rm --env-file .env fortree-scorer printenv OPENROUTER_API_KEY` returns a value. (This validates the secret-passing pattern phases 3+ depend on.)

**Why first**: phases 2–5 all need the scorer image. The sandbox image's OpenShell behaviour is deferred to phase 4 because OpenShell-in-Docker may need iteration on cap-adds, and phase 2's scorer-only loop should not be blocked by that.

## Phase 2 — Scorer-only loop (no agents)

**Spec/data files to author**
- [spec/ForeverTree.md](spec/ForeverTree.md) — copy of `prompts/BASE_PROMPT.md`, repurposed as the canonical spec (the prompt file is for agents; the spec is for humans/the harness).
- [spec/harness_contract.md](spec/harness_contract.md) — defines what generated code must produce: `./run.sh`, `./output/results.csv` columns (`cell_id, lat, lon, year, meanAge, meanHeight, temperature, precipitation`), units, schema.
- [spec/acceptance_ranges.json](spec/acceptance_ranges.json) — **user provides**. Plan holds a placeholder file marked `TODO: user-authored` until that lands.
- [spec/environment_sidecar.md](spec/environment_sidecar.md) — the env description shown to the agent in phase 3+.

**Harness code to author**
- [harness/run_metrics.py](harness/run_metrics.py) — top-level scorer entry point; invokes runner, validators, computes loc + entropy, emits JSON record.
- [harness/runners/josh_runner.py](harness/runners/josh_runner.py), [harness/runners/mesa_runner.py](harness/runners/mesa_runner.py) — invoke `./run.sh` for each target; capture exit code, stdout, stderr, wall time.
- [harness/validators/output_schema.py](harness/validators/output_schema.py) — CSV existence + column shape check.
- [harness/validators/acceptance.py](harness/validators/acceptance.py) — range check against `spec/acceptance_ranges.json`.
- [harness/loc.py](harness/loc.py) — relevant-LOC computation (strip comments, blanks, boilerplate, imports; identical rules per target).
- [harness/entropy.py](harness/entropy.py) — token-level Shannon entropy with a fixed BPE tokenizer (e.g. `tiktoken` `cl100k_base` pinned).

**Reference implementations (test fixtures, never agent training data)**
- `reference/josh/` — hand-written `.josh` + `.jshd` for ForeverTree.
- `reference/mesa/` — hand-written Mesa 3.x module.
- Default authorship: Claude writes both in the execution session unless the user objects.

**Validation gate**
- `docker run --rm -v reference/mesa:/sandbox fortree-scorer` → `did_run=true`, height/occupancy in range.
- Same for `reference/josh`.
- Deliberately broken variants (wrong CSV schema, NaN heights, missing years, NaN-only precip) flip the expected bool flags. The scorer is the system under test here, not the references.

**Why before agents**: validates spec wording, acceptance numbers, netCDF→grid→CSV alignment, and the entire scoring chain with zero LLM variance. If the scorer disagrees with hand-written correct code, no agent run is interpretable.

## Phase 3 — Single agent call, NO sandbox

**Files to author**
- [prompts/rung1_minimal.md](prompts/rung1_minimal.md) and [prompts/rung5_master.md](prompts/rung5_master.md), derived from [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md). Rungs 2–4 deferred until pilot phase. Both include the fixed boilerplate footer (run.sh contract, CSV schema) per README's "Every prompt shares a fixed boilerplate footer".
- [config/models.yaml](config/models.yaml) — short-name → OpenRouter ID map per the README table (claude, gemma, kimi, minimax, mistral).
- [config/opencode.template.json](config/opencode.template.json) — rendered per run with `${OPENROUTER_API_KEY}`, `${RESOLVED_MODEL_ID}`, `${WORKSPACE}` substituted.
- [config/docs_categories.yaml](config/docs_categories.yaml) — URL→category map; used post-hoc by phase-5 manifest builder, but commit now.
- [orchestration/launch_run.sh](orchestration/launch_run.sh) — minimal version: `docker run` the `fortree-sandbox` image *without* engaging the OpenShell layer (entrypoint is invoked with `--sandbox=off`). Container renders opencode.json, calls `opencode run`, writes into a host-bind-mounted workspace under `./runs/<run-id>/`. No code runs on the host — only `docker run` invocations.

**Validation gate**
- `MODEL=claude RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) ./orchestration/launch_run.sh` produces a workspace containing a Mesa Python module + `run.sh`. Manual eyeball: prompt rendered, OpenRouter call succeeded, files present.
- Feed that workspace to `fortree-scorer` from phase 2; end-to-end agent→score works.
- Repeat with `TARGET=josh` to exercise the Josh path.

**Why before sandboxing**: separates auth/prompt/opencode failures from OpenShell failures. OpenShell debugging is much harder; do not compound problem surfaces.

## Phase 4 — Single agent call, INSIDE OpenShell sandbox

**Files to author**
- [config/openshell-policy.yaml](config/openshell-policy.yaml) — Landlock fs mounts (RO `/usr`, `/lib`, `/etc`; RW `/sandbox`, `/tmp`); unprivileged sandbox user; seccomp baseline; `network_policies` entries for each documentation host listed in the README's network-policy table plus `openrouter.ai`.
- [docs/INDEX.md](docs/INDEX.md) — pre-built navigation entry points to whitelisted hosts. Bind-mounted into the sandbox container's workspace.
- Update [orchestration/launch_run.sh](orchestration/launch_run.sh) to flip the `fortree-sandbox` entrypoint to `--sandbox=on`, bind-mount the policy file to `/policy/openshell-policy.yaml`, and add the required `--cap-add` flags (likely `SYS_ADMIN` for userns or alternatively `--security-opt seccomp=unconfined`; pin once empirically determined).
- Capture the OpenShell access log out of the container at run end (bind-mount its log dir or copy on exit).

**Image rebuild**: only if the openshell version pin changes — `Dockerfile.sandbox` itself was already built in phase 1.

**Validation gate**
- Inside-sandbox `touch /etc/test` fails; `touch /sandbox/scratch` succeeds.
- `curl joshsim.org` succeeds; `curl github.com` fails; `curl openrouter.ai` succeeds.
- Re-run the phase-3 prompt inside the sandbox. Same workspace shape emerges. OpenShell access log captures the doc fetches.

**Host resource constraint**: do not run agent sandbox + scorer container concurrently. Tear down agent before invoking scorer.

## Phase 5 — Full 5-step orchestrated run, one cell

**Files to author**
- [prompts/recovery_template.md](prompts/recovery_template.md) — recovery-phase prompt skeleton with placeholders for the binary/structural failure surface from step 3.
- [orchestration/render_recovery_prompt.py](orchestration/render_recovery_prompt.py) — fills the template from the step-3 JSON record. Strict: surfaces only the binary outcomes, never the acceptance numbers (README "Does not include the acceptance ranges or any new information about correctness criteria").
- [harness/conformance.py](harness/conformance.py) — step 2 mechanical check: greps for `import mesa` / `from mesa` / Mesa base-class instantiation on Mesa runs; for Josh, looks for `*.josh` files, `*.jshd`, and `josh parse` exit zero on the produced files.
- [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py) — optional, gated by `SKIP_FUZZY_CONFORMANCE`; deferable but stub it so the manifest schema is complete.
- [harness/docs_log.py](harness/docs_log.py) — parses the OpenShell access log into the `docs_*` metric fields per README's metrics table, joining with `config/docs_categories.yaml`.
- [results/manifest.jsonl](results/manifest.jsonl) — empty, committed; the orchestrator appends per-run JSON records.
- Update [orchestration/launch_run.sh](orchestration/launch_run.sh) to implement all five steps end-to-end and append the manifest row.

**Validation gate (end-to-end)**
1. `MODEL=claude RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) ./orchestration/launch_run.sh`.
2. Inspect the appended row in [results/manifest.jsonl](results/manifest.jsonl): every metric listed in README's metrics table populated, including `target_conformance`, `oneshot_did_run`, `oneshot_height_in_range`, `oneshot_occupancy_in_range`, `recovery_attempted`, `prompt_tokens`, `completion_tokens`, `relevant_loc_oneshot`, `entropy_bits_oneshot`, `docs_*`, `wall_time_seconds`.
3. Force the recovery path: run a deliberately weakened rung1 prompt that almost-certainly fails one-shot validation; confirm `recovery_attempted=true` and `recovery_*` fields populate.
4. Run once with `TARGET=josh` to exercise the Josh runner path through the full flow.

## Verification (end-of-plan acceptance)

The plan is complete when:
- `./orchestration/launch_run.sh` runs to completion for at least three cells: (claude, rung5, mesa), (claude, rung5, josh), and a recovery-triggering (claude, rung1, mesa).
- Each writes a manifest row with all metric fields populated.
- All artifacts (workspace, opencode trajectory log, OpenShell access log, scorer JSON, manifest row) are recoverable post-hoc from `./runs/<run-id>/`.
- The reference implementations under `reference/{josh,mesa}/` still pass scoring (regression check that no harness change broke the scorer).

## Critical files to be created (summary)

Phase 1: [Dockerfile.scorer](Dockerfile.scorer), [Dockerfile.sandbox](Dockerfile.sandbox), [entrypoint-scorer.sh](entrypoint-scorer.sh), sandbox entrypoint script (handles `--sandbox=on|off`), [config/requirements.txt](config/requirements.txt), `.env`, `.gitignore`.

Phase 2: [spec/ForeverTree.md](spec/ForeverTree.md), [spec/acceptance_ranges.json](spec/acceptance_ranges.json), [spec/harness_contract.md](spec/harness_contract.md), [spec/environment_sidecar.md](spec/environment_sidecar.md), [harness/run_metrics.py](harness/run_metrics.py), [harness/runners/josh_runner.py](harness/runners/josh_runner.py), [harness/runners/mesa_runner.py](harness/runners/mesa_runner.py), [harness/validators/output_schema.py](harness/validators/output_schema.py), [harness/validators/acceptance.py](harness/validators/acceptance.py), [harness/loc.py](harness/loc.py), [harness/entropy.py](harness/entropy.py), `reference/josh/`, `reference/mesa/`.

Phase 3: [prompts/rung1_minimal.md](prompts/rung1_minimal.md), [prompts/rung5_master.md](prompts/rung5_master.md), [config/models.yaml](config/models.yaml), [config/opencode.template.json](config/opencode.template.json), [config/docs_categories.yaml](config/docs_categories.yaml), [orchestration/launch_run.sh](orchestration/launch_run.sh).

Phase 4: [config/openshell-policy.yaml](config/openshell-policy.yaml), [docs/INDEX.md](docs/INDEX.md), updates to [orchestration/launch_run.sh](orchestration/launch_run.sh).

Phase 5: [prompts/recovery_template.md](prompts/recovery_template.md), [orchestration/render_recovery_prompt.py](orchestration/render_recovery_prompt.py), [harness/conformance.py](harness/conformance.py), [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py), [harness/docs_log.py](harness/docs_log.py), [results/manifest.jsonl](results/manifest.jsonl).

## Deferred to subsequent plans (explicitly out of scope)

- Prompt rungs 2–4 (write during the pilot phase, after rung1+rung5 prove the template).
- `orchestration/launch_batch.sh`, `orchestration/collect_results.py` (pilot tooling).
- [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) and [LICENSE](LICENSE) — referenced by README but not on the critical path for phases 1–5.
- The headline 150-generation sweep — needs off-host infra, post-pilot prompt freeze, and a tagged batch.

## Open items the user owns

- Provide `OPENROUTER_API_KEY`.
- Author [spec/acceptance_ranges.json](spec/acceptance_ranges.json) before phase 3 begins (technically before any agent runs).
- Confirm or override the default assumption that Claude authors the hand-coded `reference/josh/` and `reference/mesa/` implementations.
- Pin the OpenShell version and Josh CLI version (Claude can propose latest-stable; user signs off).

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

**Config files to author**
- [harness/CONTRACT.md](harness/CONTRACT.md) — defines what generated code must produce: `./run.sh`, `./output/results.csv` columns (`cell_id, lat, lon, year, meanAge, meanHeight, temperature, precipitation`), units, schema. Harness-internal; not shown to the agent. The agent sees [prompts/environment_sidecar.md](prompts/environment_sidecar.md), which carries the same contract in agent-facing form.
- [harness/acceptance_ranges.json](harness/acceptance_ranges.json) — **user authors before this phase begins.** Read by `harness/validators/acceptance.py`.
- [prompts/environment_sidecar.md](prompts/environment_sidecar.md) — the boilerplate footer appended to every rung's prompt. Carries the runtime env description, the external-input data file paths/units, and the CSV output contract in agent-facing prose.

**Harness code to author**
- [harness/run_metrics.py](harness/run_metrics.py) — top-level scorer entry point; invokes runner, validators, computes loc + entropy, emits JSON record.
- [harness/runner.py](harness/runner.py) — invokes `./run.sh`; captures exit code, stdout, stderr, wall time. Single file, target-agnostic per Plan-agent design.
- [harness/validators/output_schema.py](harness/validators/output_schema.py) — CSV existence + column shape check.
- [harness/validators/acceptance.py](harness/validators/acceptance.py) — range check against [harness/acceptance_ranges.json](harness/acceptance_ranges.json).
- [harness/loc.py](harness/loc.py) — relevant-LOC computation (strip comments, blanks, boilerplate, imports; identical rules per target).
- [harness/entropy.py](harness/entropy.py) — token-level Shannon entropy with a fixed BPE tokenizer (e.g. `tiktoken` `cl100k_base` pinned).
- [harness/_files.py](harness/_files.py) — shared file-enumeration helper used by loc.py + entropy.py.

**Reference implementations (test fixtures, never agent training data)**
- `reference/josh/` — hand-written `.josh` + `.jshd` for ForeverTree.
- `reference/mesa/` — hand-written Mesa 3.x module.
- Default authorship: Claude writes both in the execution session unless the user objects.

**Validation gate**
- `docker run --rm --network=none -v reference/mesa:/sandbox fortree:latest /opt/entrypoint-scorer.sh --target mesa` → `did_run=true`, height/occupancy in range.
- Same for `reference/josh` (with `--target josh`).
- Deliberately broken variants (wrong CSV schema, NaN heights, missing years, NaN-only precip) flip the expected bool flags. The scorer is the system under test here, not the references.

**Why before agents**: validates spec wording, acceptance numbers, netCDF→grid→CSV alignment, and the entire scoring chain with zero LLM variance. If the scorer disagrees with hand-written correct code, no agent run is interpretable.

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

## Phase 4 — Observation layer and local parallelism

**Files to author**
- [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf) — dnsmasq config with query logging enabled; runs as a sidecar container on a per-run docker bridge network.
- Update [orchestration/launch_run.sh](orchestration/launch_run.sh): create a per-run docker network, start the dnsmasq sidecar, point the agent container at it via `--dns`, copy the DNS log to `./runs/<run-id>/dns.log` on teardown.
- [orchestration/launch_batch.sh](orchestration/launch_batch.sh) — fan out N concurrent `launch_run.sh` invocations with fresh `RUN_ID`s via `xargs -P` or GNU `parallel`. Each run gets its own bridge network, sidecar, and workspace dir.

**Validation gate**
- A single agent run produces a non-empty `dns.log` containing the allowlisted host(s) the agent reached during code generation.
- A deliberately-broken opencode config that lets the agent reach `stackoverflow.com` produces a `dns.log` entry for it — confirms the tripwire fires.
- `./orchestration/launch_batch.sh --model claude --rung 5 --target josh --runs 4` runs four concurrent agent invocations to completion, each with its own isolated workspace and DNS log.

## Phase 5 — Full 5-step orchestrated run, one cell

**Files to author**
- [prompts/recovery_template.md](prompts/recovery_template.md) — recovery-phase prompt skeleton with placeholders for the binary/structural failure surface from step 3.
- [orchestration/render_recovery_prompt.py](orchestration/render_recovery_prompt.py) — fills the template from the step-3 JSON record. Strict: surfaces only the binary outcomes, never the acceptance numbers (README "Does not include the acceptance ranges or any new information about correctness criteria").
- [harness/conformance.py](harness/conformance.py) — step 2 mechanical check: greps for `import mesa` / `from mesa` / Mesa base-class instantiation on Mesa runs; for Josh, looks for `*.josh` files, `*.jshd`, and `josh parse` exit zero on the produced files.
- [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py) — optional, gated by `SKIP_FUZZY_CONFORMANCE`; deferable but stub it so the manifest schema is complete.
- [harness/docs_log.py](harness/docs_log.py) — joins opencode's trajectory log (WebFetch URLs) with the dnsmasq DNS log to produce the `docs_*` metric fields per README's metrics table, categorized via `config/docs_categories.yaml`.
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
- All artifacts (workspace, opencode trajectory log, dnsmasq DNS log, scorer JSON, manifest row) are recoverable post-hoc from `./runs/<run-id>/`.
- The reference implementations under `reference/{josh,mesa}/` still pass scoring (regression check that no harness change broke the scorer).

## Critical files to be created (summary)

Phase 1: [Dockerfile](Dockerfile), [scripts/install_josh.sh](scripts/install_josh.sh), [entrypoint-scorer.sh](entrypoint-scorer.sh), [config/requirements.txt](config/requirements.txt), [config/VERSIONS.md](config/VERSIONS.md), `.env`, `.gitignore`, README "Host prerequisites" section.

Phase 2: [prompts/environment_sidecar.md](prompts/environment_sidecar.md), [harness/CONTRACT.md](harness/CONTRACT.md), [harness/acceptance_ranges.json](harness/acceptance_ranges.json), [harness/run_metrics.py](harness/run_metrics.py), [harness/runner.py](harness/runner.py), [harness/validators/output_schema.py](harness/validators/output_schema.py), [harness/validators/acceptance.py](harness/validators/acceptance.py), [harness/loc.py](harness/loc.py), [harness/entropy.py](harness/entropy.py), [harness/_files.py](harness/_files.py), `reference/josh/`, `reference/mesa/`.

Phase 3: [prompts/rung1_minimal.md](prompts/rung1_minimal.md), [prompts/rung5_master.md](prompts/rung5_master.md), [config/models.yaml](config/models.yaml), [config/opencode.template.json](config/opencode.template.json), [config/docs_categories.yaml](config/docs_categories.yaml), [orchestration/launch_run.sh](orchestration/launch_run.sh).

Phase 4: [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf), [orchestration/launch_batch.sh](orchestration/launch_batch.sh), updates to [orchestration/launch_run.sh](orchestration/launch_run.sh).

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
- Confirm or override the default assumption that Claude authors the hand-coded `reference/josh/` and `reference/mesa/` implementations.

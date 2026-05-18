# Implementation plan — Build out the ForeverTree LLM experiment harness (phases 1–5)

## Context

[README.md](README.md) describes an experiment harness for measuring how prompt-detail affects LLM-generated code on a fixed task (ForeverTree), comparing a constrained DSL target (Josh) against a general framework (Mesa). The README is aspirational: only itself, [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md) (the ForeverTree spec content), and the two netCDF climate inputs under [data/](data/) currently exist. ~95% of the repo listed in the README's "Repository layout" must be built.

We will stair-step the build so that each phase produces a demonstrable artifact that validates the foundation for the next phase, rather than building monolithically and running once at the end. **Scope of this plan is phases 1–5 only** — env bootstrap through a single successful 5-step orchestrated run for one (model × rung × target) cell. The pilot sweep and headline sweep get their own subsequent plans, after pilot data is in hand and prompts can be frozen.

The plan validates the end-to-end opencode path first under opencode's own tool allowlists plus passive observation (trajectory log + DNS tripwire). **Updated during Phase 4b**: a hard egress allowlist *was* added (kernel-enforced via dnsmasq + iptables + ipset on a per-run sidecar that shares its netns with the agent — see [Dockerfile.dnsmasq](Dockerfile.dnsmasq) and [orchestration/sidecar-init.sh](orchestration/sidecar-init.sh)). The "OpenShell or equivalent" mention is historical context only.

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
              --network=container:dnsmasq-<run-id>
              fortree:agent /opt/agent-entrypoint.sh
            (agent shares the sidecar's network namespace; the sidecar
             enforces a kernel-level egress allowlist via
             dnsmasq + iptables + ipset. opencode's webfetch + bash
             tool allowlists are still the in-process policy layer.)
- scorer:   docker run --rm --network=none
              -v ./runs/<run-id>:/sandbox
              fortree:scorer /opt/entrypoint-scorer.sh --target <josh|mesa>
- sidecar:  docker run -d --cap-add NET_ADMIN
              --add-host=host.docker.internal:host-gateway
              fortree:dnsmasq
            (built from Dockerfile.dnsmasq, alpine + iptables/ipset;
             one per agent run; lifecycle in orchestration/dns_sidecar.sh)
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
- Scorer on `reference/golden/` returns `did_run=true`, `height_in_range=true`, `occupancy_in_range=true`, `harness_errors=[]`. (The v0 occupancy gap — validator counting rows instead of trees — was resolved by adding an `nTrees` column to the SIDECAR schema; `acceptance.py` now reads `year_df["nTrees"].mean()` directly.)
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

**Status**: split into sub-phases during execution; bottom of this section tracks each.

- **4a (CI + Ollama path)** — *complete*, merged via PR #9 / #14.
- **4b (Observation: dnsmasq sidecar)** — *complete*, merged via PR #10. Originally scoped as passive logging; extended in PR #13 to **hard egress enforcement** (kernel-level iptables+ipset allowlist; see Architecture note above).
- **Mid-phase polish that landed alongside 4a/4b** — *complete*:
  - PR #11: report generator rewrite (opencode export + Jinja2 template, results-first layout, scorer table) — see [orchestration/generate_run_report.py](orchestration/generate_run_report.py), [orchestration/templates/report.md.j2](orchestration/templates/report.md.j2).
  - PR #12: SIDECAR self-test contract — agent must `chmod +x run.sh` AND run it once before declaring done.
  - PR #15: workflow publishes `report.md` to `$GITHUB_STEP_SUMMARY` so the report renders inline in the GH run UI.
  - PR #16: merged `integration-ollama.yml` + an OpenRouter path into one `integration.yml`; provider dispatch lives in [.github/scripts/setup-provider.sh](.github/scripts/setup-provider.sh).
  - PR #17: idle watcher switched from `trajectory.jsonl` size to opencode session-DB mtime (catches sub-agent activity, which the trajectory stream doesn't reflect).
- **4c (Local parallelism: `launch_batch.py`)** — *complete*. The docker-compose option was considered and skipped: per-run lifecycle was already idempotent in [orchestration/dns_sidecar.sh](orchestration/dns_sidecar.sh) and [orchestration/launch_run.sh](orchestration/launch_run.sh), so compose would have been rewrite churn for no behavioural win. Instead, [orchestration/launch_cell.sh](orchestration/launch_cell.sh) factors out the agent → scorer → report sequence that CI used to inline (so the local batch driver and CI run the same code path), and [orchestration/launch_batch.py](orchestration/launch_batch.py) drives fan-out via `concurrent.futures.ThreadPoolExecutor`. Two CLI forms: `--model M --rung R --target T --runs N` (single cell × N replicates, matches the gate phrasing below) and `--cells cells.csv` (matrix from CSV). Batch metadata lives in a sibling `runs/<batch-tag>/` dir holding `worklist.tsv`, `joblog.tsv`, `manifest.jsonl`, `summary.txt`, and `cell-logs/<run_id>.log` per cell. Orphan-resource pre-sweep handles SIGKILL'd-prior-batch recovery so a hard kill of the driver is non-fatal. The orchestrator host needs Python 3.11+ and `pyyaml`; for SSH-deploy the [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json) provides Python 3.11 + docker-in-docker out of the box.
- **4d (Durable upload: `upload_run.sh` + `mc` → GCS via S3 interop)** — *pending*.

The original "Files to author" sections below describe the **planned** scope; mark of completion notes the deltas from what actually landed.

**Files to author**

*Observation + parallelism + upload (as before)*
- ✓ [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf) — query logging + `ipset=` directives populating the kernel allowlist (openrouter.ai, mesa.readthedocs.io, joshsim.org, python.org, numpy.org, scipy.org, pandas.pydata.org, xarray.dev, unidata.github.io, readthedocs.io). *Landed in PR #10, extended for enforcement in PR #13.*
- ✓ Sidecar implementation grew beyond the original "one config file" scope:
  - [Dockerfile.dnsmasq](Dockerfile.dnsmasq) — alpine + dnsmasq + iptables + ipset; the sidecar image is built separately from the main Dockerfile to keep the multistage inheritance chain clean.
  - [orchestration/sidecar-init.sh](orchestration/sidecar-init.sh) — creates ipset, installs iptables OUTPUT rules (default DROP + carve-outs for lo/conntrack/upstream-DNS/bridge-gateway/allowlist), execs dnsmasq.
  - [orchestration/dns_sidecar.sh](orchestration/dns_sidecar.sh) — `start`/`stop` subcommands managing the per-run network + sidecar lifecycle; blocks on the sidecar's HEALTHCHECK before returning so the agent can't race a half-initialized firewall.
  - [agent-entrypoint.sh](agent-entrypoint.sh) — wraps `opencode run` + `opencode export`; traps SIGTERM so the idle/wall-clock watcher can capture a session export before the container dies.
- ✓ [orchestration/launch_run.sh](orchestration/launch_run.sh): per-run network + sidecar start/stop trap, DNS log captured to `./runs/<run-id>/dns.log` on teardown. Agent container joins the sidecar's netns via `--network=container:dnsmasq-<id>` (not `--dns`, which docker forbids alongside container netns sharing). Upload integration is **pending — phase 4d**.
- ✓ [orchestration/launch_cell.sh](orchestration/launch_cell.sh) — chains agent → scorer → report into one unit. Same env contract as `launch_run.sh`. Each step uses `set +e` + status capture in `run_meta.cell.json` so an agent failure doesn't block scoring, a scorer failure doesn't block report. Exit code reflects the highest-numbered failing step (10/11/12 for agent/scorer/report).
- ✓ [orchestration/launch_batch.py](orchestration/launch_batch.py) — Python orchestrator (argparse + `ThreadPoolExecutor`). Validates `MODEL`/`RUNG`/`TARGET` against `config/models.yaml` before spinning any docker resources; pre-sweep cleans orphan `fortree-run-*` networks + `dnsmasq-*` containers from prior SIGKILL'd batches; per-cell stdout+stderr captured to `cell-logs/<run_id>.log` so concurrent cells don't interleave on the operator's terminal; post-sweep aggregates each run's `run_meta.json` + `run_meta.cell.json` + `scorer.json` into `manifest.jsonl` (worklist-ordered, not completion-ordered). Operator UX is a Rich live panel showing each in-flight cell's lifecycle phase (`AGENT → SCORE → REPORT → DONE`, derived by polling the run dir for which artifacts exist), with a progress bar + ETA, a per-cell ✔/✗ scroll above the panel, and inline failure log tails. Live area auto-disables in non-TTY contexts (CI, `tee`, redirects) so logs stay clean.
- ✓ [.devcontainer/devcontainer.json](.devcontainer/devcontainer.json) — Python 3.11 + docker-in-docker, so deploying the orchestrator to a fresh SSH host is "clone + reopen in container" rather than `apt install` chores.
- ✓ docker-compose was **considered and skipped**. Per-run lifecycle was already idempotent (named per-`RUN_ID` resources, EXIT-trap teardown in `launch_run.sh`, healthcheck-gated sidecar start in `dns_sidecar.sh`), so compose would have been rewrite churn for no behavioural win. The pre-sweep cleanup handles the only remaining failure mode (SIGKILL of the parent skipping EXIT traps).
- ⏳ **[Phase 4d, pending]** [orchestration/upload_run.sh](orchestration/upload_run.sh) — uploads every file under `runs/<RUN_ID>/` to `${MINIO_BUCKET}/${BATCH_TAG}/<RUN_ID>/<relative-path>` via the configured S3-compatible endpoint. The orchestrator runs `mc alias set` from the `MINIO_*` env vars at the top of the script, then `mc cp --recursive` from the run dir. Target backend is the existing GCS bucket via its S3 interoperability API (`MINIO_ENDPOINT=https://storage.googleapis.com`); no MinIO server runs anywhere — `mc` is just the client. Paths under the run dir are preserved so the remote layout mirrors the local layout. Upload is opportunistic: on failure, log to stderr, record `upload_status=failed` in `run_meta.json`, leave the local copy intact, and do NOT fail `launch_run.sh`.
- ⏳ **[Phase 4d, pending]** [Dockerfile](Dockerfile) — install `mc` (single static binary from `dl.min.io`, sha256-pinned) in the `base` stage so both agent and scorer images can call it.
- ⏳ **[Phase 4d, pending]** [config/VERSIONS.md](config/VERSIONS.md) — pin the `mc` version (sha256).
- ⏳ **[Phase 4d, pending]** [.env.example](.env.example) — add `MINIO_ENDPOINT=https://storage.googleapis.com`, `MINIO_BUCKET`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `BATCH_TAG`. (`OLLAMA_HOST` row already landed in 4a.) `MINIO_*` naming is for project consistency — the bucket itself is GCS via S3 interop, not a MinIO server.

*CI + local-LLM (Ollama) path*

Motivation: OpenRouter integration tests cost real money per run and gate replication behind a paid API; a reviewer wanting to inspect the methodology should be able to run an end-to-end agent test locally and in CI without an account or key. opencode 1.14.50 supports Ollama natively via its OpenAI-compatible wrapper — no new agent dependency, only config + a workflow.

- ✓ [.github/workflows/smoke.yml](.github/workflows/smoke.yml) — two jobs: `scorer-fixtures` (asserts fixture outcomes) + `firewall-probe` (busybox in sidecar netns, asserts allow vs reject for 4 hosts). Both deterministic, no model. Fixture-assertion logic in [.github/scripts/smoke-fixtures.sh](.github/scripts/smoke-fixtures.sh); probe in [.github/scripts/firewall-probe.sh](.github/scripts/firewall-probe.sh).
- ✓ [.github/workflows/integration.yml](.github/workflows/integration.yml) (renamed from `integration-ollama.yml` in PR #16) — `workflow_dispatch` only; one workflow, two provider paths. Provider dispatch in [.github/scripts/setup-provider.sh](.github/scripts/setup-provider.sh): `ollama-*` short-name → start ollama, pull model; anything else → write `OPENROUTER_API_KEY` from repo secret. After agent run: score, render `report.md`, **publish to `$GITHUB_STEP_SUMMARY`** for inline GH-UI viewing, upload `runs/<id>/` as artifact.
- ✓ [orchestration/generate_run_report.py](orchestration/generate_run_report.py) — rewritten in PR #11 around `opencode export` (normalized session JSON) + a Jinja2 template ([orchestration/templates/report.md.j2](orchestration/templates/report.md.j2)). Layout is results-first: verdict card + tool-call counts at top, workspace files biggest-first, final assistant text, scorer table, diagnostics (full prompt + per-call tool detail + DNS log summary) collapsed at bottom. All opaque content rendered in HTML-escaped `<pre>` to prevent markdown bleed-through.
- ✓ [config/opencode.template.json](config/opencode.template.json) — both `openrouter` and `ollama` provider blocks. The ollama entry uses `npm: @ai-sdk/openai-compatible` + an explicit `models` map (opencode's catalog at `models.dev` doesn't list local ollama, so an explicit declaration is required).
- ✓ [config/models.yaml](config/models.yaml) — five OpenRouter entries + two ollama entries (`ollama-qwen-coder-7b`, `ollama-qwen-coder-1_5b`).
- ✓ [.env.example](.env.example) — `OLLAMA_HOST` row added; MINIO_* rows still pending until phase 4d.
- ✓ [README.md](README.md) — "Running the integration test locally" subsection + CI subsection covering both workflow paths.

**Validation gate**
- ✓ A single agent run produces a non-empty `dns.log` containing the allowlisted host(s) the agent reached during code generation. *(Verified in workflow run 26001455587 and locally.)*
- ✓ **Stronger version actually built**: a kernel-level firewall, not just a tripwire — `firewall-probe` CI job asserts `openrouter.ai` + `mesa.readthedocs.io` connect; `pypi.org` + `stackoverflow.com` REJECT. Reaches into the sidecar's `ipset` and iptables counters to prove enforcement. *(Verified green in every smoke run since PR #13.)*
- ✓ `./orchestration/launch_batch.py --model claude --rung 5 --target josh --runs 4 --jobs 4` runs four concurrent cells (agent + scorer + report) to completion. Each `runs/<RUN_ID>/` contains workspace + `scorer.json` + `report.md` + `dns.log`; `runs/<batch-tag>/joblog.tsv` shows 4 rows with their exit codes; `manifest.jsonl` carries 4 lines with model/rung/target + full scorer payload; `cell-logs/<run_id>.log` captures per-cell output.
- ⏳ **[Phase 4d]** After a single agent run, `mc ls $alias/${MINIO_BUCKET}/${BATCH_TAG}/<RUN_ID>/` lists every file from the local `runs/<RUN_ID>/` directory.
- ⏳ **[Phase 4d]** A run with deliberately-broken HMAC credentials records `upload_status=failed` in `run_meta.json` and the run itself still completes (upload is opportunistic, not gating).
- ✓ `gh workflow run smoke.yml` (or pushing any branch) completes green; both `scorer-fixtures` and `firewall-probe` jobs pass.
- ✓ `gh workflow run integration.yml -f model=claude -f rung=5 -f target=mesa` completes, agent exits 0, `report.md` lands in the Summary tab. *(Verified after PR #17's heartbeat fix — runs no longer false-stall on sub-agent activity.)*
- ✓ Local-dev regression: `MODEL=ollama-qwen-coder-7b RUNG=5 TARGET=mesa RUN_ID=$(uuidgen) ./orchestration/launch_run.sh` against a host ollama produces a populated workspace with no OpenRouter API key set.

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

Phase 4: [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf), [orchestration/launch_cell.sh](orchestration/launch_cell.sh), [orchestration/launch_batch.py](orchestration/launch_batch.py), [orchestration/upload_run.sh](orchestration/upload_run.sh), [orchestration/generate_run_report.py](orchestration/generate_run_report.py), updates to [orchestration/launch_run.sh](orchestration/launch_run.sh), [Dockerfile](Dockerfile) (mc install), [config/VERSIONS.md](config/VERSIONS.md) (mc pin), [.env.example](.env.example) (MinIO / HMAC envs + `OLLAMA_HOST`), [config/opencode.template.json](config/opencode.template.json) (ollama provider entry), [config/models.yaml](config/models.yaml) (ollama-prefixed short names), [.github/workflows/smoke.yml](.github/workflows/smoke.yml), [.github/workflows/integration.yml](.github/workflows/integration.yml).

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

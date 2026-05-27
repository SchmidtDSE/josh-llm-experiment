# Implementation plan — engineering build state

Engineering-side state of the ForeverTree LLM experiment harness:
what's built, what's in flight, and what remains. For the experimental
methodology (hypothesis, run flow, threats to validity), see
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md). For the scoring axes,
metric definitions, LLM-judge spec, re-analysis recipe, and open
scoring questions, see [SCORING.md](SCORING.md). For installation and
how to run, see [README.md](README.md).

Per-PR detail lives in `git log` and the merged PR descriptions; this
document is a navigation map, not a complete change history.

> **Status (2026-05-26, on `feat/k8s-refactor`).** Phase 6 (k8s
> refactor) is in flight — see §Phase 6 below. **PRs 1–6 merged** on the
> integration branch (scoring drop + regression bands, prompt update for
> 100×100, image consolidation, egress relaxation + sidecar drop, k8s
> submission path + mirror-sidecar + in-Pod fuzzy judge + devcontainer,
> repo cleanup). PR6 (#46) also folded in two devcontainer/render fixups,
> and the post-PR6 workflow was smoke-validated end-to-end on
> `sonnet × {josh, mesa}` (batch `pr6-smoke-20260526`; both cells ran,
> conformed, β≈1.0 / R²≈0.9999). PR7 (headline batch) and PR8 (merge to
> `dev`) follow. A third experiment target, `josh-mcp`, was built on
> this branch after PR6 (see §The josh-mcp arm) and folds into the
> headline panel. The integration branch is now 70+ commits ahead of
> `dev`. The pre-Phase-6 local-orchestration architecture
> (per-cell shell scripts + dnsmasq sidecar + host-side report
> renderers) is preserved in git history; see PRs #34/#36/#40/#41/#45
> for the migration steps.

## Architecture

```
fortree base image (Dockerfile):
├── Python 3.11 + scientific stack (mesa, numpy, pandas, scipy,
│                                   xarray, netCDF4, rasterio, tiktoken)
├── Eclipse Temurin 21 JRE
├── /usr/local/bin/josh             ← wrapper around joshsim-fat.jar
└── /usr/local/bin/opencode         ← pinned 1.14.50

stages:
- fortree:agent   — base + agent-entrypoint.sh + baked synthetic
                    climate netCDFs + run.sh seed; no harness/, no mc
- fortree:scorer  — base + /opt/harness/ + /opt/scorer-and-upload.sh
                    + in-Pod judge assets + mc

per-cell k8s Job (one Pod):
1. agent initContainer (fortree:agent) — agent-entrypoint.sh invokes
   `opencode run` 8× against /sandbox/PLAN.md, populating
   /cell-data/workspace/.
2. mirror-sidecar (fortree:scorer, restartPolicy: Always) — continuously
   `mc mirror`s /cell-data to the bucket for OOM forensics.
3. scorer container (fortree:scorer) — scorer-and-upload.sh runs the
   harness against the workspace, runs run-judge.sh for the in-Pod
   Q1/Q2/Q3 LLM judge, then `mc mirror`s the completed /cell-data
   tree to the bucket. Egress is monitored (trajectory.jsonl), not
   enforced.

host (devcontainer-friendly):
└── pixi env exposes: get-jars, render, apply, pull, rehydrate,
    aggregate, lab.
```

Pinned versions in [config/VERSIONS.md](config/VERSIONS.md). Python
deps in [config/requirements.txt](config/requirements.txt). Host
deps in [pixi.toml](pixi.toml).

## Build history (compressed)

Each phase's detail is preserved in `git log` and the merged PR
descriptions. The summary below is the navigation map.

| Phase | Scope | PRs |
|---|---|---|
| **1** | Environment bootstrap — single Docker image with Python 3.11, JDK 21, Josh CLI sha256-pinned, opencode 1.14.50 | #2 |
| **2a** | JRE 17→21; BASE_PROMPT bbox / year pin; SIDECAR boilerplate; v0 acceptance ranges | #3 |
| **2b** | Multistage Dockerfile (base/agent/scorer); scoring harness; static fixtures under `reference/`; smoke CI | #4, #5, #7, #8 |
| **3** | Single-agent end-to-end (rung prompts retired in 5c); `launch_run.sh`; opencode template | #6 |
| **4a** | CI workflows (smoke + integration); Ollama path for key-free CI | #9, #14 |
| **4b** | dnsmasq sidecar + kernel-level egress enforcement; firewall-probe smoke job | #10, #13 |
| **4b-polish** | `generate_run_report.py` (Jinja2); SIDECAR self-test contract; idle watcher on opencode session-DB mtime | #11, #12, #15, #16, #17 |
| **4c** | Local parallelism — `launch_cell.sh` + `launch_batch.py` (Rich live panel, orphan cleanup); per-batch manifests | #19, #20, #21–24 |
| **4d** | Durable upload — host-side `upload_batch.sh` (mc), `launch_batch.py --upload` auto-invoke. *Replaced by in-pod upload in Phase 6 PR3+.* | (merged) |
| **5a** | Post-pilot scoring revision: scorer JSON `phase5a-v1`; conformance + internal_consistency modules; synthetic CF-1.8 climate netCDFs | #25, #26, #27 |
| **5b** | Recovery-loop hypothesis — **retired**; folded into 5c's todos 5–8 | (retired) |
| **5c** | Multi-invocation planning flow — agent runs opencode 8× per cell against shared `/sandbox/PLAN.md`; `prompts/steps/`; per-step session exports; permissive cell-identity schema; `.jshd` LOC bugfix; batch-report multi-invocation diagnostics | (merged) |
| **6 (in flight)** | k8s refactor + scoring simplification — see §Phase 6 below | #34/#36/#40/#41 (PRs 1–4 merged), 5–7 pending |

## Phase 6 — k8s refactor (in flight)

Refactor unifying three coupled scope buckets: drop the local-
orchestration tree, move to per-cell k8s Jobs, and simplify the
scorer to the headline-relevant axes only.

### TL;DR

| Bucket | Before (phase 5c) | After (phase 6) |
|---|---|---|
| **Scoring** | 4 axes incl. growth-rate / age-step / nTrees consistency block; scorer runs the agent's `./run.sh` once for 11 sim years | 3 axes — *target conformance*, *ecology* (year-100 height + occupancy + observed-vs-predicted regression), *style* (LOC / imports / entropy); scorer runs 100 replicates × 100 sim years for real wall-clock |
| **Infra** | Per-cell dnsmasq+iptables sidecar in shared netns; local Docker daemon; host-side `launch_batch.py` driver | Per-cell k8s Job (one Pod, `fortree:agent` initContainer → `fortree:scorer` main container); egress *monitored* not enforced; the scorer container uploads artefacts via `mc` |
| **Repo** | `orchestration/` carries batch driver, per-cell wrapper, report renderers, rescore tooling, `upload_batch.sh` | `orchestration/` reduced to a Job manifest renderer + local `mc` download helper; reports + rescore tooling deleted; `analysis/` is the only local-workflow surface |

### Scoring simplification

PR1 (merged) implemented this bucket.

**Kept axes:**
- **Target conformance** (mechanical) — `josh validate` exit zero on
  `.josh` files, or `import mesa` + Mesa class subclassing on Mesa runs.
- **Ecology** — headline gate is now an `observed ~ predicted`
  regression against the spec-faithful reference simulator
  ([`data/reference_sim.py`](data/reference_sim.py)) run on the
  committed synthetic climate netCDFs. β/α/R² targets derived from
  physics. Secondary mean-band check kept as a coarse sanity gate.
- **Style** — `src_loc`, `comment_loc`, `imports_loc`, `entropy_bits`.
  Reported even when the cell fails to run.
- **Wall-clock** — promoted to a *headline measurement* under the
  100×100 contract. The agent's `./run.sh` carries preprocess +
  `--replicates 100` × 100 simulated years.
- **Schema gate** — precondition for ecology metrics.

**Dropped:**
- Internal-consistency block ([`harness/internal_consistency.py`](harness/internal_consistency.py)
  was deleted in PR1) — diagnostic during methodology-building,
  redundant once regression β catches the same failure modes.
- `target_conformance_fuzzy` schema placeholder — replaced by the new
  fuzzy-judge Q1+Q2+Q3 (Q3 lands in PR2).

**Acceptance bands derivation chain** (set up in PR1):

```
data/generate_synthetic_climate.py   # writes the netCDFs (committed)
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

Math primitives are factored into [`harness/spec_model.py`](harness/spec_model.py)
and imported by both `reference_sim.py` (band derivation) and
`harness/validators/acceptance.py` (gate enforcement) — single source
of truth, cannot drift.

### K8s execution

PR3–5 implement this bucket.

The original blocker for cloud execution was the per-run
**dnsmasq+iptables egress sidecar** — two containers in a shared
network namespace with `CAP_NET_ADMIN`, which only k8s (or raw
Docker) can express. Relaxing egress from *enforced* to *monitored*
removes that constraint and lets the cell run as a vanilla k8s Pod.

**Target Pod shape** (one k8s Job per cell):

```
initContainers:
  - name: agent
    image: fortree:agent
    # Runs opencode 8-step flow against /workspace.
    # Does NOT carry harness/, acceptance_ranges.json, or mc.
    command: ["/opt/agent-entrypoint.sh"]
containers:
  - name: scorer
    image: fortree:scorer
    # Runs the scorer against the workspace the agent left behind,
    # then uploads everything to the bucket via mc. Has harness/,
    # acceptance_ranges.json, and mc; the agent never sees any of these.
    command: ["/opt/scorer-and-upload.sh"]
```

The initContainer pattern is the right fit because the scorer needs
to wait for the agent's exit before running. **Image separation
stays** — the agent container does not carry `harness/`,
`acceptance_ranges.json`, or `mc`, so it literally cannot read the
scoring criteria or exfiltrate to the bucket. `mc` is added to
`fortree:scorer` only; bucket credentials are mounted into the
scorer container via a k8s Secret reference.

**Egress: monitored, not enforced.** opencode's `trajectory.jsonl`
records every `webfetch` URL the model invoked; that file is exported
into `/sandbox` and ends up in the bucket via the scorer's `mc
mirror`. The validity argument leans on it being sufficient post-hoc
evidence of what the agent reached. The DNS-log sidecar from earlier
pilots is dropped along with the kernel-level REJECT chain — the Pod
shape stays minimal, and the methodology delta is acknowledged in
[EXPERIMENTAL_DESIGN.md §Egress observability](EXPERIMENTAL_DESIGN.md).
If the headline batch surfaces concerns about indirect egress (an
agent-authored `run.sh` shelling out to `curl`/`urllib`), a Pod-level
`NetworkPolicy` or cluster-wide Cloud DNS logging can be added later
without reverting the rest of the refactor.

**Submission:** A small Python script renders one Job manifest per
cell from a matrix CSV (`model,target,replicates`), then `kubectl
apply -f -`s them. Cluster: **GKE Autopilot**, one **Indexed Job**
per batch (`completions=N, parallelism=K`, K capped to OpenRouter's
per-key concurrency).

### Repo cleanup

PR6 deleted the local-orchestration surface area in one sweep. 19
paths removed under `orchestration/`, `.github/`, and the repo root:

- **Local-orchestration shell scripts** — `launch_batch.py`,
  `launch_cell.sh`, `launch_run.sh`, `run_agent.sh`, `dns_sidecar.sh`,
  `sidecar-init.sh`
- **DNS sidecar** — `Dockerfile.dnsmasq`, `orchestration/dnsmasq.conf`
- **Report generators** — `generate_run_report.py`,
  `generate_batch_report.py` (replaced by [analysis/headline.ipynb](analysis/headline.ipynb))
- **Rescore tooling** — `rescore_batch.py`, `rescore_cell.sh`
  (replaced by a future re-score k8s Job)
- **Host-side upload** — `upload_batch.sh` (replaced by in-Pod
  `containers/scorer-and-upload.sh`)
- **Host-side fuzzy judge** — `run_fuzzy_judge.sh`, `_fuzzy_summary.py`
  (orphan); replaced by in-Pod [containers/run-judge.sh](containers/run-judge.sh)
- **Dead helpers** — `extract_time_breakdown.py`
- **Retired rung ladder** — `prompts/rungs/`
- **CI** — `.github/workflows/integration.yml` (built `Dockerfile.dnsmasq`
  and invoked `launch_cell.sh`; will be reintroduced when a CI-accessible
  k8s test cluster exists) + `.github/scripts/setup-provider.sh`
  (orphan with it)

**Plan corrections discovered during PR6:** the original delete table
incorrectly listed `harness/conformance_fuzzy.py` and
`orchestration/extract_transcript.py`. Both stay:
- `harness/conformance_fuzzy.py` is the live schema-shape stub that
  [harness/run_metrics.py:23](harness/run_metrics.py#L23) imports;
  its docstring was swept to point at `containers/run-judge.sh`.
- `orchestration/extract_transcript.py` is image-baked at
  [Dockerfile:135](Dockerfile#L135) and called by
  [containers/run-judge.sh:89](containers/run-judge.sh#L89) inside
  the scorer container. The
  [.github/workflows/build-images.yml](.github/workflows/build-images.yml)
  path filter already names it explicitly.

**Follow-on fixups (folded into PR6 during smoke-testing):**
- Devcontainer: the GKE auth-plugin install moved into
  [.devcontainer/post-create.sh](.devcontainer/post-create.sh); the
  `dhoeric/google-cloud-cli` feature's `installGkeGcloudAuthPlugin` was
  disabled because its v1.0.1 plugin step targets the stale legacy
  `google-cloud-sdk-*` package and silently no-ops against current gcloud.
- Render path: `uv run` → `python` in
  [orchestration/k8s_apply.sh](orchestration/k8s_apply.sh) (+ the
  `render_jobs.py` docstring) so `pixi run apply` works — `uv` isn't in
  the pixi devcontainer.
- Lock: regenerated [.devcontainer/devcontainer-lock.json](.devcontainer/devcontainer-lock.json)
  (the committed copy was missing the `kubectl-helm-minikube` entry).

**Smoke validation:** `sonnet × {josh, mesa}` ran end-to-end against
images built from the PR6 branch (batch `pr6-smoke-20260526`): both cells
`did_run`, `target_conformance: true`, regression β≈1.0 / R²≈0.9999,
fuzzy Q1 = yes. The only hiccup was a stale `openrouter-creds` Secret
(re-synced from `.env`), unrelated to the cleanup — confirming PR6
introduces no regression.

**Kept in `orchestration/`:** `render_jobs.py`, `k8s_apply.sh`,
`pull_artefacts.sh`, `resolve_model.py`, `_fuzzy_parse.py`,
`extract_transcript.py`, `templates/`, `matrix.csv`.

**Move + tidy:** ✓ landed in PR #44 (devcontainer extension). Five
container-entrypoint shell scripts plus `agent-run.sh.seed` moved to
[containers/](containers/); Dockerfile COPY paths updated, image-side
`/opt/<name>.sh` destinations unchanged, so runtime is a no-op.
[.github/workflows/build-images.yml](.github/workflows/build-images.yml)
path filter collapsed to a single `containers/**` glob.

**Surviving CI:**
- [.github/workflows/smoke.yml](.github/workflows/smoke.yml) — builds
  `fortree:scorer` and runs it against the reference fixtures on
  every push.
- [.github/workflows/build-images.yml](.github/workflows/build-images.yml)
  — builds + pushes `fortree-agent` and `fortree-scorer` to GHCR.

**What stays exactly the same:**
- The prompt rendering pipeline (rung body + target directive +
  SIDECAR + per-step injection) — only content changes, not shape.
- The 8-step multi-invocation flow — moves into the agent
  initContainer's entrypoint script untouched.
- The opencode pin + `config/opencode.template.json` shape.
- The five-model panel pin and OpenRouter slug resolution.
- The local `analysis/` workflow: pull bucket → pandas → notebook.

### The `josh-mcp` arm (third target)

A third experiment target, `josh-mcp`, lands on this branch after the
PR1–6 series. It generates Josh DSL exactly like `josh` but runs the
agent under a **constrained** opencode palette — no `bash`, with the
Josh pipeline reachable only through an MCP server. The *methodology*
(the environment factor, the L-shaped partial factorial, the three
contrasts, and the "never let `josh-mcp`↔`mesa` be the headline" rule)
lives in [EXPERIMENTAL_DESIGN.md §Targets](EXPERIMENTAL_DESIGN.md);
this section is the engineering build-state.

**MCP server (consumed, not built).** The Josh fat jar grows an `mcp`
stdio subcommand in [SchmidtDSE/josh#440](https://github.com/SchmidtDSE/josh/pull/440)
(base `dev`, MCP SDK 1.1.3), exposing four tools —
`validate_simulation`, `discover_config`, `preprocess_data`,
`run_simulation` (the last gained a `data` arg, a map of
`external-name.jshd → path`, in commit `6c1e5e9c`). opencode spawns it
via `command: ["josh", "mcp"]` — the wrapper forwards `mcp` to the jar
like any other subcommand. The tools surface to the agent as
`josh_validate_simulation`, `josh_preprocess_data`,
`josh_run_simulation`, `josh_discover_config`.

**The agent does not author `run.sh`.** Its deliverable is Josh source
(`simulation.josh`) plus the `.jshd` it builds via
`josh_preprocess_data`; it self-tests with `josh_run_simulation`
(2 replicates) since it has no shell to run `./run.sh`. The **scorer
materializes a canonical `run.sh`** from
[harness/run_josh_mcp.sh](harness/run_josh_mcp.sh) (baked into
`fortree:scorer`, copied over the workspace `run.sh` before the timed
run): `josh preprocess ×2` (`tasmax` as `K`, `pr` as `kg m-2 s-1`)
then `josh run --replicates N --data temperature.jshd=… --data
precipitation.jshd=…`. Those commands are byte-identical to the
agent's MCP self-test, so a green self-test predicts a green scoring
run. The `--data` key keeps the `.jshd` extension on purpose — it
selects the deserialization strategy; drop it and josh XZ-decodes a
plain `.jshd` and dies mid-sim. The harness `run.sh` only works if the
agent honors the naming convention the directive enforces
([prompts/targets/josh-mcp.md](prompts/targets/josh-mcp.md)): entry
`simulation.josh`, simulation `Main`, `external temperature` +
`precipitation`, in-model `× 31_536_000` precip→mm/yr, output via
`exportFiles.patch = "file:///sandbox/output/results_{replicate}.csv"`.
If it doesn't, the run fails → `did_run=false` (still an informative
cell — conformance and `josh validate` still run).

**Units are known facts, not discovered.** `tasmax`→`K`,
`pr`→`kg m-2 s-1` come straight from
[data/generate_synthetic_climate.py](data/generate_synthetic_climate.py);
the constrained agent reads them from the SIDECAR spec sheet (§AI
Inputs), not by poking the netCDFs — no dry run needed to learn them.

**Integration touch points (as built):**

| Where | Change |
|---|---|
| `config/opencode.josh-mcp.template.json` *(new)* | Constrained palette: `bash:false`, `webfetch:true`, `task:false`, `"josh*":true`, plus the `mcp.josh` local-server block (`timeout:120000`, `JAVA_TOOL_OPTIONS=-Xmx8g` to cap the MCP JVM). |
| [orchestration/render_jobs.py](orchestration/render_jobs.py) | `VALID_TARGETS=("josh","mesa","josh-mcp")`; `_render_opencode_json` selects the josh-mcp template; memory defaults moved to `request==limit` (agent 16Gi, scorer 24Gi) to avoid the `MaxRAMPercentage` burst-gap eviction. |
| `prompts/targets/josh-mcp.md` *(new)* | No-bash directive: drive Josh via `josh_*` MCP tools with absolute `/sandbox` paths; do not author `run.sh`; the naming convention above. |
| [prompts/SIDECAR.md](prompts/SIDECAR.md) §AI Inputs | Descriptive grid/coverage spec sheet (shared by all arms). |
| `harness/run_josh_mcp.sh` *(new, baked into scorer)* | The canonical run script (above). |
| [harness/run_metrics.py](harness/run_metrics.py) | `--target` += `josh-mcp`; copies `run_josh_mcp.sh` → workspace `run.sh` before `runner.run` when target is josh-mcp. |
| [harness/conformance.py](harness/conformance.py) | Dispatch `josh-mcp` → `_check_josh` (identical conformance keeps the contrast clean). |
| [harness/_files.py](harness/_files.py) | `_EXTENSIONS_BY_TARGET["josh-mcp"] = (".josh",)` so LOC/entropy work. |

`job.yaml.j2`, `scorer-and-upload.sh`, and the fuzzy judge need no
change — `target` flows through as a label and a `--target` arg, and
the judge reads the (now harness-canonical) `run.sh`, which still
carries the CLI calls.

**Image dependency: the agent jar must carry `mcp`.** `josh mcp` only
works if the image bundles a Josh build with the `McpCommand`
subcommand. #440 targets `dev`, which won't flow to `main` for a
while, so [scripts/install_josh.sh](scripts/install_josh.sh) and the
Dockerfile base stage **default to the rolling `dev` jar**
(`JOSH_JAR_URL`), pinned by `JOSH_JAR_SHA256`. The pin does double
duty: integrity (fail on mismatch) and **cache-busting** — the `RUN`
layer is cached by command, not by remote content, so without a
changing pin a rebuild silently keeps a stale jar (this bit us once: a
rebuild kept a km-grid-buggy jar). Bump the sha to move to a newer dev
build; `pixi run get-jars` ([scripts/get_jars.py](scripts/get_jars.py))
fetches dev+main into `jar/<branch>/` with sha256 sidecars for local
smoke / re-pinning. Pin back to `main` with
`--build-arg JOSH_JAR_URL=…/main/joshsim-fat.jar JOSH_JAR_SHA256=<main sha>`.

**Status.** The arm is wired and the pipeline is proven end-to-end on
the cluster: the harness `run.sh` produces `exit 0`, the full row
count, no XZ error, and runs in a few minutes at 100 replicates under
24Gi scorer memory with no OOM; the MCP toolchain runs clean
(validate/preprocess/run all complete, no bash, no tool timeouts). It
is staged for a mini-panel —
[orchestration/matrix-mini.csv](orchestration/matrix-mini.csv):
`{minimax, claude} × {josh, mesa, josh-mcp} × 5` = 30 cells — gated
behind a single-cell smoke
([orchestration/matrix-smoke.csv](orchestration/matrix-smoke.csv)).

**Known stale artifact:** [data/validate_synthetic_climate.py](data/validate_synthetic_climate.py)
still asserts the phase-5 31-year / 2024–2054 grid; the committed data
is 101 years (2024–2124) per the generator. It is a human/CI
maintenance script the agent never runs, so it does not affect runs,
but it should be refreshed before it is trusted as a spec-sheet
validator.

### Sequencing

**Branching strategy.** All PRs in this series land on the long-lived
integration branch **`feat/k8s-refactor`**, *not* directly on `dev`.
The integration branch was cut from `dev` once at the start of the
refactor and only merges back to `dev` after the full series has
been validated end-to-end. This keeps `dev` shippable while the
refactor is in flight — a partial state (e.g., scoring updated but
prompt + k8s submission not) would leave the local-orchestration
path broken on `dev` if landed there directly.

**PR order:**

1. **Scoring drop + regression bands** ✓ (merged, PR #34). Delete
   `harness/internal_consistency.py`, strip consistency fields from
   `run_metrics.py`, move acceptance to year-100, switch to
   `observed ~ predicted` regression gate, derive bands from
   spec-faithful reference simulator, bump `SCHEMA_VERSION` to
   `phase6-v1`. SCORING.md rewritten.
2. **Prompt update** ✓ (merged, PR #36 + follow-ups #37, #38).
   `run.sh` shape (preprocess + 100×100). Updated BASE_PROMPT /
   SIDECAR / relevant step files. Added fuzzy Q3 to
   `prompts/FUZZY_JUDGE.md`. Sub-tasks:
   - **Disambiguate the year-0 question.** PR1's offline sanity check
     against the phase-5c batches found a systematic +10% slope on
     Josh runs vs Mesa runs (β≈1.10 vs β≈1.00). The cause: Josh
     stdlib grows during step 0 (year-2024 row already shows
     `meanAge = 1`, post-growth), while Mesa implementations were
     treating step 0 as init-only against PR1's reference simulator
     (which originally skipped year 0). The fix in PR2 is to pin
     **"every year is a growth step, including 2024"** — matches
     Josh stdlib's natural behaviour and the spec's "per step, age
     and height change" reading. The reference simulator + validator
     are updated to sum predicted growth over *all* years (no
     skip-first-year filter). An N-year simulation gets N growth
     events. Whichever interpretation we pick has to match the
     reference, or the regression gate will systematically detect
     ±10% slope on the "wrong" framework.
   - 100-year sim: years 2024..2123 inclusive in the CSV (100 rows
     per cell per replicate), with growth between consecutive years.
   - `--replicates 100` (or framework equivalent) in `run.sh`.
   - Preprocess (`.jshd` build for Josh, netCDF→DataFrame for Mesa)
     inside the same `run.sh` so wall-clock includes data loading.
   - Fuzzy Q3 question wording asserts the above contract.
3. **Image consolidation** ✓ (merged, PR #40). `mc` added to
   `fortree:scorer`. New `scorer-and-upload.sh` wraps
   `run_metrics.py` + `mc mirror /sandbox` to the bucket. Trust
   boundary preserved (agent image has no `mc`). No behaviour
   change for the existing local orchestration (smoke-fixtures still
   hit `/opt/entrypoint-scorer.sh`).
4. **Egress relaxation + sidecar drop** ✓ (merged, PR #41).
   Stripped iptables/ipset from `Dockerfile.dnsmasq`; the image
   became a passive logger. Dropped the DNS-log sidecar from the
   target Pod shape entirely — `trajectory.jsonl` is the sole egress
   observation layer. `firewall-probe` smoke job retired.
   EXPERIMENTAL_DESIGN.md §Egress observability + §Threats to
   validity updated. (`Dockerfile.dnsmasq` itself was deleted in
   PR6.)
5. **K8s submission path.** ✓ merged (PR #45). Added
   `orchestration/templates/job.yaml.j2`,
   `orchestration/render_jobs.py`, `orchestration/k8s_apply.sh`,
   `orchestration/pull_artefacts.sh`. In-Pod fuzzy judge
   ([containers/run-judge.sh](containers/run-judge.sh)) folded in
   (PR #42). [containers/mirror-sidecar.sh](containers/mirror-sidecar.sh)
   continuously mirrors `/cell-data` so OOM'd cells still leave
   forensic state. Pixi-based devcontainer + `containers/` move
   landed alongside (PR #44). Mini-headline batch
   (`smoke-headline-20260521`, 100 cells) ran end-to-end on GKE
   Autopilot; artefacts present in the bucket.
6. **Repo cleanup.** ✓ merged (PR #46). Deleted 19 paths in the
   local-orchestration tree (per-cell shell scripts + dnsmasq sidecar
   + host-side report renderers + rescore tooling + host-side fuzzy
   judge + retired rung ladder + the `integration.yml` workflow that
   exercised the local path). README + EXPERIMENTAL_DESIGN.md +
   IMPLEMENTATION_PLAN.md swept. Also folds in two devcontainer/render
   fixups and was smoke-validated on `sonnet × {josh, mesa}`. See §Repo
   cleanup above for the full list, the two plan corrections
   (`conformance_fuzzy.py` and `extract_transcript.py` were kept, not
   deleted), the fixups, and the smoke result.
7. **Headline batch.** Submit the full panel as a k8s Indexed Job.
   Batch-tag suggestion: `headline-k8s-<date>`. This is the
   reportable batch — phase-5c artefacts are abandoned, not compared
   against. The panel now spans three targets (`josh`, `mesa`,
   `josh-mcp`); a held mini-panel (`matrix-mini.csv`, 30 cells)
   de-risks the josh-mcp plumbing before the full run.
8. **Merge `feat/k8s-refactor` → `dev`.** Final integration. Done
   only after PRs 1–7 have all landed on the integration branch,
   smoke CI is green on it, and the headline batch has produced the
   artefacts the paper will reference.

### Open questions

1. **Acceptance-band tolerance calibration.** PR1 derived the
   regression *targets* (β=1, α=0, R²→1) empirically from the
   spec-faithful reference simulator. The *tolerances* around those
   targets (β ∈ [0.95, 1.05], α ∈ [−0.5, 0.5], R² > 0.95) are
   currently hand-picked to give implementation tolerance. Once the
   headline batch produces a panel of agent runs, re-calibrate
   against their empirical spread. Tracked as SCORING.md Open
   methodology question #1.
2. **Per-model engagement probe.** With 100×100 the cost of a
   *succeeded* cell goes up materially. Worth a single-cell probe per
   model under the new prompt + workload before launching the
   headline batch, to surface "model fails to engage with the new
   run.sh contract" regressions cheaply. The PR6 smoke
   (`pr6-smoke-20260526`) covered `sonnet` on both targets — it engaged
   and scored clean — so this is done for sonnet; `gemma`, `kimi`,
   `minimax`, and `mistral` still want a probe each before the headline.
3. **`activeDeadlineSeconds` ceiling.** What's the right Pod-level
   timeout for 100×100? Empirically TBD; suggest first probe sets
   `activeDeadlineSeconds: 3600` and the headline batch tunes from
   there.

## Current state

Scorer JSON schema: `phase6-v1` (`harness/run_metrics.py:SCHEMA_VERSION`)
on `feat/k8s-refactor`; `phase5a-v1` on `dev`.

**Source of truth:** the GCS bucket. Per-cell artefacts land under
`<bucket>/<prefix>/<batch-tag>/<run-id>/` directly from the scorer
container's `mc mirror`. `pixi run pull <batch-tag>` syncs a batch
back to `runs/<batch-tag>/` for local analysis; `pixi run aggregate
runs/<batch-tag>` rolls it up into `analysis/aggregated.csv` for
[analysis/headline.ipynb](analysis/headline.ipynb).

**Per-cell artefacts** (under `<run-id>/` both in the bucket and
after `pixi run pull`):

- `workspace/` — agent-authored files (including `PLAN.md`)
- `prompt_body.md` — rendered shared body
- `trajectory.jsonl` — in-order concatenation of all 8 steps' opencode events
- `agent_stderr.log` — in-order concatenation of all 8 steps' stderr
- `agent_artifacts/session_export.json` — final attempted step's opencode export
- `agent_artifacts/steps/step_NN/` — per-step `trajectory.jsonl`, `agent_stderr.log`, `session_export.json`, `step_meta.json`
- `scorer.json` — full scoring record
- `scorer.fuzzy.json` — Q1/Q2/Q3 in-Pod judge output
- `transcript.md` — human-readable opencode transcript
- `run_meta.json` — orchestration metadata (model, target, image digests, timings)

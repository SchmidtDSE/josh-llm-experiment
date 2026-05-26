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

> **Status (2026-05-21, on `feat/k8s-refactor`).** Phase 6 (k8s
> refactor) is in flight — see §Phase 6 below. **PRs 1–4 merged** on
> the integration branch (scoring drop + regression bands, prompt
> update for 100×100, image consolidation, egress relaxation +
> sidecar drop). PRs 5–7 pending; final merge to `dev` happens once
> the full series is validated end-to-end. The historical phases
> below describe the codebase as it stood at the start of Phase 6;
> the Phase 6 "Repo cleanup" step deletes a substantial amount of
> that surface area.

## Architecture (transitional)

The diagram below describes the **as-of-start-of-Phase-6 shape**.
PRs 3 and 4 have already partially mutated this:
- the scorer image now also carries `mc` + `scorer-and-upload.sh`
  (PR3, for in-Pod uploads under PR5);
- the `fortree:dnsmasq` image is now a passive query logger — no
  `iptables`/`ipset`, no `CAP_NET_ADMIN`, no `sidecar-init.sh` — and
  the target Pod shape (Phase 6 §K8s execution) drops the sidecar
  entirely (PR4). Running the **local orchestration** path under
  `launch_run.sh` would now fail at the `sidecar-init.sh` step; that
  whole tree disappears in PR6's cleanup sweep.

The Phase 6 §K8s execution sub-section describes the target shape.

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
- fortree:scorer  — base + /opt/harness/ (scoring code +
                    spec_model.py + acceptance_ranges.json)
- fortree:dnsmasq — separate alpine image with iptables + ipset; the
                    egress-allowlist sidecar (Dockerfile.dnsmasq)

invocation pattern per cell (orchestrated by orchestration/launch_*.sh,
slated for deletion in PR6):
1. Bring up a per-run Docker bridge network.
2. Start `fortree:dnsmasq` on it with `--cap-add NET_ADMIN`;
   sidecar-init.sh writes the iptables OUTPUT rules.
3. Start `fortree:agent` joining the sidecar's netns
   (`--network=container:dnsmasq-<run-id>`); agent's egress is hard-
   filtered at the kernel. Inside the container, agent-entrypoint.sh
   invokes opencode **eight times in a row**, one per todo, against
   a shared `/sandbox/PLAN.md` working document.
4. After the multi-invocation chain finishes, run `fortree:scorer`
   against the workspace under `--network=none`.
5. Generate per-cell `report.md`. Tear down network + sidecar.
6. Per-batch driver aggregates all cells into `manifest.jsonl`.
```

Pinned versions in [config/VERSIONS.md](config/VERSIONS.md). Python
deps in [config/requirements.txt](config/requirements.txt). Host
deps in [pyproject.toml](pyproject.toml).

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

PR6 deletes the local-orchestration surface area in one sweep.

| Path | Reason |
|---|---|
| [orchestration/launch_batch.py](orchestration/launch_batch.py) | Replaced by k8s Job submission |
| [orchestration/launch_cell.sh](orchestration/launch_cell.sh) | Replaced by Pod spec |
| [orchestration/launch_run.sh](orchestration/launch_run.sh) | Replaced by Pod spec |
| [orchestration/run_agent.sh](orchestration/run_agent.sh) | Replaced by Pod initContainer + k8s `activeDeadlineSeconds` |
| [orchestration/dns_sidecar.sh](orchestration/dns_sidecar.sh) | DNS sidecar dropped entirely in PR4; `trajectory.jsonl` is the sole egress observation layer |
| [orchestration/sidecar-init.sh](orchestration/sidecar-init.sh) | iptables setup no longer needed (PR4 dropped the sidecar) |
| [Dockerfile.dnsmasq](Dockerfile.dnsmasq) | Sidecar dropped entirely; PR4 stripped iptables/ipset, PR6 deletes the file |
| [orchestration/dnsmasq.conf](orchestration/dnsmasq.conf) | Same — config for the now-retired DNS sidecar |
| [orchestration/generate_run_report.py](orchestration/generate_run_report.py) | Reports replaced by the headline notebook |
| [orchestration/generate_batch_report.py](orchestration/generate_batch_report.py) | Same |
| [orchestration/rescore_batch.py](orchestration/rescore_batch.py) | Replaced by re-score Job |
| [orchestration/rescore_cell.sh](orchestration/rescore_cell.sh) | Same |
| [orchestration/upload_batch.sh](orchestration/upload_batch.sh) | Upload happens inside the Pod's scorer container |
| [orchestration/run_fuzzy_judge.sh](orchestration/run_fuzzy_judge.sh) | Becomes a k8s Job that pulls completed cells from the bucket and writes back |
| [orchestration/extract_transcript.py](orchestration/extract_transcript.py) | Run inside the scorer container, not host-side |
| [orchestration/extract_time_breakdown.py](orchestration/extract_time_breakdown.py) | Same |
| [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py) | Placeholder; replaced by the real host/Job-side fuzzy judge |
| [prompts/rungs/](prompts/rungs/) | Rung ladder retired in phase 5c; the dir was already a vestige |

**Keep + repurpose:**

| Path | Repurpose |
|---|---|
| [orchestration/resolve_model.py](orchestration/resolve_model.py) | Used by the Job manifest renderer |
| [orchestration/templates/](orchestration/templates/) | New home for Pod / Job Jinja templates |
| New `orchestration/render_jobs.py` | Renders one Job per row of the matrix CSV |
| New `orchestration/k8s_apply.sh` | Thin `kubectl apply -f -` wrapper |
| New `orchestration/pull_artefacts.sh` | Local-side: `mc mirror` a batch from the bucket into `runs/<batch-tag>/` for analysis |
| [scripts/install_*.sh](scripts/) | Unchanged — image build still uses them |
| [analysis/aggregate.py](analysis/aggregate.py) | PR1 already dropped consistency columns; adds regression columns |
| [analysis/headline.ipynb](analysis/headline.ipynb) | Drop Panel C (consistency); rename Panel A "ecology" subplots to year-100 |

**Move + tidy:** ✓ landed early (with the devcontainer extension PR,
not deferred to PR6). The five container-entrypoint shell scripts plus
`agent-run.sh.seed` were `git mv`'d into
[containers/](containers/) — `containers/agent-entrypoint.sh`,
`containers/entrypoint-scorer.sh`, `containers/scorer-and-upload.sh`,
`containers/run-judge.sh`, `containers/mirror-sidecar.sh`,
`containers/agent-run.sh.seed`. Dockerfile COPY paths updated;
image-side `/opt/<name>.sh` destinations unchanged so runtime is a
no-op. [.github/workflows/build-images.yml](.github/workflows/build-images.yml)
path filter collapsed to a single `containers/**` glob.

**CI:**
- [.github/workflows/smoke.yml](.github/workflows/smoke.yml) — keep
  conceptually; firewall-probe job retired with the iptables sidecar
  in PR4.
- [.github/workflows/integration.yml](.github/workflows/integration.yml)
  — rewrite to submit a single k8s Job to a test cluster (or skip
  if the test cluster isn't free), instead of the current Docker
  Compose-shaped flow.

**What stays exactly the same:**
- The prompt rendering pipeline (rung body + target directive +
  SIDECAR + per-step injection) — only content changes, not shape.
- The 8-step multi-invocation flow — moves into the agent
  initContainer's entrypoint script untouched.
- The opencode pin + `config/opencode.template.json` shape.
- The five-model panel pin and OpenRouter slug resolution.
- The local `analysis/` workflow: pull bucket → pandas → notebook.

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
   Stripped iptables/ipset from
   [Dockerfile.dnsmasq](Dockerfile.dnsmasq); the image is now a
   passive logger. Dropped the DNS-log sidecar from the target Pod
   shape entirely — `trajectory.jsonl` is the sole egress
   observation layer. The image/config stay in the repo as no-ops
   until PR6's cleanup sweep. `firewall-probe` smoke job retired.
   EXPERIMENTAL_DESIGN.md §Egress observability + §Threats to
   validity updated.
5. **K8s submission path.** Add `orchestration/templates/job.yaml.j2`
   + `orchestration/render_jobs.py` + `orchestration/k8s_apply.sh`.
   Submit a one-cell smoke test to a GKE Autopilot cluster; verify
   artefacts land in the bucket.
6. **Repo cleanup.** Delete everything in the "Delete" table above
   in one sweep. README + EXPERIMENTAL_DESIGN docs catch up to the
   new shape.
7. **Headline batch.** Submit the full panel as a k8s Indexed Job.
   Batch-tag suggestion: `headline-k8s-<date>`. This is the
   reportable batch — phase-5c artefacts are abandoned, not compared
   against.
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
   run.sh contract" regressions cheaply.
3. **`activeDeadlineSeconds` ceiling.** What's the right Pod-level
   timeout for 100×100? Empirically TBD; suggest first probe sets
   `activeDeadlineSeconds: 3600` and the headline batch tunes from
   there.

## Current state

Scorer JSON schema: `phase6-v1` (`harness/run_metrics.py:SCHEMA_VERSION`)
on `feat/k8s-refactor`; `phase5a-v1` on `dev`.

**Per-cell artefacts under `runs/<batch-tag>/<run_id>/`** (current
local-orchestration layout; will change in PR5 when the scorer
container does in-Pod uploads):

- `workspace/` — agent-authored files (including `PLAN.md`)
- `prompt_body.md` — rendered shared body
- `trajectory.jsonl` — in-order concatenation of all 8 steps' opencode events
- `agent_stderr.log` — in-order concatenation of all 8 steps' stderr
- `agent_artifacts/session_export.json` — final attempted step's opencode export
- `agent_artifacts/steps/step_NN/` — per-step `trajectory.jsonl`, `agent_stderr.log`, `session_export.json`, `step_meta.json`
- `dns.log` — every DNS query the agent container made
- `scorer.json` — full scoring record
- `report.md` — Jinja2-rendered per-cell report *(deleted in PR6)*
- `transcript.md` — human-readable opencode transcript
- `time_breakdown.json` — phase timings
- `run_meta.json`, `run_meta.cell.json`, `run_meta.final.json` — orchestration metadata

**Per-batch artefacts under `runs/<batch-tag>/`** *(most deleted in PR6)*:
- `worklist.tsv`, `joblog.tsv`, `manifest.jsonl`, `summary.txt`, `batch_report.md`, `cell-logs/<run_id>.log`

The k8s flow (PR5+) replaces this layout with the bucket as the
source of truth — per-cell artefacts land under
`<bucket>/<prefix>/<batch-tag>/<run-id>/` directly from the scorer
container's `mc mirror`.

# K8s refactor — scoring simplification + cloud-native execution

Refactor plan for the next round of ForeverTree experiments. Three coupled
scope buckets, captured here so the docs ([README.md](README.md),
[EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md),
[SCORING.md](SCORING.md)) can be updated against a single proposal and so
the implementation can be sliced into a sequenced PR series.

> **Status (2026-05-20).** Proposal, not yet implemented. Existing
> phase-5c batches (`runs/batch-overnight-20260520-i{1,2,3}`) are
> abandoned — the headline batch under this refactor starts fresh.
> No backwards compatibility is required between the old and new
> scorer schemas, batch layouts, or orchestration shapes.

## TL;DR

| Bucket | Before | After |
|---|---|---|
| **Scoring** | 4 axes incl. growth-rate / age-step / nTrees consistency block; scorer runs the agent's `./run.sh` once for 11 sim years | 2 axes — *ecology* (year-100 height + occupancy) and *style* (LOC / imports / entropy); scorer runs 100 replicates × 100 sim years for real wall-clock |
| **Infra** | Per-cell dnsmasq+iptables sidecar in shared netns; local Docker daemon; host-side `launch_batch.py` driver | Per-cell k8s Job (one Pod, `fortree:agent` initContainer → `fortree:scorer` main container); egress *monitored* not enforced; the scorer container uploads artefacts via `mc` |
| **Repo** | `orchestration/` carries batch driver, per-cell wrapper, report renderers, rescore tooling, `upload_batch.sh` | `orchestration/` reduced to a Job manifest renderer + local `mc` download helper; reports + rescore tooling deleted; `analysis/` is the only local-workflow surface |

## 1. Scoring simplification

The phase-5c results showed the internal-consistency block was
diagnostic during methodology-building (it caught spec violations the
acceptance gate missed) but is now redundant: models that hit the
ecology bands also clear the consistency checks. Drop it.

### Keep

- **Target conformance** (mechanical) — `josh validate` exit zero on
  `.josh` files, or `import mesa` + Mesa class subclassing on Mesa runs.
  Still needed; this is what catches the "plain-Python sidestep" loophole.
- **Ecology metrics** — `height_year100_mean`, `occupancy_year100_mean`,
  `growth_rate_mean_m`. Compared against pre-registered acceptance
  bands (loose gate) and a spec-faithful tighter band (interpretive),
  same shape as the [ecology panel](analysis/headline.ipynb).
- **Style metrics** — `src_loc`, `comment_loc`, `imports_loc`,
  `entropy_bits`. These are the H1-adjacent "is the DSL more concise?"
  signal and they don't depend on running the model.
- **Wall-clock metric** — now promoted to a *headline measurement*
  rather than a reported-but-ignored diagnostic, because the new run
  shape (100 reps × 100 years) makes it large enough to be meaningful.
- **Schema gate** — kept as a precondition. Without a valid CSV, the
  ecology metrics are undefined.

### Drop

- **Internal-consistency block.** Delete
  [harness/internal_consistency.py](harness/internal_consistency.py)
  and all `consistency.*` fields from the scorer record.
- **target_conformance_fuzzy schema placeholder.** Replaced by the new
  fuzzy-judge spec below.
- **Acceptance-range "year 10" semantics.** Replaced by year-100.

### Add: 100 replicates × 100 simulation years

The agent's `./run.sh` now drives the full evaluation workload. The
prompt instructs the agent to put both preprocessing and the run with
`--replicates 100 --years 100` (or framework-equivalent) inside the
single `run.sh`. This serves three purposes:

1. **Real wall-clock.** A single-replicate 11-year run finishes in
   seconds; differences between targets get swamped by interpreter
   startup. 100×100 puts cell wall-times in the minutes range where
   model-vs-framework differences become measurable.
2. **Statistical floor for ecology metrics.** With 100 reps per cell
   the year-100 height/occupancy means have tight enough CIs to
   distinguish "spec-faithful with noise" from "wrong constants but
   plausible shape."
3. **Apples-to-apples comparison.** Because the workload is defined
   inside the agent's own `run.sh`, we need a verification that the
   agent did in fact wire it up that way — see the fuzzy-judge update
   below.

Acceptance ranges in
[harness/acceptance_ranges.json](harness/acceptance_ranges.json) update:
`target_year: 2034` → `target_year: 2123` (or whatever year-100 maps to
given the agent's chosen epoch); `height_year10` and `occupancy_year10`
keys rename to `..._year100`. Tighter bands TBD against a spec-faithful
reference run.

> **Open Q:** Should the scorer also normalise across noise — e.g.,
> compare the mean of the agent's 100 reps against the spec's expected
> distribution rather than against a fixed range? Resolvable once we
> have a reference distribution from a known-good run.

### Add: fuzzy-judge "preprocess + run in one run.sh" question

Extend [prompts/fuzzy_judge.md](prompts/fuzzy_judge.md) with a third
question:

> **Q3 — `run.sh` shape.** Does the agent's `./run.sh` invoke both the
> preprocessing step (loading climate netCDFs, building any derived
> inputs) and the simulation invocation with `--replicates 100` (or the
> Mesa equivalent — 100 parallel runs of a 100-year simulation)? Answer
> `yes` / `no` / `partial` with one sentence.

This is the apples-to-apples verification. A `no` here means the
wall-clock measurement isn't a fair comparison and the cell should be
flagged in analysis (not dropped — same convention as
`target_conformance=False`).

Persisted as `fuzzy_q3_answer` + `fuzzy_q3_justification` in the same
`scorer.fuzzy.json` the existing Q1/Q2 use. Schema bump:
`fuzzy-v1` → `fuzzy-v2`.

### Docs touched in this bucket

- [SCORING.md](SCORING.md) — rewrite §Scoring axes (drop axis 3
  internal-consistency, rewrite axis 4 spec-parameter for year-100
  + 100-reps), update §Metrics table, update fuzzy-judge spec to
  three questions, retire Open Q #3 (Spearman replacement — moot
  with the consistency block gone), keep Open Q #1 reframed against
  year-100 bands.
- [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) — update §Scoring,
  §Sample size (note that wall-clock is now a primary metric not a
  diagnostic), §Open methodology questions.
- [prompts/BASE_PROMPT.md](prompts/BASE_PROMPT.md) and
  [prompts/SIDECAR.md](prompts/SIDECAR.md) — instruct the agent to
  write `run.sh` such that it does preprocess + 100×100 in one
  invocation.
- [prompts/steps/](prompts/steps/) — touch the relevant step files
  (probably step_04_describe_commands.md, step_06_complete_logic.md,
  step_07_validate_outputs.md) to align with the new `run.sh`
  contract.

## 2. K8s execution

### Why this is now possible

The original blocker for cloud execution was the per-run
**dnsmasq+iptables egress sidecar** — two containers in a shared
network namespace with `CAP_NET_ADMIN`, which Cloud Run Jobs / Fargate
forbid and which only k8s (or raw Docker) can express. Once we relax
egress from *enforced* to *monitored*, that constraint disappears and
the cell can run as a vanilla k8s Pod.

The opencode `trajectory.jsonl` already records every `webfetch` call
with URL + timestamp. That covers ~all egress in practice. Indirect
egress (agent-authored `urllib` in `run.sh`) becomes observable via
either (a) GKE VPC flow logs at the node level, or (b) a passive
`dnsmasq` sidecar with query logging only (no iptables, no NET_ADMIN
needed). Option (b) preserves the `dns.log` artefact for continuity
with the headline batch and is recommended.

### Pod shape: agent then scorer in one Pod

One k8s Job per cell. The Pod has:

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
  - name: dns-monitor
    image: fortree:dnsmasq-passive
    # Passive query logger only — no iptables, no NET_ADMIN.
    # Records every DNS query the agent makes into dns.log so the
    # scorer can include it in the upload.
```

The initContainer pattern is the right fit because the scorer needs to
wait for the agent's exit before running. The scorer container also
owns the upload, so a stalled agent killed by `activeDeadlineSeconds`
still produces a partial upload (the scorer runs against whatever
workspace exists).

**Image separation stays.** The current
[Dockerfile](Dockerfile) multi-stage layout (`agent` vs `scorer`
targets sharing the `base` stage) keeps the trust-boundary argument
load-bearing: the agent container does not carry `harness/`,
`acceptance_ranges.json`, or `mc`, so the agent literally cannot read
the scoring criteria or exfiltrate its own outputs to the bucket. This
matters even with relaxed network egress, because it forecloses an
in-container "agent inspects the gate it's being graded against"
loophole. The k8s Pod expresses this separation cleanly: two different
images for the two containers, no shared filesystem except the
emptyDir mounted at `/workspace`.

`mc` is added to `fortree:scorer` only. Bucket credentials are mounted
into the scorer container via a k8s Secret reference — the agent
container's Pod spec never names that Secret, so even an agent that
shells out can't reach the bucket.

### Egress: monitored, not enforced

Two observation layers:

1. **opencode trajectory.jsonl** (already present). Captures every
   `webfetch` URL the model invoked. Primary record.
2. **Passive dnsmasq sidecar.** Same image as today, but
   `sidecar-init.sh` is stripped to just `exec dnsmasq -k` with
   `log-queries` — no iptables, no ipset, no `CAP_NET_ADMIN`. The
   `dns.log` artefact shape is preserved. The Pod sets
   `dnsPolicy: None` + `dnsConfig.nameservers: [<sidecar-IP>]` so
   the agent container's resolver still goes through the sidecar
   (this is the k8s equivalent of the current `--network=container:`
   sharing — different mechanism, same effect on the DNS log).

The hard policy boundary (the iptables OUTPUT-chain `REJECT`) goes
away. The validity argument leans on the trajectory + DNS logs being
sufficient post-hoc evidence of what the agent reached.

### Artefacts: in-Pod `mc` upload

The scorer container runs `mc mirror /workspace
$MINIO_ENDPOINT/$MINIO_BUCKET/$BATCH_TAG/$RUN_ID/` at the end of its
script. Credentials come from a k8s Secret mounted as env vars
(`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_ENDPOINT`). Same GCS
S3-interop bucket the headline batch already uses; no MinIO server
runs anywhere.

The Pod itself is ephemeral — emptyDir for `/workspace`, no PVC. The
authoritative artefact store is the bucket. This is the same
"bucket-is-source-of-truth" pattern the current `upload_batch.sh`
already implements, just relocated from a host-side post-batch step
into a per-cell in-Pod step.

### Submission shape

A small Python script renders one Job manifest per cell from a matrix
CSV (`model,target,replicates`), then `kubectl apply -f -`s them all.
Parallelism cap comes from the k8s Job's `parallelism` field on the
Indexed Job, or from external rate-limiting (OpenRouter is still the
binding constraint at ~4–8 concurrent on a single API key).

Cluster shape: **GKE Autopilot**, one **Indexed Job** per batch with
`completions=N, parallelism=K` (K capped to OpenRouter's per-key
concurrency, currently ~4–8). Autopilot bills per Pod-second so the
cluster has no idle cost between batches; Indexed Jobs keep the
per-batch state to a single k8s resource for clean teardown.

### Docs touched in this bucket

- [README.md](README.md) — replace §Running it / §Running a batch
  locally / §Archiving a batch to GCS sections with k8s submission
  instructions. Keep §Re-scoring section but reshape against the
  k8s scorer (a re-score is a new Job pulling the workspace from
  the bucket and re-running the scorer container).
- [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) — rewrite §Stack
  (no more dnsmasq enforcement story), §Run flow (Pod-shaped, not
  docker-network-shaped), §Egress observability (drop hard-policy
  layer, keep monitoring layer), §Threats to validity (acknowledge
  the relaxation — there's now a "what if the agent did weird
  egress" footnote rather than a kernel-level guarantee).
- [SCORING.md](SCORING.md) — §Re-analysing completed runs gets a
  k8s-flavoured rewrite (re-score Job manifest instead of `docker
  run`).

## 3. Repo cleanup

Goal: the repo should contain only what's needed to reproduce a k8s
batch and to analyse downloaded results locally. Everything in the
host-side batch-orchestration tree goes.

### Delete

| Path | Reason |
|---|---|
| [orchestration/launch_batch.py](orchestration/launch_batch.py) | Replaced by k8s Job submission |
| [orchestration/launch_cell.sh](orchestration/launch_cell.sh) | Replaced by Pod spec |
| [orchestration/launch_run.sh](orchestration/launch_run.sh) | Replaced by Pod spec |
| [orchestration/run_agent.sh](orchestration/run_agent.sh) | Replaced by Pod initContainer + k8s `activeDeadlineSeconds` |
| [orchestration/dns_sidecar.sh](orchestration/dns_sidecar.sh) | Replaced by passive Pod sidecar manifest |
| [orchestration/sidecar-init.sh](orchestration/sidecar-init.sh) | iptables setup no longer needed; passive dnsmasq image's entrypoint becomes a one-liner |
| [orchestration/generate_run_report.py](orchestration/generate_run_report.py) | Reports replaced by the headline notebook |
| [orchestration/generate_batch_report.py](orchestration/generate_batch_report.py) | Same |
| [orchestration/rescore_batch.py](orchestration/rescore_batch.py) | Replaced by re-score Job |
| [orchestration/rescore_cell.sh](orchestration/rescore_cell.sh) | Same |
| [orchestration/upload_batch.sh](orchestration/upload_batch.sh) | Upload happens inside the Pod's scorer container |
| [orchestration/run_fuzzy_judge.sh](orchestration/run_fuzzy_judge.sh) | Becomes a k8s Job that pulls completed cells from the bucket and writes back |
| [orchestration/extract_transcript.py](orchestration/extract_transcript.py) | Run inside the scorer container, not host-side |
| [orchestration/extract_time_breakdown.py](orchestration/extract_time_breakdown.py) | Same |
| [harness/internal_consistency.py](harness/internal_consistency.py) | Scoring axis dropped |
| [harness/conformance_fuzzy.py](harness/conformance_fuzzy.py) | Placeholder; replaced by the real host/Job-side fuzzy judge |
| [Dockerfile.dnsmasq](Dockerfile.dnsmasq) — enforcement bits | Strip the `iptables`/`ipset` install + `sidecar-init.sh` copy if we keep the passive sidecar; or delete the file entirely if we drop the sidecar |
| [prompts/rungs/](prompts/rungs/) | Rung ladder retired in phase 5c; the dir was already a vestige |

### Keep + repurpose

| Path | Repurpose |
|---|---|
| [orchestration/resolve_model.py](orchestration/resolve_model.py) | Used by the Job manifest renderer |
| [orchestration/templates/](orchestration/templates/) | New home for Pod / Job Jinja templates |
| New `orchestration/render_jobs.py` | Renders one Job per row of the matrix CSV |
| New `orchestration/k8s_apply.sh` | Thin `kubectl apply -f -` wrapper |
| New `orchestration/pull_artefacts.sh` | Local-side: `mc mirror` a batch from the bucket into `runs/<batch-tag>/` for analysis |
| [scripts/install_*.sh](scripts/) | Unchanged — image build still uses them |
| [harness/run_metrics.py](harness/run_metrics.py) | Strip the consistency block; add 100×100 wall-clock recording |
| [harness/runner.py](harness/runner.py) | Bump `DEFAULT_TIMEOUT_S` to accommodate 100×100 runs (probably ~30 min ceiling per cell, TBD) |
| [analysis/aggregate.py](analysis/aggregate.py) | Drop consistency-field aggregation; add year-100 field aggregation |
| [analysis/headline.ipynb](analysis/headline.ipynb) | Drop Panel C (consistency); rename Panel A "ecology" subplots to year-100; the rest stays |

### CI

- [.github/workflows/smoke.yml](.github/workflows/smoke.yml) — keep
  conceptually (assert scorer fixtures produce expected outcomes) but
  drop fixtures referencing consistency fields. Builds
  `fortree:runner` instead of `fortree:scorer`.
- [.github/workflows/integration.yml](.github/workflows/integration.yml)
  — rewrite to submit a single k8s Job to a test cluster (or skip
  if the test cluster isn't free), instead of the current Docker
  Compose-shaped flow.

### What stays exactly the same

- The prompt rendering pipeline (rung body + target directive +
  SIDECAR + per-step injection) — only content changes, not shape.
- The 8-step multi-invocation flow — moves into the agent
  initContainer's entrypoint script untouched.
- The opencode pin + `config/opencode.template.json` shape.
- The five-model panel pin and OpenRouter slug resolution.
- The local `analysis/` workflow: pull bucket → pandas → notebook.

## Sequencing

Suggested PR order so each step is reviewable in isolation:

1. **Scoring drop.** Delete `harness/internal_consistency.py`,
   strip consistency fields from `run_metrics.py`, update
   `acceptance_ranges.json` to year-100, update SCORING.md.
   Bump `SCHEMA_VERSION` to `phase6-v1`. Smoke-test by hand-rolling
   a minimal valid workspace and confirming every field in the new
   schema appears (no reference to phase-5c artefacts — we're
   starting fresh).
2. **Prompt update** for `run.sh` shape (preprocess + 100×100).
   Update BASE_PROMPT / SIDECAR / relevant step files. Add fuzzy
   Q3 to `prompts/fuzzy_judge.md`. Sub-tasks:
   - **Disambiguate the year-0 question.** PR1's offline sanity check
     against the phase-5c batches found a systematic +10% slope on
     Josh runs vs Mesa runs (β≈1.10 vs β≈1.00), almost certainly
     because Josh implementations grow trees at step 0 too while Mesa
     implementations treat step 0 as init-only. The 11-step run gets
     11 vs 10 growth events depending on interpretation, and the spec
     at [BASE_PROMPT.md §Entities](prompts/BASE_PROMPT.md) is
     ambiguous. Pin the prompt to **"year 0 is initialization, no
     growth"** so an N-year simulation gets N-1 growth events. This
     matches the reference simulator [data/reference_sim.py](data/reference_sim.py),
     which sums `predicted_growth` over years 1..N-1. Whichever
     interpretation we pick has to match the reference or the
     regression gate will systematically detect ±10% slope on the
     "wrong" framework.
   - 100-year sim: years 2024..2123 inclusive in the CSV (100 rows
     per cell per replicate), with growth between consecutive years.
     Spelled out explicitly in the prompt so agents can't
     accidentally produce a 99-row or 101-row CSV.
   - `--replicates 100` (or framework equivalent) in `run.sh`.
   - Preprocess (`.jshd` build for Josh, netCDF→DataFrame for Mesa)
     inside the same `run.sh` so wall-clock includes data loading.
   - Fuzzy Q3 question wording asserts the above contract.
3. **Image consolidation.** Merge `agent` and `scorer` build
   targets in [Dockerfile](Dockerfile). Add `mc` to the image.
   Add `scorer-and-upload.sh`. Smoke-build locally; no behaviour
   change yet (still runnable under the old orchestration).
4. **Egress relaxation.** Either strip iptables/ipset from
   [Dockerfile.dnsmasq](Dockerfile.dnsmasq) (passive sidecar) or
   delete the file (no sidecar). Update
   [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) §Egress
   observability and §Threats to validity. Acknowledge methodology
   delta in the paper draft.
5. **K8s submission path.** Add `orchestration/templates/job.yaml.j2`
   + `orchestration/render_jobs.py` + `orchestration/k8s_apply.sh`.
   Submit a one-cell smoke test to a GKE Autopilot cluster; verify
   artefacts land in the bucket.
6. **Repo cleanup.** Delete everything in the "Delete" table above
   in one sweep. README + EXPERIMENTAL_DESIGN docs catch up to the
   new shape.
7. **Headline batch.** Submit the full panel as a k8s Indexed Job.
   Batch-tag suggestion: `headline-k8s-<date>`. This is the
   reportable batch — phase-5c artefacts are abandoned, not
   compared against.

## Open questions

1. **Acceptance-band recalibration.** *Substantively resolved in PR1.*
   The headline ecology gate is now an `observed ~ predicted`
   regression against a spec-faithful Python reference simulator
   ([`data/reference_sim.py`](data/reference_sim.py)) run on the
   committed synthetic climate netCDFs. β/α/R² targets are derived
   from physics, not hand-picked. Open follow-up: tolerance widths
   around those targets (β ∈ [0.95, 1.05], etc.) are currently
   hand-picked to give implementation tolerance — once the headline
   batch produces a panel of agent runs we can re-calibrate against
   their empirical spread. See SCORING.md §Open methodology
   questions #1.
2. **Per-model engagement probe.** With 100×100 the cost of a
   *succeeded* cell goes up materially, so it's worth a single-cell
   probe per model under the new prompt + workload before launching
   the headline batch, to surface any "model fails to engage with the
   new run.sh contract" regressions cheaply.
3. **`activeDeadlineSeconds` ceiling.** What's the right Pod-level
   timeout for 100×100? Empirically TBD; suggest first probe sets
   `activeDeadlineSeconds: 3600` and the headline batch tunes from
   there.

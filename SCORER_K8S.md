# SCORER_K8S.md — k8s re-scoring pass (score saved workspaces without re-running the agent)

## Problem

The headline panel excludes cells that never produced a `scorer.json`
(`engagement_status=no_scorer`). We established (see the
`no-scorer`/throttle vs runtime-kill audit) that these split into two
populations:

1. **Provider throttling** — OpenRouter `429`/`AI_APICallError`, usually a
   free-tier lockout that died on step 1 with no scorable artifact.
   *Not the model's fault; no real attempt was made.* → **re-run** the
   agent under clean conditions.
2. **Runtime kill** — agent ran cleanly (`exit=0` on completed steps),
   was killed mid-step by the 2 h `activeDeadlineSeconds` (slow josh-mcp
   loop), and **left a partial workspace behind** (mirror-sidecar saved
   it). *This is a legitimate timeout failure, currently mis-handled as
   an exclusion.* → **score the partial as `did_run=False`; do NOT
   re-run** (re-running would be a fresh dice roll → selection bias).

This doc plans the **re-scoring pass** for population (2): run the
existing scorer against each saved partial workspace, in k8s, producing a
real `scorer.json` (almost always `did_run=False`) so the cell becomes an
honest failure row instead of a silent exclusion.

## Why k8s, not host-side

Scoring is a deterministic function of the workspace, so a host-side
`entrypoint-scorer.sh` would be *correct* — but it would measure
`sim_wall_seconds` (and any JVM/Decimal timing) on devcontainer hardware,
not the GKE `Performance`/`n2` node the rest of the panel ran on. For the
few partials that happen to produce a runnable sim, that would bias the
runtime panel. Running the scorer **in the same Pod spec** keeps every
metric drawn on identical hardware/heap/`--network=none` conditions.
(For the common case — a step-04 partial with no working `run.sh` —
`did_run=False`, so there is no sim runtime to bias; k8s is still the
clean, consistent choice and lets us reuse the scorer container verbatim.)

**Provenance note:** the rescore regenerates only `scorer.json` +
`scorer.fuzzy.json`. The agent-phase metrics (`agent_wall_seconds`,
`agent_cost_usd`, tool calls, tokens) come from the **original** run's
`agent_meta/`, which we preserve untouched — so those stay authentic to
the real agent run, not the rescore.

## Design — a "rescore Job" = the per-cell Job minus the agent

Reuse [orchestration/templates/job.yaml.j2](orchestration/templates/job.yaml.j2)
almost verbatim. The per-cell Pod is three pieces today:

```
initContainer setup    → seeds /cell-data from the ConfigMap
initContainer agent    → 8-step opencode loop  ……………… DROP for rescore
container     scorer   → scores /cell-data/workspace + mc-mirrors up
initContainer mirror-sidecar (native sidecar)
```

The rescore Job keeps **setup + mirror-sidecar + scorer** and **removes
the agent initContainer**. The only real change is what `setup` does:

| container | per-cell Job (today) | rescore Job (new) |
|---|---|---|
| `setup` | `cp` PLAN/opencode/prompt from ConfigMap | **`mc mirror` the saved cell tree down from the bucket** into `/cell-data` (workspace + agent_meta + opencode), so the scorer sees the partial *and* agent_meta is preserved for re-upload |
| `agent` | opencode 8-step loop | **removed** |
| `scorer` | `scorer-and-upload.sh --target T` | **unchanged** |
| `mirror-sidecar` | periodic upload | **unchanged** |

`setup` already needs `mc` + the MinIO secret — use the **scorer image**
for it (it carries `mc`), exactly like `mirror-sidecar` does. So no new
image, no new credential surface.

### Reuse map (what we touch vs. what we don't)

- **Reused as-is:** scorer image, `containers/scorer-and-upload.sh`,
  `containers/entrypoint-scorer.sh`, `containers/mirror-sidecar.sh`,
  `containers/run-judge.sh` (fuzzy judge), the `minio_secret` /
  `openrouter-creds` (key `api-key`) secrets, `Performance`/`n2`
  nodeSelector, scorer resource block, `JAVA_TOOL_OPTIONS`,
  `analysis/aggregate.py`, `orchestration/pull_artefacts.sh`.
- **New (small):** a `rescore` mode in `render_jobs.py` that emits the
  agent-less template variant + a bucket-pull `setup`; a classifier
  script that decides which cells are population (2); a `pixi run
  rescore` task mirroring `pixi run apply`.
- **Template change:** guard the agent initContainer and the
  ConfigMap-based setup behind `{% if not rescore %}`, and add a
  `{% if rescore %}` setup that pulls from the bucket. One template, two
  modes — keeps drift out.

## Inputs — the classifier (deterministic, from saved artifacts)

Before rendering anything, label every `no_scorer` cell from its saved
artifacts (the logic we already ran by hand):

```
for each no_scorer cell:
    api_errs   = count 'AI_APICallError' across agent_meta/steps/*/agent_stderr.log
    steps_ok   = steps with step_meta.exit_code == 0
    last_step  = highest step dir; has step_meta.json?
    if api_errs > 0 and steps_ok == 0:        → THROTTLE   (re-run agent, clean conditions)
    elif last_step lacks step_meta and steps_ok >= 1: → RUNTIME_KILL (rescore partial)
    elif no agent_meta at all:                → INFRA_NONSTART (e.g. the api-key Secret bug → re-run)
    else:                                     → REVIEW (hand-inspect; don't auto-handle)
```

Output: `orchestration/rescore-manifest.csv` (`orig_batch_tag,run_id,target,minio_prefix`)
for the `RUNTIME_KILL` rows. `THROTTLE` / `INFRA_NONSTART` cells are reported in
the printed summary but NOT written to a matrix here — the deficit re-rep that
replaces them (and tops every combo up to N reps) is computed by
[analysis/apply_scoring.ipynb](analysis/apply_scoring.ipynb), the single source
of truth for "what to launch". The classifier
`orchestration/classify_no_scorer.py` keeps the labeling reproducible and
auditable (it becomes the paper's methods appendix).

## Flow

```
1. pixi run aggregate runs/<all batches>          # current state
2. analysis/apply_scoring.ipynb                    # classify → rescore-manifest.csv + deficit matrix-rescore-rerun.csv
3. pixi run rescore -- --batch-tag rescore-<date> --manifest orchestration/rescore-manifest.csv \
       --image-scorer ghcr.io/.../fortree-scorer:a5b3eae   # render + apply agent-less Jobs
4. kubectl -n joshsim get jobs -l batch-tag=rescore-<date> -w
5. pixi run pull <each original batch>            # scorer.json now present for rescored cells
6. pixi run aggregate runs/<all batches>          # no_scorer rows → real did_run=False rows
   # then `pixi run apply` the deficit matrix (matrix-rescore-rerun.csv) in small waves; re-run the notebook
```

**Upload target:** the rescore must write `scorer.json` back to the
**original** cell's bucket key (`<batch>/<run-id>/`), not a new
`rescore-<date>` path, so `pull` + `aggregate` pick it up in place. Set
`BATCH_TAG`/`RUN_ID`/`MINIO_PREFIX` env on the scorer container to the
*original* cell's values (the Job's own name can differ). The
mirror-sidecar/scorer key off those env vars, so this is just how we
render them — no script change.

## Idempotency & safety

- **Target only `no_scorer` cells.** Never rescore a cell that already
  has a `scorer.json` (would regenerate `sim_wall_seconds` under a
  different run and break comparability with the originally-scored
  cells).
- **Preserve `agent_meta/`.** `setup` pulls the whole cell tree down; the
  scorer only writes `scorer.json`/`scorer.fuzzy.json`; the final mirror
  re-uploads the merged tree. Verify the scorer does not wipe
  `agent_meta/` (`scorer-and-upload.sh` scrubs only stale CSV/scorer
  self-test output under `workspace/`, not `agent_meta/`).
- **Smoke-test on one cell first** (e.g. `kimi-josh-mcp-r0`): confirm the
  scorer handles a partial josh-mcp workspace — `run.sh`+`runner.py`
  present but `mcp_calls.json` absent/incomplete → `./run.sh` no-ops →
  `did_run=False`, no crash. This is the one real unknown; the rest is
  plumbing.
- **`activeDeadlineSeconds`:** scoring is bounded (no agent, no retries) —
  the 100-replicate canonical `./run.sh` is the long pole. josh sims can
  be 30–60 min, so set the rescore deadline to ~5400 s and keep the
  scorer memory block as-is. A `did_run=False` partial returns in
  seconds.

## Open questions / risks

1. **Does `scorer-and-upload.sh` tolerate a workspace with no/partial
   `run.sh`?** Must degrade to `did_run=False`, not exit non-zero in a way
   that loses the upload. Verify in the one-cell smoke test. If it's
   brittle, add a guard at the top of `entrypoint-scorer.sh`.
2. **MinIO prefix mismatch.** These batches live at the bucket *root*,
   but the template/`pull_artefacts.sh` prepend `MINIO_PREFIX`. The
   rescore `setup` pull and the scorer upload must use the *same* prefix
   the original cell was written under (root for the headline-rerep
   batches). Pin `MINIO_PREFIX=""` for those; this is the same
   `:-`-vs-`-` bug noted in `pull_artefacts.sh` — fix once, reuse.
3. **Fuzzy judge cost.** `run-judge.sh` calls OpenRouter; a `did_run=False`
   partial still gets judged. Cheap, but if we want to skip Q3/Q4 on
   obvious non-runs, gate the judge on `did_run`. Optional.
4. **N bookkeeping.** After rescoring, kimi/josh-mcp = 4 pass + 4
   `did_run=False` = **4/8**; gemma/josh-mcp = 4 + 3 (+ the killed live
   cell) = up to 8. Other treatments reach N=8 via standard re-run, not
   rescore. Confirm the aggregator counts a rescored `did_run=False` row
   in the Panel-A denominator (it should: `engagement_status` is no
   longer `no_scorer` once `scorer.json` exists).

## Why this is bias-free

No `RUNTIME_KILL` cell is re-run — its already-recorded behavior is scored
with the **same deterministic scorer** applied to every other cell. The
only re-runs are `THROTTLE`/`INFRA_NONSTART` cells that produced **zero
scorable work** (the model never attempted the task), which is a standard
infra-failure replacement, not a selective re-roll of an outcome.

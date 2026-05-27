# MCP_UPDATE — adding the `josh-mcp` arm + a netCDF spec sheet

> Status: **design / pre-implementation**. This is the experiment-side planning
> doc. The companion server-side work lives in the Josh repo:
> [SchmidtDSE/josh#440](https://github.com/SchmidtDSE/josh/pull/440) ("Add MCP
> server (Phase 1)") lands the `mcp` subcommand on Josh `dev` — stdio transport,
> a local in-JVM backend, and **four tools** (`validate_simulation`,
> `discover_config`, `preprocess_data`, `run_simulation`), bundling MCP SDK
> `1.1.3`. No code in *this* repo has changed yet; this doc is the prep artifact
> that the implementation PR will follow, now reconciled against the landed
> server surface (see §4 and §10 for the one consequential gap: `run_simulation`
> has no `--data`).

## 1. Goal & motivation

Today the experiment runs every cell of the panel through `opencode` with one
fixed tool palette — read / write / edit / glob / grep / **bash** / webfetch on,
`task` off, `permission.bash: "allow"` — wired once in
[config/opencode.template.json](config/opencode.template.json) and stamped onto
every Job by [orchestration/render_jobs.py](orchestration/render_jobs.py). The
agent's *environment* is therefore held constant across the `josh` and `mesa`
arms; the only thing that varies is the implementation target.

We are adding a third arm, **`josh-mcp`**. It generates Josh DSL exactly like
`josh`, but the agent is **constrained**:

- **no `bash`, no `webfetch`** — no arbitrary shell, no network;
- **read** access to its workspace (the repo it can see), **write** access to its
  sandbox;
- Josh's `preprocess` / `run` / `validate` (and `discover_config`) reachable
  **only through an MCP server** that opencode spawns as a stdio subprocess.

This mirrors the real Josh **product** direction — Josh exposed as an MCP tool a
model can call — and isolates "just the Josh parts" of the agent's loop: the
model authors `.josh` source and drives the Josh pipeline through a typed tool
surface instead of poking at a shell.

A no-`bash` agent cannot inspect the input data interactively (`ncdump`,
`xarray.open_dataset(...)`, etc.). So alongside the new arm we add a **descriptive
netCDF spec sheet**, validated by a jupyter notebook, that tells the model
everything it needs to know about the input files up front. Removing the need for
diagnostic data-poking is precisely what makes the constrained arm viable.

The Josh MCP server itself is built **on the Josh side** —
[PR #440](https://github.com/SchmidtDSE/josh/pull/440), Phase 1, targeting Josh
`dev`. It adds a `mcp` stdio subcommand on the same fat jar exposing the four
tools above. The experiment *consumes* that server; it does not build it. Two
properties of the landed v1 surface shape our arm and are carried through below:
`preprocess_data` is **per-(file, variable)** and requires an explicit `unitsStr`
(the agent cannot introspect units — the spec sheet must supply them, §5), and
`run_simulation` exposes a **minimal parameter set** (`script`, `simulation`,
`replicates`, `serialPatches`, `seed`) with **no `--data`** — `--data`,
`--custom-tag`, and `--output-steps` are explicitly deferred to a follow-up
(§4.2, §7).

## 2. The new experimental dimension

The experiment has been a clean **1-factor** design: `target ∈ {josh, mesa}`, with
the environment frozen. `josh-mcp` introduces a second, orthogonal factor —
**environment ∈ {full-tools, mcp-constrained}**. Two honest caveats about that
factor:

- It bundles several changes at once (bash/webfetch off, Josh commands delivered
  via MCP instead of CLI, and the spec sheet). We mitigate the spec-sheet part of
  that bundle by giving the spec sheet to *all* arms (see §3), so the live knob
  between `josh` and `josh-mcp` is genuinely *tool access*.
- There is **no `mesa-mcp`** — there is no Mesa MCP server. So this is not a clean
  2×2. It is an **L-shaped partial factorial**: three of the four
  `{josh, mesa} × {full, mcp}` cells are populated; `(mesa, mcp)` is empty by
  design.

That geometry gives three pairwise contrasts, and it matters which claim each one
supports:

| Contrast | Holds fixed | Varies | Claim it backs |
|---|---|---|---|
| `josh` ↔ `mesa` | environment = full-tools | target | **H1, unchanged** — DSL vs framework. **This stays the headline number.** |
| `josh` ↔ `josh-mcp` | target = Josh | environment | **Cost-of-constraint** — what does removing the shell buy or cost, within Josh? Controlled. |
| `josh-mcp` ↔ `mesa` | — | target *and* environment | **Product** claim only — "the Josh-MCP workflow vs a generic Mesa dev loop." *Not* controlled (Mesa keeps bash). |

The rule that protects the science: **never let `josh-mcp` ↔ `mesa` become the
headline DSL-vs-framework figure.** It is a legitimate and arguably more
compelling *product* story, but it confounds target with environment and must be
labeled as such wherever it appears.

## 3. Decisions (locked) & rationale

1. **Three arms.** `josh`, `mesa`, and the new `josh-mcp`. `josh-mcp` is an
   *addition*, not a replacement — raw `josh` stays as the full-tools Josh
   baseline. Without it we could never attribute a Josh↔Mesa difference to "the
   DSL" rather than "the constrained environment."

2. **Spec sheet is descriptive-only, and goes to all arms.**
   - *Descriptive-only:* it formalizes facts [prompts/SIDECAR.md](prompts/SIDECAR.md)
     §AI Inputs already states in prose — file paths, variable names + units
     (`tasmax` in K; `pr` flux → mm/yr via ×31\_536\_000), dimensions and ranges
     (`calendar_year`, `lat`, `lon`), the 2024–2123 window — plus one
     notebook-validated sample row. It deliberately **omits** the 3 km→16 km
     spatial-aggregation recipe and any "how data reaches the runtime" guidance.
     Those are part of the data-binding task the experiment is built to measure;
     handing them over would sand down a real difficulty.
   - *All arms:* if only `josh-mcp` got the spec sheet, the `josh`↔`josh-mcp`
     contrast would differ in **both** tool access **and** information —
     uninterpretable. Giving it to every arm makes that contrast a clean
     *tools-only* comparison, and keeps `josh`↔`mesa` internally consistent
     (both still full-tools, both now spec-sheet-fed).
   - *Cost:* the spec sheet edits the **shared** SIDECAR, so it changes the
     existing `josh`/`mesa` arms too. Headline numbers are therefore **not**
     comparable to the in-flight Phase-6 batch — bump the batch tag and
     re-baseline all three arms together.

3. **`josh-mcp` runs as an exploratory probe first.** A small sub-batch (1–2
   models × `josh-mcp` × 2–3 reps) to prove the MCP server spawns, the agent
   actually calls the Josh tools, and it produces conformant `.josh` artifacts —
   *before* `josh-mcp` is added to the headline `matrix.csv`. This is
   brand-new plumbing; de-risk it on ~3 cells, not ~50.

## 4. The `josh-mcp` runtime

### 4.1 opencode config

A new sibling template, `config/opencode.josh-mcp.template.json`, differs from
[config/opencode.template.json](config/opencode.template.json) in exactly three
ways: bash/webfetch off, an MCP server block added, and the `josh*` MCP tools
enabled for the agent. Schema confirmed against the opencode MCP docs (top-level
`"mcp"` key; `type: "local"` stdio servers; MCP tools exposed under the
`<server>` name prefix and toggled per-agent in the `tools` block):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "openrouter": { "options": { "apiKey": "{env:OPENROUTER_API_KEY}" } }
  },
  "mcp": {
    "josh": {
      "type": "local",
      "command": ["josh", "mcp"],
      "enabled": true,
      "environment": {}
    }
  },
  "agent": {
    "coder": {
      "model": "${RESOLVED_MODEL_ID}",
      "tools": {
        "read":     true,
        "write":    true,
        "edit":     true,
        "glob":     true,
        "grep":     true,
        "bash":     false,   // was true
        "webfetch": false,   // was true
        "task":     false,
        "josh*":    true     // expose the MCP server's tools
      },
      "permission": {
        "external_directory": "allow"
        // bash / webfetch permission lines dropped
      }
    }
  }
}
```

`command: ["josh", "mcp"]` works because `josh` in the agent image is a thin
wrapper that forwards subcommands to the fat jar (Dockerfile:41,52). PR #440
documents the equivalent explicit form `["java", "-jar",
"/path/to/joshsim-fat.jar", "mcp"]`; either is fine. Confirm at implementation
time that the wrapper passes `mcp` through (the existing wrapper already forwards
`run`/`validate`/`preprocess`, so it will).

The Josh tools then appear to the agent as `josh_validate_simulation`,
`josh_preprocess_data`, `josh_run_simulation`, `josh_discover_config` (server-name
prefix) — these four and no more in v1; `inspect_exports` / `inspect_jshd` are
Phase 3 on the Josh side, so there is **no MCP tool that inspects the output
CSV's shape**. The agent directive (§4.3) names them.

### 4.2 The `run.sh` contract is unchanged

The scorer's contract is still "run `./run.sh`, read `output/results.csv`," and
the **scorer container keeps full bash + the `josh` CLI** — it is the harness, not
the constrained agent. So:

- the agent **still authors `./run.sh`** (declaratively — it is the unit of work
  the experiment measures), and
- the agent **self-tests via the MCP tools** (`josh_run_simulation` /
  `josh_validate_simulation`), because it cannot execute `./run.sh` itself (no
  bash).

This keeps [orchestration/templates/job.yaml.j2](orchestration/templates/job.yaml.j2)
and [containers/scorer-and-upload.sh](containers/scorer-and-upload.sh) untouched.

**Data binding must be script-resident, not runtime `--data`.** This is the one
place the landed server surface constrains the arm. The agent's self-test path is
`josh_run_simulation`, whose v1 schema is `{script, simulation, replicates,
serialPatches, seed}` — there is **no way to pass `--data` through MCP**. So for
the agent's MCP self-test to be faithful to the `./run.sh` the scorer later runs,
the simulation's input-data wiring must live **inside the `.josh`/`.jshc` + the
preprocessed `.jshd`** (the `.jshd` paths the agent chose via
`preprocess_data`'s `outputFile`), *not* be injected at run time with
`josh run --data …`. Our prompts already lean this way (`josh.md` says "use only
josh primitives `.josh`, `.jshc`, `.jshd`"; SIDECAR puts output wiring in
`exportFiles.patch`, i.e. in the script), and nothing in the repo mandates
`--data`. The new `prompts/targets/josh-mcp.md` must make this **explicit**: bind
external climate data through the `.jshc`/`.josh` referencing the `.jshd`, so a
`josh_run_simulation` self-test exercises the exact same wiring as `run.sh`. If
the agent instead reaches for `josh run --data` (a common idiom), its MCP
self-test silently diverges from `run.sh` — a concrete instance of the
"constraint may hurt" risk (§7). The agent builds each `.jshd` with one
`preprocess_data` call per variable (`tasmax`, `pr`).

### 4.3 Read-only repo + sandbox writes

The agent's workspace is `/sandbox` (a writable emptyDir, seeded with `PLAN.md`);
the ground-truth files (`data/reference_sim.py`, `harness/*`,
`acceptance_ranges.json`) are baked into the **scorer** image, *not* the agent
image, so the agent never sees them regardless of palette. Within the agent
container, "read access to the repo" means the input data + any installed
language docs the model can read; writes are scoped to `/sandbox`. Enforcement is
primarily the opencode tool palette (no bash, write tool only); a filesystem-level
read-only mount of any shared inputs is a cheap defense-in-depth addition worth
considering at implementation time.

### 4.4 Hard dependency: agent image must ship a Josh build with `mcp`

`command: ["josh", "mcp"]` only works if the `fortree:agent` image bundles a Josh
fat jar whose `JoshSimCommander` includes the `McpCommand` subcommand. The image
gets its jar from [scripts/install_josh.sh](scripts/install_josh.sh), which pins a
**rolling `main` fat jar** by sha256 at build time (Dockerfile:55–57). But PR #440
targets Josh **`dev`** — so `josh mcp` does **not** reach the agent image until
#440 propagates `dev` → `main` *and* `install_josh.sh`'s pin is bumped to a build
that includes it. Concretely, the probe is blocked until one of: (a) #440 merges
and `dev`→`main` flows, then we bump the pin; or (b) point `install_josh.sh` at
the `dev` build now. **We've taken (b)** (2026-05-27, commit `320c81b` on
`feat/k8s-refactor`): the shared base stage now defaults to the dev jar via a
`JOSH_JAR_URL` build arg (Dockerfile:55), and CI confirms both `fortree-agent`
and `fortree-scorer` bundle it — the built jar's sha256 (`2c42dcdf…`) matches the
dev jar `pixi run get-jars` fetches, which carries the MCP classes
(`org/joshsim/mcp/*` + the bundled SDK; `main` carries none). `pixi run get-jars`
([scripts/get_jars.py](scripts/get_jars.py)) refreshes both rolling builds into
`jar/<branch>/` with sha256 sidecars for local smoke / re-pinning. The four
exposed tools are known (§1); `run_simulation`'s `--data` gap (§4.2) is the
surface limitation to design around.

## 5. The netCDF spec sheet

**Contents (descriptive-only).** A structured block formalizing, per input file:

- path (`data/maxtemp_synthetic.nc`, `data/precip_synthetic.nc`);
- data variable + units (`tasmax`, K; `pr`, kg m⁻² s⁻¹) and the precip→mm/yr
  conversion (×31\_536\_000);
- dimensions and sizes (`calendar_year`, `lat`, `lon`), coordinate ranges and
  bounding box, and the 2024–2123 simulation window;
- one **validated sample row** (a concrete `(year, lat, lon) → value`) so the
  model can sanity-check its indexing without opening the file.

It does **not** include aggregation recipes or data-binding code (Decision 2).

**Where it lives.** In a formalized [prompts/SIDECAR.md](prompts/SIDECAR.md)
§AI Inputs, which is shared by all arms — so josh, mesa, and josh-mcp all receive
identical input information.

**Validation.** A jupyter notebook asserts every claim in the spec sheet against
the actual `.nc` files and fails loudly on drift. It builds on the existing
[data/validate_synthetic_climate.py](data/validate_synthetic_climate.py), which
already checks dims (`{calendar_year: 31, lat: 31, lon: 50}`), units, and the
lat/lon bounding box. The notebook is a **human/CI maintenance artifact** — it is
*not* something the agent runs.

## 6. Integration touch points

| Where | Change | Notes |
|---|---|---|
| `config/opencode.josh-mcp.template.json` *(new)* | Constrained palette + `mcp.josh` block (§4.1) | Sibling of the existing template; only bash/webfetch off + MCP block + `josh*` on differ |
| [orchestration/render_jobs.py](orchestration/render_jobs.py) (`VALID_TARGETS`, ~L116) | `("josh", "mesa", "josh-mcp")` | |
| [orchestration/render_jobs.py](orchestration/render_jobs.py) (`_render_opencode_json`, ~L148–152) | Select template by target — `josh-mcp` → new template | thread `target` through |
| [orchestration/render_jobs.py](orchestration/render_jobs.py) (`_render_prompt_body`, ~L137–145) | Read `prompts/targets/josh-mcp.md` | new directive file |
| `prompts/targets/josh-mcp.md` *(new)* | Josh directive + "no bash; self-test via the `josh_*` MCP tools; still author `run.sh` declaratively" | |
| [prompts/SIDECAR.md](prompts/SIDECAR.md) (§AI Inputs ~L19–38; self-test L13, L54) | Formalize the descriptive spec sheet; reconcile the bash-centric "invoke `./run.sh` to self-test" language for the no-bash arm (override in `josh-mcp.md`, or a SIDECAR variant) | |
| [harness/conformance.py](harness/conformance.py) (`check` dispatch, ~L93–109) | Route `josh-mcp` → `_check_josh` | identical conformance keeps the contrast clean |
| [harness/run_metrics.py](harness/run_metrics.py) (`--target` choices, ~L100) | Add `josh-mcp` | scoring path otherwise unchanged |
| [prompts/FUZZY_JUDGE.md](prompts/FUZZY_JUDGE.md) | josh-mcp-aware variant: recognize MCP-invoked preprocess/run, not only CLI calls in `run.sh` | **real edit** — else it under-counts conformance |
| *(jupyter notebook, new)* + [data/validate_synthetic_climate.py](data/validate_synthetic_climate.py) | Notebook asserts spec-sheet claims against the `.nc` files | extends the existing validator |
| [orchestration/matrix.csv](orchestration/matrix.csv) | Add `josh-mcp` rows | **only when promoted** from probe to headline |
| [EXPERIMENTAL_DESIGN.md](EXPERIMENTAL_DESIGN.md) / [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Document the environment factor, the partial-factorial framing, the three contrasts | |

**No change needed:** [orchestration/templates/job.yaml.j2](orchestration/templates/job.yaml.j2)
and [containers/scorer-and-upload.sh](containers/scorer-and-upload.sh) — `target`
already flows through as a label and a `--target` arg, and the `josh-mcp` slug is
hyphen-safe for the cell-id slugifier.

## 7. Risks & mitigations

- **Constraint may *hurt*.** Removing bash removes the agent's debug/iterate loop
  (no live `./run.sh`, no ad-hoc inspection). `josh-mcp` could score *worse* than
  `josh`. **Mitigation:** pre-register `josh-mcp` as a *question*, reported as a
  **delta vs `josh`**, not as a winner. A small gap is the strong product result
  ("you don't need to hand the agent a shell"); a large gap is the interesting
  finding ("the MCP surface must expose more — e.g. an output-shape inspector — to
  be viable"). Either outcome is informative.
- **v1 MCP surface gaps (PR #440).** Two confirmed limits bite the self-test
  loop: (1) `run_simulation` has **no `--data`**, so the agent can only faithfully
  self-test a simulation whose data binding is script/`.jshc`-resident (§4.2) —
  the josh-mcp directive must enforce that, and we should re-check after the
  `--data` follow-up lands whether to relax it; (2) `inspect_exports` /
  `inspect_jshd` are **Phase 3**, so the agent has no MCP tool to confirm its
  output CSV's shape — it must infer success from the `run_simulation` result
  alone. **Mitigation:** lean on the descriptive spec sheet + a precise
  `josh-mcp.md` directive; treat both as inputs to the "what must the MCP surface
  expose to be a viable product" finding (the delta read, below).
- **Agent-vs-scorer wall-clock gap widens.** The headline cost metric (`./run.sh`
  wall-clock) is measured in the scorer and is environment-independent, but a
  no-bash agent may never have run `./run.sh` itself. Flag that josh-mcp's
  agent-phase self-test happened via MCP (or not at all) so the cost metric isn't
  misread.
- **Fuzzy judge under-counts.** [prompts/FUZZY_JUDGE.md](prompts/FUZZY_JUDGE.md)
  currently looks for `josh preprocess` / `josh run` *CLI calls in `run.sh`*. For
  josh-mcp those happen as MCP tool calls. The judge prompt needs a josh-mcp-aware
  variant or it will mark conformant cells as non-conformant. **This is a real
  edit, not optional.**
- **Re-baseline.** The shared-SIDECAR spec sheet changes `josh`/`mesa` too — bump
  the batch tag and re-run all three arms; do not compare against the in-flight
  Phase-6 batch.

## 8. Sequencing

1. **Probe sub-batch.** 1–2 models × `josh-mcp` × 2–3 reps, `FAIL_FAST=false`,
   Balanced compute class (cheaper than Performance) — following the gemma
   single-cell probe precedent. Render via `--single-cell`, no matrix edit.
2. **Promote.** Only after the probe confirms tool engagement + conformant `.josh`
   output, add `josh-mcp` rows to [orchestration/matrix.csv](orchestration/matrix.csv).
   This grows the target axis by 50% (e.g. 100 → 150 cells at 10 reps); josh-mcp
   cells inherit Josh's JVM cost/OOM profile, so budget accordingly.

## 9. Verification

1. **Render check.**
   `pixi run render -- --batch-tag josh-mcp-probe --single-cell model=sonnet,target=josh-mcp …`
   then assert the rendered `opencode.json` carries the `mcp.josh` block with
   `bash:false`, and that `prompt_body.md` contains the josh-mcp directive + the
   descriptive spec sheet.
2. **Local opencode smoke.** Run the agent with the josh-mcp config against a
   trivial workspace; confirm opencode spawns `josh mcp`, exposes the `josh_*`
   tools, the bash tool is absent, and `trajectory.jsonl` shows MCP tool calls.
3. **Notebook.** Execute it; confirm it asserts every spec-sheet claim against the
   real `.nc` files and fails on drift.
4. **GKE probe batch.** 1–2 `target=josh-mcp` cells; confirm `did_run`,
   `target_conformance` via `_check_josh`, the ecology regression, `scorer.json`
   `schema_version = phase6-v1`, and that the trajectory used MCP tools (no bash).
5. **Delta read.** Compare `josh-mcp` vs `josh` success-rate as the pre-registered
   question — a delta to interpret, not a pass/fail gate.

## 10. Open questions & dependencies

- **Josh-side MCP server readiness — mostly resolved by PR #440.** Known: the
  four tools, stdio transport, `dev` base, SDK 1.1.3. Remaining: the `dev`→`main`
  propagation + `install_josh.sh` pin bump that actually puts `josh mcp` in the
  agent image (§4.4) — the hard prerequisite for the probe.
- **Does `run_simulation` honor `.jshc`-resident data binding without `--data`?**
  The arm's viability hinges on this (§4.2): can a `.josh`/`.jshc` reference its
  preprocessed `.jshd` files such that `josh_run_simulation` (which passes only
  `script`/`simulation`) resolves the climate inputs with no runtime `--data`?
  Verify against Josh docs (`llms-full.txt`) and the `examples/test_mcp.sh` smoke
  on the Josh side *before* the probe — if `--data` turns out to be mandatory for
  this sim, the probe waits on the `--data` follow-up.
- **Fuzzy-judge edit timing.** Land the josh-mcp-aware judge variant *with* the
  probe (so probe conformance is scored correctly) vs after (accepting that probe
  fuzzy-conformance is noisy). Recommend: with the probe.
- **Self-test language.** Whether to override SIDECAR's "invoke `./run.sh` to
  self-test" purely in `prompts/targets/josh-mcp.md`, or to introduce a SIDECAR
  variant. Recommend: override in the target directive to keep SIDECAR
  single-source.

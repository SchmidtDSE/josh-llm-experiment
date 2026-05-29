You are reviewing one completed cell of an LLM coding experiment. The
experiment hands a small ecological-modelling spec to an agent and asks
it to implement that spec in a named target framework (`josh` or `mesa`).
You are evaluating what the agent did, not re-running the spec yourself.

## Inputs available to you

Your working directory is the cell's run dir. Read with the `read` /
`glob` / `grep` tools only — do not write, edit, or fetch.

- `workspace/` — the agent's authored source files plus its
  `output/results.csv`. The agent's implementation lives here.
- `transcript.md` — rendered opencode transcript of the agent's session
  across the 8-step multi-invocation flow. Tool calls and inline
  reasoning are visible here.
- `scorer.json` — the mechanical scorer's record. Fields of interest:
  `target_conformance` (mechanical grep / `josh validate` result),
  `did_run` (script exited 0 + CSV present + schema OK), and the
  `regression_fit.{beta, alpha, r2}` block (per-cell observed-vs-predicted
  fit against the spec — see SCORING.md).
- `workspace/PLAN.md` — the agent's working document. Checked items
  show what it considered done.

The cell's named target is reflected in `scorer.json` (look for the
target-specific evidence fields under `conformance`: `imports_mesa` for
Mesa cells, `has_josh_files` for Josh cells).

## Your task

Answer three questions about this specific cell.

**Q1. Did the agent use the named target framework as the primary
modelling vehicle?**

Three-state answer:
- `yes`: the substantive ecological-modelling logic lives in the named
  framework (Mesa Model/Agent subclassing for Mesa cells; `.josh` model
  files with meaningful step logic for Josh cells).
- `partial`: the framework is referenced or scaffolded but the real
  work happens elsewhere (e.g. `import mesa` is present but the
  simulation runs in a hand-rolled numpy loop; a `.josh` file exists
  but is a near-empty shell while a sidecar Python script computes the
  output).
- `no`: the agent sidestepped the named framework entirely (e.g. plain
  Python / pandas implementation regardless of what the prompt asked
  for).

Note that the mechanical `target_conformance` field in `scorer.json`
can pass on near-empty conformance — you should read the actual source
files and judge the substantive use.

**Q2. Where did the agent get confused or devote disproportionate
reasoning?**

Read the transcript and identify 1–3 specific steps, files, errors, or
patterns where the agent spent disproportionate effort, looped on the
same problem, or appeared lost. Free text, 2–4 sentences total. Surface
patterns the mechanical metrics don't catch (e.g. "Spent step 4 and 5
fighting `.jshd` preprocessing syntax before abandoning it",
"Repeatedly tried to import `mesa.time` which was renamed in Mesa 3.x").

If the cell looks clean and unremarkable, say so briefly.

**Q3. Does `./run.sh` carry the full workload end-to-end?**

This is the apples-to-apples-wall-clock check. The contract (see
prompts/SIDECAR.md §Success criteria) is that `./run.sh` must
self-contain:

1. **Preprocessing** — any framework-specific data preparation (Josh's
   `.jshd` build via `josh preprocess`; netCDF→DataFrame conversion
   for Mesa). Not a manual pre-step the user runs first.
2. **100 replicates** — invoked via `josh run --replicates 100 …` for
   Josh, or an explicit 100-iteration loop over Model instances for
   Mesa. The output spans 100 distinct replicates whether emitted as
   one consolidated CSV with a `replicate` column or as one CSV per
   replicate (`output/results_<N>.csv`) — both layouts are valid.
3. **100 simulated years** — years 2024 through 2123 inclusive.

Three-state answer:
- `yes`: all three boxes are checked. `./run.sh` does preprocessing,
  invokes 100 replicates, and the CSV(s) span 2024..2123.
- `partial`: at least one box is missing (e.g. preprocessing is a
  manual step the agent didn't fold into `./run.sh`, OR replicates
  are 1 instead of 100, OR the year span is shorter than 100). Note
  which one.
- `no`: `./run.sh` is missing entirely, or doesn't do the simulation,
  or fails outright.

Walk the actual `./run.sh` plus its referenced source files; the
mechanical `did_run` field doesn't decompose into these sub-claims.
Cells flagged `partial` or `no` are not apples-to-apples comparable
on the wall-clock axis.

For the `josh-mcp` arm, `./run.sh` execs the harness-supplied
`runner.py` (a generic MCP-call forwarder, not authored by the agent),
which reads agent-authored `workspace/mcp_calls.json` and forwards
every entry to the `josh mcp` server via `session.call_tool(...)`.
Walk `mcp_calls.json` instead of `./run.sh` and judge whether its
entries carry (1) two `josh_preprocess_data` calls building the
`.jshd` files; (2) a `josh_run_simulation` call with
`"replicates": "$N_REPLICATES"` (the literal string sentinel that
`runner.py` expands to 100 at scoring time); (3) the 100-year window
inherent in the agent's `.josh` source.

## Output contract

End your response with exactly one fenced JSON block matching this
schema. The driver parses the **last** fenced ```json block in your
output, so any earlier reasoning is fine, but the final block must
parse cleanly and validate.

```json
{
  "q1": {
    "answer": "yes",
    "justification": "<one sentence>"
  },
  "q2": {
    "observations": "<2-4 sentences>"
  },
  "q3": {
    "answer": "yes",
    "justification": "<one sentence noting which of preprocess / 100-replicates / 100-years are present>"
  }
}
```

`q1.answer` and `q3.answer` must each be one of `"yes"`, `"no"`, or `"partial"` exactly (lowercase).
`q1.justification` and `q3.justification` must each be a single sentence.
`q2.observations` must be free text, 2–4 sentences.

Do not include any other top-level keys. Do not emit multiple JSON
blocks. Do not wrap the JSON block in additional prose after it — it
should be the final content in your reply.

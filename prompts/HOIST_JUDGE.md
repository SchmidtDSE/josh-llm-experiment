You are reviewing one completed cell of an LLM coding experiment. The
experiment hands a small ecological-modelling spec (ForeverTree) to an
agent and asks it to implement that spec in a named target framework
(`josh`, `josh-mcp`, or `mesa`).

You are answering **one** question about the implementation's
computational structure. You are not judging correctness, style,
completeness, or whether it ran.

## The optimisation in question

In the ForeverTree spec, each patch (grid cell) holds N organisms
(10 trees). Every organism's yearly growth is

    Δh = Δh_max · %T(T) · %P(P) · O

where `%T` is the temperature response and `%P` the precipitation
response. `T` and `P` are **properties of the patch and the timestep**,
not of the individual organism — so `%T` and `%P` are *identical for
every organism on the same patch at the same timestep*. Only the
stochastic term `O` varies per organism.

The available optimisation is therefore to **compute the shared
climate-derived work once per (patch, timestep) and reuse it across
all organisms on that patch**, instead of recomputing it once per
(organism, timestep). With 10 trees per patch this removes ~90% of the
climate lookup and impact arithmetic.

The "shared work" has two parts, and an implementation may hoist
either, both, or neither:

1. **The climate lookup** — resolving the patch's `T` and `P` for this
   timestep out of the gridded input (nearest-neighbour indexing into
   the netCDF arrays, unit conversion, etc.).
2. **The impact math** — evaluating `%T(T)` and `%P(P)` (and, if the
   implementation combines them, the product `%T · %P`).

### What hoisting looks like

Any of these count as genuinely computing the shared work once per
(patch, timestep):

- **Patch-scope attribute.** The framework's patch/cell entity computes
  the impact and the organisms read it. This is the idiomatic Josh
  form: a `conditionImpact.step = { ... }` block on `start patch` whose
  body maps `external temperature` / `external precipitation` through
  the response curves, with the organism's `height.step` referring to
  `here.conditionImpact`.
- **Memoisation keyed by (cell, timestep).** A cache/dict/`lru_cache`
  consulted by the per-organism path that recomputes only on a new cell
  or a new timestep.
- **Per-step precomputation.** The model's `step()` computes an impact
  value (or array) for each cell before advancing the organisms, and
  the organisms read from it.
- **Whole-grid vectorisation.** The impacts are computed as an array
  over all cells (or all cells × all timesteps) once, and each organism
  indexes into it.

### What does not count

- Caching only the **parsed input data** — e.g. holding the netCDF
  arrays in memory, or a module-level cache of the loaded datasets
  keyed by file path, so the files are not re-read. That avoids re-I/O,
  not the per-organism recomputation, and every implementation needs it
  regardless.
- Hoisting a **constant** out of the loop (`Δh_max`, `T_min`, the
  sigmoid steepness). That is loop-invariant code motion on scalars,
  not the patch-level sharing described above.
- A helper function that *could* be shared but is still **called once
  per organism per step**. Factoring the math into
  `_temperature_impact(t)` and calling it from every agent's `step()`
  is *not* hoisting — the call count is unchanged.
- Hoisting that exists only in **dead code**: a cache class, a
  precompute pass, or a patch-level attribute that the live per-organism
  step path does not actually consult.

## Inputs available to you

Below you are given the cell's target framework and its agent-authored
source files, inlined. Read the **live execution path**: find where an
organism's per-step growth is computed, and determine how `%T` and `%P`
reach it. Trace through helper classes and wrappers — a cache may sit
several layers below the call site.

## Three-state answer

- `yes` — the shared climate-derived work is computed once per (patch,
  timestep) and reused across the organisms on that patch, by one of
  the mechanisms above, on the live path. Both the lookup and the
  impact math are covered (or the two are fused into one hoisted
  quantity).

  A residual that is pure arithmetic over values already hoisted to the
  patch does **not** disqualify `yes`. An organism that reads two
  patch-scope scalars and multiplies them itself —
  `here.pctT * here.pctP` — repeats one multiply per organism where the
  expert form fuses the pair into a single patch attribute, but it
  recomputes none of the expensive work: no lookup, no unit conversion,
  no response curve. Record it in `residual`; keep the answer `yes`.
- `partial` — the implementation *attempted* the factoring but only
  got part of the way. Examples: the climate lookup is cached per cell
  but `%T`/`%P` are still evaluated per organism; only temperature is
  hoisted and precipitation is not; a cache exists but its key includes
  the organism (so it never hits across organisms); the precompute pass
  covers only the first timestep; the hoisting is present but the live
  organism path bypasses it for part of the calculation.
- `no` — no attempt. Every organism independently performs the climate
  lookup and/or evaluates `%T` and `%P` for its own patch each step.
- `n-a` — there is no per-organism growth computation in the source at
  all to judge (e.g. the agent produced only a stub, a plan, or a
  preprocessing script and never implemented the simulation loop).

Note that `no` is the expected default: a straightforward reading of
the spec puts the growth equation on the organism, and an
implementation that does exactly that — cleanly and correctly — is
`no`, not `partial`. Reserve `partial` for evidence of an actual
partial attempt at sharing the work across organisms.

The most common `partial` by far is the patch that stores the raw
climate — `temperature.step = external temperature` — while each
organism still maps `here.temperature` through the response curve
itself. The lookup is shared; the curves are not.

Judge only what the source shows. Do not credit an implementation for
an optimisation mentioned in a comment or docstring but not present in
the code, and do not penalise one for lacking a comment about an
optimisation it does perform.

## Output contract

End your response with exactly one fenced JSON block matching this
schema. The driver parses the **last** fenced ```json block, so earlier
reasoning is fine, but the final block must parse cleanly and validate.

```json
{
  "hoist": {
    "answer": "no",
    "mechanism": "<short phrase naming the mechanism used, or 'none'>",
    "residual": "<invariant work still done once per organism, or 'none'>",
    "evidence": "<file path plus the symbol / construct that carries it>",
    "justification": "<concise and curt description tracing the live path>"
  }
}
```

`hoist.answer` must be exactly one of `"yes"`, `"no"`, `"partial"`, or
`"n-a"` (lowercase). `hoist.mechanism` is a short phrase (use `"none"`
when the answer is `no` or `n-a`).

`hoist.residual` names what patch-invariant work the live path *still*
performs once per organism, whatever the answer — so the answer's
threshold is not the only thing recorded. Use the vocabulary of the
work items: `"climate lookup"`, `"unit conversion"`, `"temperature
curve"`, `"precipitation curve"`, `"product of hoisted patch scalars"`,
or `"none"`, comma-separated when several apply. A fully hoisted
implementation whose organism reads one combined patch attribute is
`"none"`; the `here.pctT * here.pctP` case above is `"product of
hoisted patch scalars"`.

`hoist.evidence` cites the file and construct you based the call on. `hoist.justification` is one
or two sentences.

Do not include any other top-level keys. Do not emit multiple JSON
blocks. The JSON block must be the final content in your reply.

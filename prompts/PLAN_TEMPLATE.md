# Working document

This file is the shared plan across agent invocations. Every agent that runs reads this document, completes its assigned todo item, summarises its work under that todo in 1–2 sentences, and changes the checkbox from `[ ]` to `[x]`. Do not edit todos other than your assigned one. The `## Plan` section below is for agents to append architectural decisions and notes that later steps need.

## Todo list

The 8 todos below are the full description of what each step entails. Each step's prompt only names the todo number; the work to do lives here. Read this list carefully before starting your assigned step.

- [ ] **1. Bootstrap the plan section.** Add a high-level outline to the `## Plan` section below describing what the eventual simulation needs to cover. Sketch the major pieces but leave the details to later todos — this step is about creating the scaffolding the next three planning todos will fill in.

- [ ] **2. Describe how to work with geospatial data.** Add a subsection under `## Plan` covering: which netCDF files the simulation reads from `data/`, and how those data will actually reach the simulation runtime.

- [ ] **3. Describe the structure of the model to be built.** Add a subsection under `## Plan` covering: the entities your implementation will have, how state is initialised *before* the simulation starts (10 trees per patch at age=0, height=0 — this initial state is not a CSV row), what happens on each growth step (years 2024–2123 inclusive, one growth event per year per tree using that year's climate, CSV rows record post-growth state), and how per-cell statistics are aggregated into the required CSV columns. Be explicit about replication: 100 independent replicates of the full 100-year run, each replicate gets its own per-tree-per-year stochastic-offset draws, climate and initial conditions are identical across replicates.

- [ ] **4. Describe the commands required to complete the task and run the simulation.** Add a subsection under `## Plan` listing the exact shell or Josh CLI subcommands needed to build, run, and self-validate the simulation. **`./run.sh` is the unit of measurement** — it must invoke (a) any preprocessing the framework needs (Josh's `.jshd` build, or netCDF→DataFrame conversion for Mesa), (b) the simulation for **100 replicates × 100 years (2024–2123 inclusive)**, and (c) emission of `./output/results.csv` with one row per `(cell, year, replicate)`. Include the `chmod +x run.sh` step and the self-test invocation. No work that the user is expected to do by hand outside `./run.sh`.

- [ ] **5. Make a stubbed simulation that runs end-to-end without the real growth logic.** Author the source files and `./run.sh` so the workspace executes cleanly, but use **placeholder** growth values (e.g. constant 0, or a trivial linear function) rather than the real equation. The stub may use **1 replicate × the full 100-year span** to keep iteration fast — replicates get scaled up to 100 in todo 6. `chmod +x run.sh` and invoke it once yourself to confirm it exits 0 and writes the CSV at the expected path. The primary goal here is to ensure the **environment** is properly configured for you to iterate on the model logic itself in a later step.

- [ ] **6. Complete the stubbed simulation with the real growth logic and scale to 100 replicates.** Replace the placeholder growth with the full climate-aware behavior requested in the spec, **and** flip `./run.sh` to run with 100 replicates (Josh: `--replicates 100`; Mesa: a loop over 100 independent Model instances writing the `replicate` column). The CSV must now reflect real `meanHeight` / `meanAge` values that vary by cell and year, and must contain `n_cells × 100 years × 100 replicates` rows. Invoke `./run.sh` to confirm it still exits 0 and that the CSV has the expected shape.

- [ ] **7. Validate that the outputs are as expected.** Spot-check `./output/results.csv` against the spec. Verify: (a) one row per `(cell, year, replicate)`, (b) year 2024 rows already show `meanAge = 1` and a non-zero `meanHeight` (one growth event has occurred — the h=0 init state is *before* the sim and not a row), (c) year 2123 rows reflect 100 accumulated growth events, (d) `nTrees = 10` throughout (no death/reproduction), (e) climate values vary by cell and year as expected. Fix any discrepancies you find in the source files and re-run `./run.sh` until the CSV passes your own checks.

- [ ] **8. Clean up the code to ensure it is readable and clean.** Remove dead code, debug prints, and stub-era placeholders; ensure variable / function names are self-documenting. Re-run `./run.sh` one final time to confirm the cleanup did not break anything.

## Plan

_(empty — agents fill this in starting with todo 1.)_

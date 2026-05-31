# Working document

This file is the shared plan across agent invocations. Every agent that runs reads this document, completes its assigned todo item, summarises its work under that todo in 1–2 sentences, and changes the checkbox from `[ ]` to `[x]`. Do not edit todos other than your assigned one. The `## Plan` section below is for agents to append architectural decisions and notes that later steps need.

## Todo list

The 8 todos below are the full description of what each step entails. Each step's prompt only names the todo number; the work to do lives here. Read this list carefully before starting your assigned step.

- [ ] **1. Bootstrap the plan section.** Add a high-level outline to the `## Plan` section below describing what the eventual simulation needs to cover. Sketch the major pieces but leave the details to later todos — this step is about creating the scaffolding the next three planning todos will fill in.

- [ ] **2. Describe how to work with geospatial data.** Add a subsection under `## Plan` covering: which netCDF files the simulation reads from `data/`, and how those data will actually reach the simulation runtime.

- [ ] **3. Describe the structure of the model to be built.** Add a subsection under `## Plan` covering: the entities your implementation will have, how state is initialised (10 trees per patch at age=0, height=0), what happens on each step, and how per-cell statistics are aggregated into the required CSV columns. Be explicit about replication: independent replicates of the full 100-year run, each replicate gets its own per-tree-per-year stochastic-offset draws; climate and initial conditions are identical across replicates.

- [ ] **4. Describe the commands required to complete the task and run the simulation.** Add a subsection under `## Plan` listing the exact shell or Josh CLI subcommands needed to build, run, and self-validate the simulation. **`./run.sh` is the unit of measurement** and is already seeded for you in the workspace — read it before authoring this todo. It is already executable and already reads `${N_REPLICATES:-2}`; your job is to fill in the body so it invokes (a) any preprocessing the framework needs, (b) the simulation using `"$N_REPLICATES"` for the replicate count, and (c) emission of `./output/results.csv` (or per-replicate CSVs) with one row per `(cell, year, replicate)`.

- [ ] **5. Make a stubbed simulation that runs end-to-end without the real growth logic.** Author the source files and fill in the body of the seeded `./run.sh` so the workspace executes cleanly, but use **placeholder** growth values (e.g. constant 0, or a trivial linear function) rather than the real equation. Invoke `./run.sh` to confirm it exits 0 and writes the CSV at the expected path. The primary goal here is to ensure the **environment** is properly configured for you to iterate on the model logic itself in a later step.

- [ ] **6. Complete the stubbed simulation with the real growth logic.** Replace the placeholder growth with the full climate-aware behavior requested in the spec. The CSV must now reflect real `meanHeight` / `meanAge` values that vary by cell and year. Re-invoke `./run.sh` to confirm the CSV has the expected shape under multiple replicates.

- [ ] **7. Validate that the outputs are as expected.** Spot-check `./output/results.csv` against the spec. Verify: (a) one row per `(cell, year, replicate)`, (b) `meanHeight` grows over time within each cell, (c) `nTrees = 10` throughout (no death/reproduction), (d) climate values by cell and year, (e) the two replicates (0 and 1) have independent stochastic draws. Fix any discrepancies you find in the source files and re-run `./run.sh` until the CSV passes your own checks.

- [ ] **8. Clean up the code to ensure it is readable and clean.** This is an *edit-in-place* step on the files you authored — **do not delete files from `/sandbox/`**. The files that MUST be present at the end of this step:
  - `/sandbox/run.sh` *(the scorer invokes this directly — never delete; edit only)*
  - your source files (`.josh` / `.jshd` for Josh; `.py` for Mesa) and anything `run.sh` imports or invokes

  The cleanup scope is **inside** your source and `run.sh`: remove dead code, debug prints, scratch/test variants (e.g. `test.josh`, `scratch.py`), and stub-era placeholders; ensure variable / function names are self-documenting. Re-run `./run.sh` one final time to confirm the cleanup did not break anything.

## Plan

_(empty — agents fill this in starting with todo 1.)_

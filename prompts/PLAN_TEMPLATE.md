# Working document

This file is the shared plan across agent invocations. Every agent that runs reads this document, completes its assigned todo item, summarises its work under that todo in 1–2 sentences, and changes the checkbox from `[ ]` to `[x]`. Do not edit todos other than your assigned one. The `## Plan` section below is for agents to append architectural decisions and notes that later steps need.

## Todo list

The 8 todos below are the full description of what each step entails. Each step's prompt only names the todo number; the work to do lives here. Read this list carefully before starting your assigned step.

- [ ] **1. Bootstrap the plan section.** Add a high-level outline to the `## Plan` section below describing what the eventual simulation needs to cover. Sketch the major pieces but leave the details to later todos — this step is about creating the scaffolding the next three planning todos will fill in.

- [ ] **2. Describe how to work with geospatial data.** Add a subsection under `## Plan` covering: which netCDF files the simulation reads from `data/`, and how those data will actually reach the simulation runtime.

- [ ] **3. Describe the structure of the model to be built.** Add a subsection under `## Plan` covering: the entities your implementation will have, how state is initialised at year 0 (e.g. 10 trees per patch, age = 0, height = 0), what happens on each step, and how per-cell statistics are aggregated into the required CSV columns.

- [ ] **4. Describe the commands required to complete the task and run the simulation.** Add a subsection under `## Plan` listing the exact shell or Josh CLI subcommands needed to build, run, and self-validate the simulation. Include what `./run.sh` will invoke, the `chmod +x run.sh` step, and the self-test invocation. This subsection is what later code-writing todos will execute.

- [ ] **5. Make a stubbed simulation that runs end-to-end without the real growth logic.** Author the source files and `./run.sh` so the workspace executes cleanly: read the netCDFs, walk every `(year, cell)`, write a `./output/results.csv` with the required columns and one row per cell-year, but use **placeholder** growth values (e.g. constant 0, or a trivial linear function) rather than the real equation. `chmod +x run.sh` and invoke it once yourself to confirm it exits 0 and writes the CSV at the expected path.

- [ ] **6. Complete the stubbed simulation with the real growth logic.** Replace the placeholder growth with the full equation from the spec: clamped temperature impact (quadratic in normalised window), logistic precipitation impact, multiplicative Gaussian stochasticity, age increment of 1/year, height accumulation across years. The CSV must now reflect real `meanHeight` / `meanAge` values that vary by cell and year. Invoke `./run.sh` to confirm it still exits 0 and that the CSV looks non-trivial.

- [ ] **7. Validate that the outputs are as expected.** Spot-check `./output/results.csv` against the spec: all required columns present, one row per `(cell, year)`. Fix any discrepancies you find in the source files and re-run `./run.sh` until the CSV passes your own checks.

- [ ] **8. Clean up the code to ensure it is readable and clean.** Remove dead code, debug prints, and stub-era placeholders; ensure variable / function names are self-documenting. Re-run `./run.sh` one final time to confirm the cleanup did not break anything.

## Plan

_(empty — agents fill this in starting with todo 1.)_

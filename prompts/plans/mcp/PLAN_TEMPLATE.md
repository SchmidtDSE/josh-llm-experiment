# Working document

This file is the shared plan across agent invocations. Every agent that runs reads this document, completes its assigned todo item, summarises its work under that todo in 1–2 sentences, and changes the checkbox from `[ ]` to `[x]`. Do not edit todos other than your assigned one. The `## Plan` section below is for agents to append architectural decisions and notes that later steps need.

## Todo list

The 8 todos below are the full description of what each step entails. Each step's prompt only names the todo number; the work to do lives here. Read this list carefully before starting your assigned step.

- [ ] **1. Bootstrap the plan section.** Add a high-level outline to the `## Plan` section below describing what the eventual simulation needs to cover. Sketch the major pieces but leave the details to later todos — this step is about creating the scaffolding the next three planning todos will fill in.

- [ ] **2. Describe how to work with geospatial data.** Add a subsection under `## Plan` covering: which netCDF files the simulation reads from `data/`, and how those data will actually reach the simulation runtime.

- [ ] **3. Describe the structure of the model to be built.** Add a subsection under `## Plan` covering: the entities your implementation will have, how state is initialised (10 trees per patch at age=0, height=0), what happens on each step, and how per-cell statistics are aggregated into the required CSV columns. Be explicit about replication: independent replicates of the full 100-year run, each replicate gets its own per-tree-per-year stochastic-offset draws; climate and initial conditions are identical across replicates.

- [ ] **4. Describe how you will build and exercise the model.** Add a subsection under `## Plan` describing the sequence of Josh MCP tool calls you'll use to stand the model up — validating the script, building the preprocessed inputs from the netCDFs, and running the simulation — and how you'll confirm it produced the expected output. You have no shell and author no run script; you build and exercise the model directly through the MCP tools.

- [ ] **5. Make a stubbed simulation that runs end-to-end without the real growth logic.** Author the source files and use the Josh MCP tools to run the model with **placeholder** growth values (e.g. constant 0, or a trivial linear function) rather than the real equation, confirming it executes cleanly and writes the output CSV(s) at the expected path. The primary goal here is to ensure the **environment** and data binding are wired correctly before you iterate on the model logic itself.

- [ ] **6. Complete the stubbed simulation with the real growth logic.** Replace the placeholder growth with the full climate-aware behavior requested in the spec. The CSV must now reflect real `meanHeight` / `meanAge` values that vary by cell and year. Re-run the model through the MCP tools to confirm the CSV has the expected shape under multiple replicates.

- [ ] **7. Validate that the outputs are as expected.** Spot-check `output/results.csv` (or the per-replicate CSVs) against the spec. Verify: (a) one row per `(cell, year, replicate)`, (b) `meanHeight` grows over time within each cell, (c) `nTrees = 10` throughout (no death/reproduction), (d) climate values vary by cell and year as expected, (e) the replicates have independent stochastic draws. Fix any discrepancies you find in the source files and re-run through the MCP tools until the output passes your own checks.

- [ ] **8. Author `/sandbox/mcp_calls.json` for the scorer's reproducer run.** The harness provides a generic Python runner at `/sandbox/runner.py` that forwards every JSON entry to the `josh mcp` server via the Python MCP client. Write `mcp_calls.json` as a JSON array of `{"tool": "...", "arguments": {...}}` capturing the *exact* MCP tool calls you used to (a) preprocess your two externals into `.jshd` files (two `josh_preprocess_data` entries) and (b) run the simulation (one `josh_run_simulation` entry). Use the literal string `"$N_REPLICATES"` as the value of the `replicates` argument so the scorer can override it to 100 at scoring time. Reuse the exact paths, variable names, and units you passed during your self-test — these are part of the task; the harness does not supply them. You can't execute the runner yourself (no shell), but a consistent `mcp_calls.json` will reproduce your validated self-test.

- [ ] **9. Clean up the code to ensure it is readable and clean.** Remove dead code, debug prints, and stub-era placeholders from your `.josh` source and `mcp_calls.json`; ensure variable / external / file names are self-documenting. Re-run the model through the MCP tools one final time to confirm the cleanup did not break anything.

## Plan

_(empty — agents fill this in starting with todo 1.)_

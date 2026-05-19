---

## Your assigned todo for this invocation

**Todo 5: Make a stubbed simulation that runs without the logic using the chosen library.**

First, read `/sandbox/PLAN.md` — the previous four todos should have left a coherent plan. Briefly list the actions you intend to take, then carry them out. Author the source files and `./run.sh` so the stub executes end-to-end: it reads the netCDFs, walks every `(year, cell)`, writes a `./output/results.csv` with the required columns and one row per cell-year, but uses placeholder (e.g. constant 0 or NaN) growth values rather than the real growth equation. Make `run.sh` executable (`chmod +x run.sh`) and invoke it once yourself to confirm it exits 0 and writes the CSV. When finished, change `- [ ] 5.` to `- [x] 5.` in the todo list and append a 1–2 sentence summary under that line. Do not touch any other todo. Then exit.

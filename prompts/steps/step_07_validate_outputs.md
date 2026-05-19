---

## Your assigned todo for this invocation

**Todo 7: Validate that the outputs are as expected.**

First, read `/sandbox/PLAN.md`. Briefly list the actions you intend to take, then carry them out. Spot-check `./output/results.csv` against the spec: required columns present and in the right order, one row per (cell, year) for years 2024–2034 inclusive, `meanHeight` strictly non-decreasing along each cell's year trajectory, climate values in plausible ranges (temperature in Kelvin, precipitation as mm/year after the `× 31_536_000` conversion). Fix any discrepancies in the source files and re-run `./run.sh` until the CSV passes your own checks. When finished, change `- [ ] 7.` to `- [x] 7.` in the todo list and append a 1–2 sentence summary under that line. Do not touch any other todo. Then exit.

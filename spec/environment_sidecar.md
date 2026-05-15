# Environment sidecar — what the agent sees at step 1

This text is appended to every rung's prompt at agent invocation time (the "fixed boilerplate footer" in [EXPERIMENTAL_DESIGN.md](../EXPERIMENTAL_DESIGN.md)). It tells the agent what is available in the workspace, what to produce, and what the scoring will check.

---

## Your environment

You are running inside a container with the following pre-installed:

- **Python 3.11** with the scientific stack: `mesa` (3.0.3), `numpy`, `pandas`, `scipy`, `xarray`, `netCDF4`, `rasterio`, `tiktoken`.
- **Eclipse Temurin 21 JRE**.
- **The Josh CLI** as `josh` on `PATH`. Subcommands: `run`, `validate`, `preprocess`, `discoverConfig`, `inspectJshd`, `inspect-exports`, `server`, `runRemote`.

You may invoke `./run.sh` freely to self-test. You do not have package-installation privileges (no `pip install`, no `apt-get install`); use what is already present.

## Your workspace

Your working directory is `/sandbox`. You may read, write, and edit any file inside it. Outside `/sandbox`, you can read installed package source (useful: `python -c "import mesa; print(mesa.__file__)"` to find Mesa's installed location and follow imports), but you cannot write anywhere outside.

The following input data files are present and read-only:

- `./data/precip_tulare_annual.nc` — annual total precipitation. Native units: `kg m⁻² s⁻¹` (multiply by `31_536_000` to get `mm/year`). Dims: `(calendar_year=30, lat=31, lon=50)`. Years cover 2024–2053.
- `./data/maxtemp_tulare_annual.nc` — annual mean (max) air temperature in Kelvin. Same dims and year range as the precipitation file.

## What you must produce

A single executable file `./run.sh` in the workspace root, plus whatever source files it invokes. When the scorer runs:

```sh
cd /sandbox && ./run.sh
```

…your code must exit 0 and write `./output/results.csv` with the schema documented in [`harness_contract.md`](harness_contract.md). One row per (cell, year) for the eleven years 2024–2034 inclusive, columns `cell_id, lat, lon, year, meanAge, meanHeight, temperature, precipitation`.

## What the scorer will check

After `./run.sh` exits, the scorer runs in a separate offline container (`--network=none`) against your workspace. It checks:

1. Exit code is 0 and `./output/results.csv` exists.
2. The CSV schema matches the spec (columns, dtypes, units).
3. Mean tree height at year 2034 falls in the pre-registered acceptance range.
4. Mean tree count per cell at year 2034 falls in the pre-registered acceptance range.

You are not told the acceptance ranges. The score is based on whether your output falls inside them, not on matching a specific value.

## What you may NOT do

- Modify files outside `/sandbox`.
- Install new packages.
- Read any file under `/opt/` (the scoring harness and acceptance ranges live there; they are not for you to see).

You may invoke `./run.sh` as many times as you like to self-test before declaring done. The scorer only sees the final state of the workspace.

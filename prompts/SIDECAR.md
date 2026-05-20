## Procedure

To implement this, multiple AI agents will run one at a time to complete a todo list. Each agent is assigned exactly one todo per invocation. The shared todo list lives in `/sandbox/PLAN.md`; the prompt you receive only names which todo number is yours, so you must read `PLAN.md` to find the full description of what to do. Before doing any work, briefly (in 1–2 sentences) list the actions you intend to take to complete your assigned todo. Then carry them out by calling the available tools — listing the plan is a preamble, not the task itself. When done, mark your item complete (change `[ ]` to `[x]`) and append a 1–2 sentence summary of what you did under that todo line. Do not touch any other todo. Then exit.

### AI environment

You are running inside a container with the following pre-installed:

- **Python 3.11** with the scientific stack: `mesa` (3.0.3), `numpy`, `pandas`, `scipy`, `xarray`, `netCDF4`, `rasterio`, `tiktoken`.
- **Eclipse Temurin 21 JRE**.
- **The Josh CLI** as `josh` on `PATH`. Subcommands: `run`, `validate`, `preprocess`, `discoverConfig`, `inspectJshd`, `inspect-exports`, `server`, `runRemote`.

You may invoke `./run.sh` freely to self-test. Use what is already installed; do not assume internet access for package installation.

### AI workspace

Your working directory is `/sandbox`. Read, write, and edit any file inside it. You can also read installed package source on disk (useful: `python -c "import mesa; print(mesa.__file__)"`).

### AI Inputs

Two CF-1.8 compliant netCDF files in `data/` provide annual climate forcings over the bounding box specified in the prompt. Both are indexed by `(calendar_year, lat, lon)` and span 2024–2124; the simulation uses years 2024–2123 inclusive (100 calendar years, 100 growth events — every year is a growth step, see the §Temporal domain section of the spec above).

- `data/maxtemp_synthetic.nc` — data variable `tasmax` (annual maximum air temperature, K).
- `data/precip_synthetic.nc`  — data variable `pr` (precipitation flux).

#### Temperature

The `tasmax` variable is in **Kelvin (K)**. Use the values directly. The growth equation's `T_min` and `T_max` defaults are already in Kelvin, so no unit conversion is needed; pass the netCDF values straight into the temperature impact calculation.

#### Precipitation

The `pr` variable is a precipitation flux in `kg m⁻² s⁻¹`. Convert to mm/year via the standard physical conversion (1 kg of water spread over 1 m² is equivalent to 1 mm of depth):

```
precipitation_mm_per_year = precipitation_value_from_netcdf * 31_536_000
```

where `31_536_000` is the number of seconds in a 365-day year.

### Success criteria

A single executable file `./run.sh` in the workspace root, plus whatever source files it invokes. When invoked:

```sh
cd /sandbox && ./run.sh
```

Your code must exit 0 and write `./output/results.csv`.

**`./run.sh` is the unit of work being measured** — its wall-clock time is the headline cost metric for the experiment. It must do the **entire** workload: any preprocessing the chosen framework needs (e.g. Josh's `.jshd` build, or netCDF→DataFrame conversion for Mesa), the 100-replicate × 100-year simulation, and emission of `./output/results.csv`. No "pre-step" the user is expected to run by hand. The contract is "one script, end-to-end."

Three requirements that are part of the delivery, not optional:

- `./run.sh` must be executable. After writing it, run `chmod +x run.sh`. The scorer invokes the file as `./run.sh`; a script without the executable bit will not run.
- `./run.sh` must invoke the simulation for **100 replicates × 100 years (2024–2123 inclusive)**. Use your framework's native replicate flag (`josh run --replicates 100 …` for Josh) or a loop over `n=100` independent Model instances (for Mesa). The CSV must contain `n_cells × 100 × 100` rows.
- Before you declare yourself done, execute `./run.sh` at least once yourself. Confirm it exits 0 and writes `./output/results.csv`. If it fails, fix the cause and re-run. A handoff that requires the user to do their own chmod or first-run debug is a failure.

The CSV is UTF-8, comma-separated, with a header row. Required data columns:

| Column          | Type   | Unit          |
|-----------------|--------|---------------|
| `year`          | int    | calendar year |
| `nTrees`        | int    | count         |
| `meanAge`       | float  | years         |
| `meanHeight`    | float  | meters        |
| `temperature`   | float  | Kelvin        |
| `precipitation` | float  | mm/year       |

Plus a per-cell identifier — either a string column `cell_id` or the pair `position.x` and `position.y`. Plus a `replicate` column (integer index, 0..99) identifying which of the 100 replicates each row came from. Josh's default export already includes `replicate`; Mesa implementations must emit it explicitly. Other columns are accepted and ignored.

**One row per (cell, year, replicate)** for the 100 years 2024–2123 inclusive × 100 replicates. Every row reflects post-growth state for that year: year-2024 rows show `meanAge = 1` and a non-zero `meanHeight` (one growth event has occurred); year-2123 rows reflect 100 accumulated growth events. The h=0 / age=0 initial state is *before* the simulation and is not a CSV row. See the §Temporal domain section of the spec above for the convention.

### Working document

`/sandbox/PLAN.md` is the shared working document across agent invocations. It carries the 8-item todo list (the full description of what each step entails) and a `## Plan` section where agents append architectural decisions and notes that later steps need. See the Procedure section at the top of this document for the per-invocation contract.

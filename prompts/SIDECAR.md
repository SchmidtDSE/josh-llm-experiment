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

Two CF-1.8 compliant netCDF files in `data/` provide annual climate forcings over the bounding box specified in the prompt. Both are indexed by `(calendar_year, lat, lon)` and span 2024–2124; the simulation uses years 2024–2123 inclusive (100 calendar years).

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

**`./run.sh` is the unit of work being measured** — its wall-clock time is the headline cost metric for the experiment. It must do the **entire** workload: any preprocessing the chosen framework needs (e.g. Josh's `.jshd` build, or netCDF→DataFrame conversion for Mesa), the simulation, and emission of the output CSV(s). No "pre-step" the user is expected to run by hand. The contract is "one script, end-to-end."

We've seeded `/sandbox/run.sh` for you — it's already executable and runs 2 replicates by default. **Fill in the body** with your preprocess + simulation invocation, using `"$N_REPLICATES"` for the replicate count. Two replicates is the self-test scale.

Before you declare yourself done, execute `./run.sh` once and confirm it exits 0 and writes the output CSV(s) at the expected path. If it fails, fix the cause and re-run.

The CSV is UTF-8, comma-separated, with a header row. Required data columns:

| Column          | Type   | Unit          |
|-----------------|--------|---------------|
| `year`          | int    | calendar year |
| `nTrees`        | int    | count         |
| `meanAge`       | float  | years         |
| `meanHeight`    | float  | meters        |
| `temperature`   | float  | Kelvin        |
| `precipitation` | float  | mm/year       |

Plus a per-cell identifier — either a string column `cell_id` or the pair `position.x` and `position.y`. Other columns are accepted and ignored.

**Two output layouts are accepted; pick whichever is natural for your framework:**

- **Single consolidated CSV** at `output/results.csv` containing all replicates, with an integer `replicate` column distinguishing them. If a `replicate` column is absent the scorer treats the whole file as a single replicate (so a 1-replicate dev run still scores).
- **One CSV per replicate** (Josh's canonical layout) at `output/results_{N}.csv` — `results_0.csv`, `results_1.csv`, and so on. The integer in the filename is the authoritative replicate index; no in-file `replicate` column is needed. For Josh, set `exportFiles.patch = "file:///sandbox/output/results_{replicate}.csv"` and combine with `--replicates "$N_REPLICATES"`.

Total row count across whichever layout you pick: `n_cells × 100 years × N_REPLICATES`. Whether the year-2024 row reflects pre-growth state (`meanHeight = 0`) or post-growth state (`meanHeight ≈ Δh`) is up to your framework — pick whichever is natural.

### Working document

`/sandbox/PLAN.md` is the shared working document across agent invocations. It carries the 8-item todo list (the full description of what each step entails) and a `## Plan` section where agents append architectural decisions and notes that later steps need. See the Procedure section at the top of this document for the per-invocation contract.

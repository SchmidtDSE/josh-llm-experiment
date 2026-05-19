## Your environment

You are running inside a container with the following pre-installed:

- **Python 3.11** with the scientific stack: `mesa` (3.0.3), `numpy`, `pandas`, `scipy`, `xarray`, `netCDF4`, `rasterio`, `tiktoken`.
- **Eclipse Temurin 21 JRE**.
- **The Josh CLI** as `josh` on `PATH`. Subcommands: `run`, `validate`, `preprocess`, `discoverConfig`, `inspectJshd`, `inspect-exports`, `server`, `runRemote`.

You may invoke `./run.sh` freely to self-test. Use what is already installed; do not assume internet access for package installation.

## Your workspace

Your working directory is `/sandbox`. Read, write, and edit any file inside it. You can also read installed package source on disk (useful: `python -c "import mesa; print(mesa.__file__)"`).

## External Inputs

Two external data sources are required. Both must provide one value per (cell, step) pair across the full spatial and temporal extent of the simulation.

| Input            | Native unit       | Used as     |
|------------------|-------------------|-------------|
| Air temperature  | Kelvin (K)        | Drives the temperature impact on growth. |
| Precipitation    | mm/year (see note)| Drives the precipitation impact on growth. |

For this task, two netCDF files containing data from Tulare County, California via [Cal-Adapt](https://cal-adapt.org/) are provided:

- `data/precip_tulare_annual.nc`
- `data/maxtemp_tulare_annual.nc`

The provided netCDF reports precipitation as a flux in kg m⁻² s⁻¹. Because 1 kg of water spread over 1 m² is equivalent to 1 mm of depth, the conversion to mm/year is (31,536,000 = seconds in a 365-day year):

```
precipitation_mm_per_year = precipitation_kgm2s * 31_536_000
```

## What you must produce

A single executable file `./run.sh` in the workspace root, plus whatever source files it invokes. When invoked:

```sh
cd /sandbox && ./run.sh
```

Your code must exit 0 and write `./output/results.csv`.

Two requirements that are part of the delivery, not optional:

- `./run.sh` must be executable. After writing it, run `chmod +x run.sh`. The scorer invokes the file as `./run.sh`; a script without the executable bit will not run.
- Before you declare yourself done, execute `./run.sh` at least once yourself. Confirm it exits 0 and writes `./output/results.csv`. If it fails, fix the cause and re-run. A handoff that requires the user to do their own chmod or first-run debug is a failure.

The CSV is UTF-8, comma-separated, with a header row. Columns, in order:

| Column          | Type   | Unit          |
|-----------------|--------|---------------|
| `cell_id`       | string |               |
| `lat`           | float  | degrees       |
| `lon`           | float  | degrees       |
| `year`          | int    | calendar year |
| `nTrees`        | int    | count         |
| `meanAge`       | float  | years         |
| `meanHeight`    | float  | meters        |
| `temperature`   | float  | Kelvin        |
| `precipitation` | float  | mm/year       |

One row per (cell, year) for the eleven years 2024–2034 inclusive. `cell_id` format: `{i}_{j}` where `i` is the lat index (0-indexed, south → north) and `j` is the lon index (0-indexed, west → east) of the simulation grid.

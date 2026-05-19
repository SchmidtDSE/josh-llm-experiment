## Your environment

You are running inside a container with the following pre-installed:

- **Python 3.11** with the scientific stack: `mesa` (3.0.3), `numpy`, `pandas`, `scipy`, `xarray`, `netCDF4`, `rasterio`, `tiktoken`.
- **Eclipse Temurin 21 JRE**.
- **The Josh CLI** as `josh` on `PATH`. Subcommands: `run`, `validate`, `preprocess`, `discoverConfig`, `inspectJshd`, `inspect-exports`, `server`, `runRemote`.

You may invoke `./run.sh` freely to self-test. Use what is already installed; do not assume internet access for package installation.

## Your workspace

Your working directory is `/sandbox`. Read, write, and edit any file inside it. You can also read installed package source on disk (useful: `python -c "import mesa; print(mesa.__file__)"`).

## External Inputs

Two CF-1.8 compliant netCDF files in `data/` provide annual climate forcings over the bounding box specified in the prompt. Both are indexed by `(calendar_year, lat, lon)` and span 2024–2054; the simulation uses years 2024–2034 inclusive.

- `data/maxtemp_synthetic.nc` — data variable `tasmax` (annual maximum air temperature, K).
- `data/precip_synthetic.nc`  — data variable `pr` (precipitation flux).

### Temperature

The `tasmax` variable is in **Kelvin (K)**. Use the values directly. The growth equation's `T_min` and `T_max` defaults are already in Kelvin, so no unit conversion is needed; pass the netCDF values straight into the temperature impact calculation.

### Precipitation

The `pr` variable is a precipitation flux in `kg m⁻² s⁻¹`. Convert to mm/year via the standard physical conversion (1 kg of water spread over 1 m² is equivalent to 1 mm of depth):

```
precipitation_mm_per_year = precipitation_value_from_netcdf * 31_536_000
```

where `31_536_000` is the number of seconds in a 365-day year.

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

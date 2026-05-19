## Your environment

You are running inside a container with the following pre-installed:

- **Python 3.11** with the scientific stack: `mesa` (3.0.3), `numpy`, `pandas`, `scipy`, `xarray`, `netCDF4`, `rasterio`, `tiktoken`.
- **Eclipse Temurin 21 JRE**.
- **The Josh CLI** as `josh` on `PATH`. Subcommands: `run`, `validate`, `preprocess`, `discoverConfig`, `inspectJshd`, `inspect-exports`, `server`, `runRemote`.

You may invoke `./run.sh` freely to self-test. Use what is already installed; do not assume internet access for package installation.

## Your workspace

Your working directory is `/sandbox`. Read, write, and edit any file inside it. You can also read installed package source on disk (useful: `python -c "import mesa; print(mesa.__file__)"`).

## External Inputs

Two netCDF files in `data/` provide climate forcings for Tulare County, California via [Cal-Adapt](https://cal-adapt.org/). Both are indexed by `(calendar_year, lat, lon)` and span 2024–2053; the simulation uses years 2024–2034 inclusive.

- `data/maxtemp_tulare_annual.nc` — annual maximum air temperature.
- `data/precip_tulare_annual.nc`  — annual precipitation.

### Temperature

The data variable in `maxtemp_tulare_annual.nc` is in **Kelvin (K)**. Use the values directly. The growth equation's `T_min` and `T_max` defaults are already in Kelvin, so no unit conversion is needed; pass the netCDF values straight into the temperature impact calculation.

### Precipitation — read this carefully, the unit label is misleading

The data variable in `precip_tulare_annual.nc` is labeled `units = kg m⁻² s⁻¹`. **That label is misleading.** The values are *not* an instantaneous flux. The upstream pipeline downloaded daily precipitation rates (each in kg m⁻² s⁻¹) and **summed them across days** to build this file; the unit string was never updated. Each daily rate represented an accumulation of `rate × 86_400` mm over that day, so the sum-of-N-daily-rates in the file represents the same total in (mm/day) units summed across the days — i.e., mm — once you multiply by seconds-per-day.

The correct conversion to mm/year is therefore:

```
precipitation_mm_per_year = precipitation_value_from_netcdf * 86_400
```

**Do not multiply by 31,536,000 (seconds in a year).** That factor is correct for a true instantaneous flux but the values in this file have already been time-aggregated by the pipeline. Using seconds-per-year over-estimates annual precipitation by a factor of ~365 and will push every cell well past `P_high`, saturating the precipitation impact term so growth becomes effectively independent of rainfall.

Sanity check after conversion: Tulare County annual precipitation should land in the low hundreds of mm/year (historical norm ~200–400 mm/year in the valley, higher in the Sierras). If your converted values are in the thousands of mm/year, the conversion factor is wrong.

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

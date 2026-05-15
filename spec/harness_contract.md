# Harness contract — what generated code must produce

The scoring harness invokes generated code through a fixed contract. Both reference implementations and agent-generated code adhere to it; deviations are caught by [harness/validators/output_schema.py](../harness/validators/output_schema.py) *(planned, phase 2b)* and surfaced as `csv_schema_ok=false` in the scorer's JSON record.

## Workspace layout

At the moment the scorer runs, the workspace at `/sandbox` looks like:

```
/sandbox/
├── run.sh                              # generated; executable
├── <generated source files>            # .py and/or .josh + .jshd
├── data/
│   ├── precip_tulare_annual.nc         # mounted by the orchestrator
│   └── maxtemp_tulare_annual.nc        # mounted by the orchestrator
└── output/                             # created by run.sh; CSV lands here
    └── results.csv
```

## Invocation

The scorer's runner enters the workspace and runs `./run.sh` with a per-run timeout (default 10 minutes — long enough for a correct ForeverTree implementation, short enough to catch a wedged process well under the 30-minute agent backstop). The runner records:

- Exit code (0 = success).
- Wall time in seconds.
- The last ~4 kB of stdout and stderr (truncated tail, for debugging).
- Whether the timeout fired.

## Output: `./output/results.csv`

UTF-8, comma-separated, header row required. One row per (cell, simulation year). Columns, in order:

| Column          | Type   | Unit          | Description |
|-----------------|--------|---------------|-------------|
| `cell_id`       | string |               | `{i}_{j}` where `i` is the 0-indexed lat row (south → north) and `j` is the 0-indexed lon column (west → east) of the simulation grid. |
| `lat`           | float  | degrees       | Cell centroid latitude. |
| `lon`           | float  | degrees       | Cell centroid longitude. |
| `year`          | int    | calendar year | The year this row corresponds to (e.g. `2024`). |
| `meanAge`       | float  | years         | Mean `age` of all ForeverTrees on the cell at the end of this year. |
| `meanHeight`    | float  | meters        | Mean `height` of all ForeverTrees on the cell at the end of this year. |
| `temperature`   | float  | Kelvin        | Patch annual mean temperature for this year (the value driving growth). |
| `precipitation` | float  | mm/year       | Patch annual precipitation for this year (the value driving growth, post-unit-conversion). |

## Simulation duration

Ten growth steps covering eleven years inclusive: **2024 through 2034**. Eleven rows per cell, one per year (the year-0 row records the initial state at age 0 / height 0 / step-0 climate; subsequent rows record state after each growth step).

## Grid

The orchestrator passes in the two netCDF files at `./data/`; the implementation derives the grid from the data extent intersected with the BBox specified in the [spec](ForeverTree.md). The actual data covers `lat [35.80°, 36.73°]`, `lon [−119.52°, −117.98°]` — full Tulare County coverage at the BBox in the spec.

## What "success" means

`./run.sh` exit 0 AND `./output/results.csv` exists AND the CSV passes the schema check above. Any of those failing flips `did_run=false` in the scorer JSON record. Downstream metrics (`height_in_range`, `occupancy_in_range`) are then null.

# ForeverTree

Generic model specification for a demonstrative example with climate-dependent growth.

<br>

## Overview
A simple spatial vegetation model in which a single fictional tree species called **ForeverTree** grows on a regular spatial grid. Each tree ages and gains height each year. Growth is modulated by two climate drivers supplied as external data as well as a small bit of stochasticity:

1. **Air temperature** (annual mean).
2. **Precipitation** (annual total).

The model is small enough to read in one sitting but large enough to exercise the core concepts a general is likely to encounter early in any agent-based or patch-based ecological simulation:

- A spatial grid of cells (patches) covering a geographic region.
- A population of agents (trees) living on each patch.
- Per-step lifecycle events (initialization vs. update).
- Stochastic processes (random initial growth, sampling).
- Coupling of agent behaviour to external environmental data.
- Aggregation and export of summary statistics over time.

The model is intentionally not intended to be a realistic ecological forecast but, instead, a demonstrative case study.

<br>

## Environment

This simulation requires operating across both space and time.

### Spatial domain

- The simulation runs over a rectangular geographic region described by a
  bounding box in latitude/longitude.
- The region is discretized into a regular grid of square **patches**.
- Patch size (the edge length of each cell) is a fixed parameter.

| Parameter        | Default value                                    |
|------------------|--------------------------------------------------|
| Patch edge length| 1 km                                             |
| Bounding box low | 35.80° latitude, −119.52° longitude              |
| Bounding box high| 36.73° latitude, −117.98° longitude              |

### Temporal domain

- Each simulation step represents **one calendar year**.
- The simulation runs **years 2024 through 2123 inclusive — 100 calendar years**.
- **Year 2024 is initialization.** Trees are placed with `age = 0`, `height = 0` *and do not grow during year 2024*. The first growth event is between year 2024 and year 2025. The simulation therefore produces 99 growth events across the 100-year span. This convention matters; see §Growth Model and §Outputs below.
- The implementing engine is expected to evaluate every patch and every
  agent within a patch exactly once per step.

### Replication

Run **100 independent stochastic replicates** of the full 100-year simulation. Replicates share identical climate inputs and identical initial conditions; they differ only in the per-tree per-step stochastic growth-offset draws (§Stochasticity below). The CSV is keyed by `(cell, year, replicate)` — one row per combination — so the same `(cell, year)` appears 100 times in the output, once per replicate.

<br>

## Entities

Each tree is to be represented as an agent and each grid cell has properties to which agents react.

### Grid
At step 0, each grid cell is populated with a fixed number of ForeverTree agents (default: **10 trees per patch**).

| Attribute      | Type / unit         | Description                                  |
|----------------|---------------------|----------------------------------------------|
| `temperature`  | scalar, K           | Annual mean air temperature, sourced from external data for the current step and patch location. |
| `precipitation`| scalar, mm/year     | Annual total precipitation, sourced from external data for the current step and patch location. |
| `trees`        | collection of agent | The ForeverTree agents currently on this patch. |

Then, during each step:

- The patch gathers its current `temperature` and `precipitation` from the external data sources.
- These values are made available to every agent on the patch when the agent computes its growth for the step.

### ForeverTree (organism / agent)

A ForeverTree is an individual tree.

| Attribute | Variable    | Description                              |
|-----------|-------------|------------------------------------------|
| `age`     | $y$         | The tree's age in years.                 |
| `height`  | $h$         | The tree's height in meters.             |

All trees start with `age = 0` years and `height = 0` m at the year-2024 initialization step. **Year 2024 is initialization only — no growth event happens during year 2024.** Each subsequent year (2025, 2026, …, 2123) is a growth step in which:

- `age` increases by exactly 1 year ($y_{i} = y_{i-1} + 1$).
- `height` increases by an amount called `newGrowth` ($\Delta h$), which depends on the climate at the tree's patch this step. See Section 5.

Across the 100-year span there are therefore **99 growth events per tree** (years 2025–2123, inclusive), not 100. A faithful implementation produces year-2024 rows with `meanHeight = 0` and `meanAge = 0`, and year-2123 rows reflecting the accumulation of 99 climate-driven growth events.

ForeverTrees in this specification do **not** die, reproduce, or move. In other words, the population on each patch is fixed for the entire run.

<br>

## Growth Model

For each tree, each step, the height ($h$) is:

$\Delta h = \Delta h_{max} * \%_{T} * \%_{P} * O$
$h_{i} = h_{i-1} + \Delta h$

Here we expect $\Delta h_{max} = \frac{1 m}{1 year}$ (the height a tree would gain in a year under perfectly optimal conditions on both axes). However, both $\%_{T}$ (temperature impact) and $\%_{P}$ (precpitation impact) are unitless scalars in the closed interval `[0, 1]` (equivalently, 0% to 100%). The impacts multiply, so growth is choked off whenever *either* driver is unfavorable. A tree at the perfect temperature but in a drought still doesn't grow. However, a tree with ideal rainfall but in a freeze also doesn't grow.

### Temperature impact

This simple simulation assumes trees grow best at an intermediate temperature. The response is a downward-opening parabola: zero at the cold and hot edges of a tolerated range but peaking at the midpoint.

Let $T$ be the patch's annual mean temperature, and let
$[T_{min}, T_{max}]$ define the tree's tolerated temperature window. We can then define the normalized position within that window:

$x_{T} = \frac{T - T_{min}}{T_{max} - T_{min}}$

Then the impact is the quadratic that is 0 at $x_T = 0$ and $x_T = 1$ and peaks at 1 at $x_T = 0.5$:

$\%_{T} = 4 * x_T * (1 - x_T)$

We will use the default parameters $T_{min} = 270 K$ and $T_{max} = 330 K$. Values outside of $T_{min}$ to $T_{max}$ should be clamped to that range.

### Precipitation impact

For precipitation we use a saturating response. In other words, too little water means no growth and plenty of water means full growth. However, there is a smooth transition in between.

Let $P$ be the patch's annual precipitation. Then, we can define:

$x_P = \frac{P - P_{low}}{P_{high} - P_{low}}$

Then use a logistic sigmoid centered on the midpoint of the window defined by $P_{low}$ to $P_{high}$. It is scaled so that the impact is close to 0 at `P_low` and close to 1 at $P_{high}$:

$\%_{P} = \frac{1}{1 + e^{-k × (x_P - 0.5)}}$

In this form, $k$ is a steepness constant. Using the default $k$ of 12 see expect roughly ~0.05 at $P_{low}$ and 0.95 at $P_{high}$. In other words, the sigmoid parameterized so that $P_{low}$ maps to ~0% and $P_{high}$ maps to ~100%.

All this in mind, $P_{low}$ of 300 mm/year and $P_high$ of 500 mm/year is recommended as a default.

### Stochasticity
There is a stochastic element $O$ which is anticiated to offset the percentage used for $\Delta h_{max}$ and should be a gaussian value with mean of 1 and std deviation of 0.05. This means that it is possible that some trees may grow over $\Delta h_{max}$ under ideal conditions.

$O$ is drawn independently per (tree, year, replicate). Trees on the same patch see the same `temperature` and `precipitation` each year but each gets its own draw of $O$; that's the only source of within-patch tree-to-tree variation in this spec. Across replicates of the same simulation, the climate inputs and initial conditions are identical — replicates differ only in their $O$ draws.

<br>

## Style

The resultant code should be self-documenting, wherever possible, with comments minimized and included only where absolutely necessary.  

## Outputs

The model should export **per cell, per step, per replicate**:

| Variable        | Definition                                              |
|-----------------|---------------------------------------------------------|
| `nTrees`        | Count of ForeverTree agents currently on the cell.      |
| `meanAge`       | Mean `age` of all ForeverTrees on the cell (year).      |
| `meanHeight`    | Mean `height` of all ForeverTrees on the patch (m).     |
| `temperature`   | The patch's annual mean temperature this step (K).      |
| `precipitation` | The patch's annual precipitation this step (mm/year).   |

This should happen as a CSV where each cell is identified either by latitude / longitude or a cell index, and where the replicate index is exposed as its own column (Josh's default schema includes a `replicate` column; Mesa implementations should emit one explicitly). The full CSV therefore has `n_cells × 100 years × 100 replicates` rows. Per the temporal-domain convention above, the year-2024 rows carry `meanAge = 0` and `meanHeight = 0`; year 2123 reflects 99 growth events.

<br>
"""Validate the agent's output CSV(s) — parseable, required columns present.

The check is a gate, not a substantive correctness measure. It exists to
distinguish "the agent produced something with the columns we need" from
"the agent produced nothing usable". Substantive checks live in
`acceptance.py`.

Two CSV layouts are accepted; the scorer is permissive about which one
the agent picks:

1. **Single consolidated CSV** at `output/results.csv` with a
   `replicate` column distinguishing replicates (or no `replicate`
   column at all, in which case the file is treated as replicate 0).
2. **One CSV per replicate** at `output/results_{N}.csv` (the Josh-
   canonical pattern emitted when `exportFiles.patch` includes the
   `{replicate}` template). The integer in the filename is the
   authoritative replicate index; any `replicate` column inside the
   file is overwritten by the filename's index.

Rules:
- Required columns must be a subset of the actual columns; extras and
  arbitrary column order are accepted.
- Required numeric/integer columns must be coercible to numeric (string
  values in `meanHeight` etc. fail; NaN does not).
- The acceptance target year (e.g. 2123) must appear in the `year` column.
- NaN in numeric columns is *not* a schema failure: rows with NaN in any
  required numeric column are counted (`csv_rows_dropped_nan`) and
  downstream modules filter them before computing means.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Matches Josh's canonical per-replicate filename: `results_<digits>.csv`.
_PER_REP_RE = re.compile(r"^results_(\d+)\.csv$")

# Data columns the scorer actually consumes. Required on every CSV.
REQUIRED_DATA_COLUMNS = [
    "year",
    "nTrees",
    "meanAge",
    "meanHeight",
    "temperature",
    "precipitation",
]

# Cell-identity is provided EITHER by a `cell_id` string column OR by the
# `position.x` + `position.y` pair (Josh's default export). At least one
# alternative below must be fully present. When only the position pair is
# present, `load_clean_results` synthesises a `cell_id` from it so
# downstream consumers can keep grouping by a single column.
CELL_IDENTITY_ALTERNATIVES = [
    ["cell_id"],
    ["position.x", "position.y"],
]

INTEGER_COLUMNS = ("year", "nTrees")
# Lat/lon are not required, but if present they must be numeric. Same for
# Josh's native spatial fields. The scorer never consumes any of these
# mathematically; they're descriptive metadata.
NUMERIC_COLUMNS = (
    "lat",
    "lon",
    "meanAge",
    "meanHeight",
    "temperature",
    "precipitation",
    "position.x",
    "position.y",
    "position.latitude",
    "position.longitude",
)


def _check_required_columns_present(df: pd.DataFrame) -> list[str]:
    actual = set(df.columns)
    errors: list[str] = []
    missing_data = [c for c in REQUIRED_DATA_COLUMNS if c not in actual]
    if missing_data:
        errors.append(
            f"missing required columns: {missing_data} "
            f"(got: {list(df.columns)})"
        )
    has_cell_identity = any(
        all(c in actual for c in alt) for alt in CELL_IDENTITY_ALTERNATIVES
    )
    if not has_cell_identity:
        alts = " or ".join(
            "(" + ", ".join(a) + ")" for a in CELL_IDENTITY_ALTERNATIVES
        )
        errors.append(
            f"missing required columns for cell identity; need one of: {alts} "
            f"(got: {list(df.columns)})"
        )
    return errors


def _check_required_columns_numeric_coercible(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    for col in (*INTEGER_COLUMNS, *NUMERIC_COLUMNS):
        if col not in df.columns:
            continue
        try:
            pd.to_numeric(df[col], errors="raise")
        except (ValueError, TypeError) as exc:
            errors.append(f"{col} not numeric-coercible: {exc}")
    return errors


def _check_target_year_present(df: pd.DataFrame, target_year: int) -> list[str]:
    if "year" not in df.columns:
        return []
    years = pd.to_numeric(df["year"], errors="coerce").dropna()
    if target_year not in set(years.astype(int).tolist()):
        present = sorted({int(y) for y in years.astype(int).tolist()})
        return [
            f"target year {target_year} not in CSV "
            f"(years present: {present[:20]}{'...' if len(present) > 20 else ''})"
        ]
    return []


def _count_rows_with_nan_in_required_numeric(df: pd.DataFrame) -> int:
    cols = [
        c for c in (*INTEGER_COLUMNS, *NUMERIC_COLUMNS)
        if c in df.columns and c != "year"
    ]
    if not cols:
        return 0
    # Coerce non-numeric to NaN so we count both literal NaN and unparseable cells.
    coerced = df[cols].apply(pd.to_numeric, errors="coerce")
    return int(coerced.isna().any(axis=1).sum())


def _find_output_files(workspace: Path) -> list[tuple[Path, int | None]]:
    """Return (path, replicate_index) for the agent's output CSV(s).

    If `output/results.csv` exists, it is treated as the (potentially
    multi-replicate) consolidated layout and returned as one entry
    with `replicate_index=None`. Otherwise, files matching
    `output/results_<N>.csv` are returned with their integer index
    (sorted numerically, not lexicographically — so `results_10.csv`
    sorts after `results_2.csv`). Returns an empty list if neither
    layout is present.
    """
    output_dir = workspace.resolve() / "output"
    if not output_dir.is_dir():
        return []
    single = output_dir / "results.csv"
    if single.is_file():
        return [(single, None)]
    pairs: list[tuple[Path, int]] = []
    for p in output_dir.glob("results_*.csv"):
        m = _PER_REP_RE.match(p.name)
        if m:
            pairs.append((p, int(m.group(1))))
    pairs.sort(key=lambda pr: pr[1])
    return pairs  # type: ignore[return-value]


def _load_combined_results(workspace: Path) -> pd.DataFrame:
    """Read all output CSVs into a single combined DataFrame.

    Per-rep files get a synthesised `replicate` column from their
    filename index, overwriting any in-file `replicate` value (the
    filename's `{N}` is the authoritative encoding under Josh's
    canonical layout). Consolidated CSVs are returned as-is; their
    `replicate` column, if any, is preserved.

    Raises FileNotFoundError if no output CSVs are present — callers
    should gate on `_find_output_files(...) != []` first.
    """
    pairs = _find_output_files(workspace)
    if not pairs:
        raise FileNotFoundError(f"no output CSVs under {workspace}/output/")
    frames = []
    for path, rep_idx in pairs:
        df = pd.read_csv(path)
        if rep_idx is not None:
            df["replicate"] = rep_idx
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def check_output_schema(workspace: Path, target_year: int) -> dict:
    pairs = _find_output_files(workspace)
    if not pairs:
        return {
            "csv_exists": False,
            "csv_row_count": None,
            "csv_rows_dropped_nan": None,
            "csv_schema_ok": False,
            "csv_schema_errors": [
                f"no output CSV found: expected {workspace}/output/results.csv "
                f"or {workspace}/output/results_<N>.csv files"
            ],
        }

    try:
        df = _load_combined_results(workspace)
    except Exception as exc:
        return {
            "csv_exists": True,
            "csv_row_count": None,
            "csv_rows_dropped_nan": None,
            "csv_schema_ok": False,
            "csv_schema_errors": [f"pandas read_csv failed: {exc!r}"],
        }

    errors: list[str] = []
    errors += _check_required_columns_present(df)
    # Only run the column-level checks once all required cols exist.
    if not errors:
        errors += _check_required_columns_numeric_coercible(df)
        errors += _check_target_year_present(df, target_year)

    rows_dropped = _count_rows_with_nan_in_required_numeric(df)

    return {
        "csv_exists": True,
        "csv_row_count": len(df),
        "csv_rows_dropped_nan": rows_dropped,
        "csv_schema_ok": not errors,
        "csv_schema_errors": errors,
        "csv_source_layout": "single" if pairs[0][1] is None else "per-replicate",
        "csv_source_files": [str(p.relative_to(workspace.resolve())) for p, _ in pairs],
    }


def load_clean_results(workspace: Path) -> tuple[pd.DataFrame, int]:
    """Load output CSV(s) and drop rows with NaN in any required numeric column.

    Returns (filtered_df, n_dropped). Caller is expected to have already
    confirmed `csv_schema_ok=true` via `check_output_schema`. Handles both
    the single-CSV and per-replicate-CSV layouts transparently — see
    `_find_output_files` and `_load_combined_results` for the layout
    detection + concatenation. The required numeric columns are coerced
    to float before filtering, so string values that survived the gate
    become NaN and get dropped here too.

    Cell-identity normalisation: when the CSV uses Josh's default schema
    (only `position.x` + `position.y`, no `cell_id`), a synthetic
    `cell_id` column is constructed as `f"{position.x}_{position.y}"` so
    downstream consumers can groupby a single key without caring which
    alternative the agent emitted.
    """
    df = _load_combined_results(workspace)
    n_before = len(df)
    numeric_cols = [
        c for c in (*INTEGER_COLUMNS, *NUMERIC_COLUMNS)
        if c in df.columns and c != "year"
    ]
    if numeric_cols:
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        df = df.dropna(subset=numeric_cols)
    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
        df = df.dropna(subset=["year"])
        df["year"] = df["year"].astype(int)
    if "cell_id" not in df.columns and {"position.x", "position.y"}.issubset(df.columns):
        df["cell_id"] = (
            df["position.x"].astype(str) + "_" + df["position.y"].astype(str)
        )
    return df.reset_index(drop=True), n_before - len(df)

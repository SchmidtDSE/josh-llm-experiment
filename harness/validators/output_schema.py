"""Validate `output/results.csv` is parseable and carries the required columns.

The check is a gate, not a substantive correctness measure. It exists to
distinguish "the agent produced a CSV with the columns we need somewhere
in it" from "the agent produced nothing usable". Substantive checks live
in `acceptance.py` and `internal_consistency.py`.

Rules:
- Required columns must be a subset of the actual columns; extras and
  arbitrary column order are accepted.
- Required numeric/integer columns must be coercible to numeric (string
  values in `meanHeight` etc. fail; NaN does not).
- The acceptance target year (e.g. 2034) must appear in the `year` column.
- NaN in numeric columns is *not* a schema failure: rows with NaN in any
  required numeric column are counted (`csv_rows_dropped_nan`) and
  downstream modules filter them before computing means.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

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
# downstream consumers (internal_consistency, etc.) can keep grouping by
# a single column.
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


def check_output_schema(workspace: Path, target_year: int) -> dict:
    csv_path = workspace.resolve() / "output" / "results.csv"

    if not csv_path.is_file():
        return {
            "csv_exists": False,
            "csv_row_count": None,
            "csv_rows_dropped_nan": None,
            "csv_schema_ok": False,
            "csv_schema_errors": [f"missing: {csv_path}"],
        }

    try:
        df = pd.read_csv(csv_path)
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
    }


def load_clean_results(workspace: Path) -> tuple[pd.DataFrame, int]:
    """Load results.csv and drop rows with NaN in any required numeric column.

    Returns (filtered_df, n_dropped). Caller is expected to have already
    confirmed `csv_schema_ok=true` via `check_output_schema`. The required
    numeric columns are coerced to float before filtering, so string
    values that survived the gate become NaN and get dropped here too.

    Cell-identity normalisation: when the CSV uses Josh's default schema
    (only `position.x` + `position.y`, no `cell_id`), a synthetic
    `cell_id` column is constructed as `f"{position.x}_{position.y}"` so
    downstream consumers can groupby a single key without caring which
    alternative the agent emitted.
    """
    csv_path = workspace.resolve() / "output" / "results.csv"
    df = pd.read_csv(csv_path)
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

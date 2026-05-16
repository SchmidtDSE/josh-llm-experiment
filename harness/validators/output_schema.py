"""Validate `output/results.csv` against the SIDECAR contract.

Checks (any failure flips csv_schema_ok=false and appends a string to
csv_schema_errors):
- File exists.
- Header columns match the spec exactly, in order.
- dtypes: cell_id str, year int, others float.
- Row count = n_cells * 11 (one row per (cell, year) for years 2024-2034).
- No NaN in numeric columns (per user decision: NaN is a schema violation,
  not a downstream range issue).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

EXPECTED_COLUMNS = [
    "cell_id",
    "lat",
    "lon",
    "year",
    "meanAge",
    "meanHeight",
    "temperature",
    "precipitation",
]

# Spec: 11 rows per cell (years 2024 through 2034 inclusive).
ROWS_PER_CELL = 11

_NUMERIC_COLUMNS = ("lat", "lon", "meanAge", "meanHeight", "temperature", "precipitation")


def check(workspace: Path) -> dict:
    csv_path = workspace.resolve() / "output" / "results.csv"
    errors: list[str] = []

    if not csv_path.is_file():
        return {
            "csv_exists": False,
            "csv_row_count": None,
            "csv_schema_ok": False,
            "csv_schema_errors": [f"missing: {csv_path}"],
        }

    try:
        df = pd.read_csv(csv_path)
    except Exception as exc:
        return {
            "csv_exists": True,
            "csv_row_count": None,
            "csv_schema_ok": False,
            "csv_schema_errors": [f"pandas read_csv failed: {exc!r}"],
        }

    row_count = len(df)

    actual_columns = list(df.columns)
    if actual_columns != EXPECTED_COLUMNS:
        errors.append(
            f"columns mismatch: expected {EXPECTED_COLUMNS}, got {actual_columns}"
        )

    if "cell_id" in df.columns and not pd.api.types.is_object_dtype(df["cell_id"]):
        errors.append(f"cell_id dtype: expected object/str, got {df['cell_id'].dtype}")
    if "year" in df.columns and not pd.api.types.is_integer_dtype(df["year"]):
        errors.append(f"year dtype: expected integer, got {df['year'].dtype}")
    for col in _NUMERIC_COLUMNS:
        if col in df.columns and not pd.api.types.is_float_dtype(df[col]):
            errors.append(f"{col} dtype: expected float, got {df[col].dtype}")

    if "cell_id" in df.columns:
        n_cells = df["cell_id"].nunique()
        expected_rows = n_cells * ROWS_PER_CELL
        if row_count != expected_rows:
            errors.append(
                f"row count: expected {expected_rows} "
                f"({n_cells} cells × {ROWS_PER_CELL} years), got {row_count}"
            )

    for col in _NUMERIC_COLUMNS:
        if col in df.columns and df[col].isna().any():
            n_nan = int(df[col].isna().sum())
            errors.append(f"{col}: {n_nan} NaN values (numeric cols must be finite)")

    return {
        "csv_exists": True,
        "csv_row_count": row_count,
        "csv_schema_ok": not errors,
        "csv_schema_errors": errors,
    }

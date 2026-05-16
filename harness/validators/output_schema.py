"""Validate `output/results.csv` against the SIDECAR contract.

Each check appends a string to `csv_schema_errors` on failure; any
non-empty error list flips `csv_schema_ok=false`. NaN in numeric columns
is treated as a schema violation rather than a downstream range issue.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

EXPECTED_COLUMNS = [
    "cell_id",
    "lat",
    "lon",
    "year",
    "nTrees",
    "meanAge",
    "meanHeight",
    "temperature",
    "precipitation",
]

# Spec: 11 rows per cell (years 2024 through 2034 inclusive).
ROWS_PER_CELL = 11

INTEGER_COLUMNS = ("year", "nTrees")
NUMERIC_COLUMNS = ("lat", "lon", "meanAge", "meanHeight", "temperature", "precipitation")


def _check_columns_match_spec(df: pd.DataFrame) -> list[str]:
    actual = list(df.columns)
    if actual != EXPECTED_COLUMNS:
        return [f"columns mismatch: expected {EXPECTED_COLUMNS}, got {actual}"]
    return []


def _check_column_dtypes(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    if "cell_id" in df.columns and not pd.api.types.is_object_dtype(df["cell_id"]):
        errors.append(f"cell_id dtype: expected object/str, got {df['cell_id'].dtype}")
    for col in INTEGER_COLUMNS:
        if col in df.columns and not pd.api.types.is_integer_dtype(df[col]):
            errors.append(f"{col} dtype: expected integer, got {df[col].dtype}")
    for col in NUMERIC_COLUMNS:
        if col in df.columns and not pd.api.types.is_float_dtype(df[col]):
            errors.append(f"{col} dtype: expected float, got {df[col].dtype}")
    return errors


def _check_row_count_matches_grid(df: pd.DataFrame) -> list[str]:
    if "cell_id" not in df.columns:
        return []
    n_cells = df["cell_id"].nunique()
    expected = n_cells * ROWS_PER_CELL
    if len(df) != expected:
        return [
            f"row count: expected {expected} "
            f"({n_cells} cells × {ROWS_PER_CELL} years), got {len(df)}"
        ]
    return []


def _check_no_nan_in_numeric_columns(df: pd.DataFrame) -> list[str]:
    errors: list[str] = []
    for col in NUMERIC_COLUMNS:
        if col in df.columns and df[col].isna().any():
            n_nan = int(df[col].isna().sum())
            errors.append(f"{col}: {n_nan} NaN values (numeric cols must be finite)")
    return errors


def check_output_schema(workspace: Path) -> dict:
    csv_path = workspace.resolve() / "output" / "results.csv"

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

    errors: list[str] = []
    errors += _check_columns_match_spec(df)
    errors += _check_column_dtypes(df)
    errors += _check_row_count_matches_grid(df)
    errors += _check_no_nan_in_numeric_columns(df)

    return {
        "csv_exists": True,
        "csv_row_count": len(df),
        "csv_schema_ok": not errors,
        "csv_schema_errors": errors,
    }

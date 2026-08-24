#!/usr/bin/env python3
"""Resolve a short model name to its OpenRouter slug.

Reads config/models.yaml relative to the repo root (the parent of this
script's directory). Writes the slug to stdout. Exits non-zero with a
helpful stderr if the name isn't in the config.

Usage: resolve_model.py <short-name>
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "models.yaml"


def resolve(short_name: str) -> str:
    """Short name -> OpenRouter slug. Raises KeyError if unknown.

    Importable form of the CLI below, for host-side drivers that resolve
    in-process (orchestration/run_hoist_judge.py) instead of shelling out
    the way containers/run-judge.sh does.
    """
    models = yaml.safe_load(CONFIG_PATH.read_text())
    slug = models.get(short_name)
    if not slug:
        valid = ", ".join(sorted(models.keys()))
        raise KeyError(f"MODEL '{short_name}' not in {CONFIG_PATH}. Valid: {valid}")
    return slug


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: resolve_model.py <short-name>", file=sys.stderr)
        return 2

    try:
        print(resolve(sys.argv[1]))
    except KeyError as exc:
        print(str(exc).strip('"'), file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    sys.exit(main())

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


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: resolve_model.py <short-name>", file=sys.stderr)
        return 2

    short_name = sys.argv[1]
    config_path = Path(__file__).resolve().parent.parent / "config" / "models.yaml"
    models = yaml.safe_load(config_path.read_text())

    slug = models.get(short_name)
    if not slug:
        valid = ", ".join(sorted(models.keys()))
        print(
            f"MODEL '{short_name}' not in {config_path}. Valid: {valid}",
            file=sys.stderr,
        )
        return 4

    print(slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())

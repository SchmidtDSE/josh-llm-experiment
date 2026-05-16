"""Shared file enumeration for loc + entropy.

Both LOC and entropy compute over the same set of agent-authored source
files; this helper is the single source of truth for which files are "in"
that set, so the two metrics never drift on file selection.
"""

from __future__ import annotations

from pathlib import Path

_EXCLUDED_DIRS = {"data", "output", "results", "__pycache__", ".git"}
_EXCLUDED_FILES = {"run.sh"}

_EXTENSIONS_BY_TARGET = {
    "mesa": (".py",),
    "josh": (".josh", ".jshd"),
}


def enumerate_source_files(workspace: Path, target: str) -> list[Path]:
    """Return agent-authored source files under `workspace` for `target`.

    Excludes run.sh, data/, output/, results/, and __pycache__/. Returns
    absolute paths sorted for determinism.
    """
    if target not in _EXTENSIONS_BY_TARGET:
        raise ValueError(f"unknown target: {target!r}")
    extensions = _EXTENSIONS_BY_TARGET[target]

    workspace = workspace.resolve()
    matches: list[Path] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _EXCLUDED_FILES:
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(workspace).parts):
            continue
        if path.suffix not in extensions:
            continue
        matches.append(path)
    matches.sort()
    return matches

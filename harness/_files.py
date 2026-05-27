"""Shared file enumeration for loc + entropy.

Both LOC and entropy compute over the same set of agent-authored source
files; this helper is the single source of truth for which files are "in"
that set, so the two metrics never drift on file selection.

Josh `.jshd` files are explicitly excluded here. `.jshd` is the binary
output of `josh preprocess` — compiled climate-data blobs in netCDF-like
format. Their byte stream happens to contain newline characters, so a
naive line-count treats a 112 KB binary as ~2000 "lines" of code. That
inflated `src_loc` by 400× on cells where the agent ran preprocessing
against the full grid. Conformance still cares whether `.jshd` files
exist (it indicates the agent ran the preprocessing step), so
`conformance.py` discovers them via its own helper instead of relying
on this enumerator.
"""

from __future__ import annotations

from pathlib import Path

EXCLUDED_DIRS = {"data", "output", "results", "__pycache__", ".git"}
EXCLUDED_FILES = {"run.sh"}

_EXTENSIONS_BY_TARGET = {
    "mesa": (".py",),
    # `.jshd` is binary preprocessed data, not source — see module docstring.
    "josh": (".josh",),
    # josh-mcp produces identical Josh source; only the agent's tool palette differs.
    "josh-mcp": (".josh",),
}


def enumerate_source_files(workspace: Path, target: str) -> list[Path]:
    """Return agent-authored source files under `workspace` for `target`.

    Excludes run.sh, data/, output/, results/, and __pycache__/. Returns
    absolute paths sorted for determinism. Returns text-source extensions
    only; binary data files like Josh's `.jshd` are not included here
    (see module docstring).
    """
    if target not in _EXTENSIONS_BY_TARGET:
        raise ValueError(f"unknown target: {target!r}")
    return find_workspace_files(workspace, _EXTENSIONS_BY_TARGET[target])


def find_workspace_files(workspace: Path, extensions: tuple[str, ...]) -> list[Path]:
    """Find files under `workspace` with any of the given extensions,
    respecting the standard exclusion list (run.sh, data/, output/,
    results/, __pycache__/, .git/). Returns absolute paths sorted for
    determinism.

    Callers that legitimately need binary artefacts (e.g. conformance's
    `.jshd` presence check) use this directly with their own extension
    tuple; LOC and entropy go through `enumerate_source_files` instead.
    """
    workspace = workspace.resolve()
    matches: list[Path] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if path.name in EXCLUDED_FILES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(workspace).parts):
            continue
        if path.suffix not in extensions:
            continue
        matches.append(path)
    matches.sort()
    return matches

"""Mechanical target-conformance check: did the agent use the named framework?

Answers "did the LLM produce Josh-DSL artifacts / a Mesa Python module as
instructed" — independent of whether the resulting model is correct or even
runs. Catches the failure mode where an agent told to use Mesa silently
implements the task in plain Python, or where a Josh prompt produces a
Python module instead of `.josh` / `.jshd` files.

This is the "mechanical" half of phase-5 conformance. The fuzzy
(LLM-judge) half is stubbed in `conformance_fuzzy.py` and deferred.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from _files import enumerate_source_files, find_workspace_files


_MESA_IMPORT_RE = re.compile(r"^\s*(?:import\s+mesa|from\s+mesa(?:\.[\w.]+)?\s+import)\b", re.M)
_MESA_SUBCLASS_RE = re.compile(
    r"class\s+\w+\s*\([^)]*\b(?:mesa\.)?(?:Model|Agent)\b[^)]*\)",
    re.M,
)
_DECIMAL_IMPORT_RE = re.compile(r"^\s*(?:import\s+decimal|from\s+decimal\s+import)\b", re.M)


def _check_mesa(workspace: Path) -> dict:
    py_files = enumerate_source_files(workspace, "mesa")
    imports_mesa = False
    subclasses_model = False
    uses_decimal = False
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _MESA_IMPORT_RE.search(text):
            imports_mesa = True
        if _MESA_SUBCLASS_RE.search(text):
            subclasses_model = True
        if _DECIMAL_IMPORT_RE.search(text):
            uses_decimal = True
    return {
        "imports_mesa": imports_mesa,
        "subclasses_model": subclasses_model,
        "uses_decimal": uses_decimal,
        "has_josh_files": False,
        "has_jshd_files": False,
        "josh_validate_exit_code": None,
        "py_files_counted": [str(p.relative_to(workspace)) for p in py_files],
        "target_conformance": imports_mesa and subclasses_model,
    }


def _check_josh(workspace: Path, parse_timeout_s: int = 30) -> dict:
    # `enumerate_source_files` returns text source (`.josh`) only.
    # `.jshd` is binary preprocessed data — discovered separately so it
    # doesn't pollute LOC / entropy as 2000-"line" binary blobs.
    josh_files = enumerate_source_files(workspace, "josh")
    jshd_files = find_workspace_files(workspace, (".jshd",))
    has_josh = len(josh_files) > 0
    has_jshd = len(jshd_files) > 0

    # Permissive conformance: target_conformance passes if at least one
    # .josh file validates. Catches the "agent left scratch test.josh
    # files that fail josh validate" failure mode without losing the
    # signal — the regression gate is the real backstop on whether the
    # primary model is correct. `josh_validate_exit_code` stays as the
    # worst exit (so a strict reviewer can still see the litter).
    per_file_exits: dict[str, int] = {}
    worst_exit: int | None = None
    any_validates = False
    for path in josh_files:
        try:
            proc = subprocess.run(
                ["josh", "validate", str(path)],
                cwd=str(workspace),
                capture_output=True,
                timeout=parse_timeout_s,
            )
            rc = proc.returncode
        except (FileNotFoundError, subprocess.TimeoutExpired):
            rc = -1
        except Exception:
            rc = -1
        per_file_exits[str(path.relative_to(workspace))] = rc
        if rc == 0:
            any_validates = True
        if worst_exit is None or rc < worst_exit or (rc != 0 and worst_exit == 0):
            worst_exit = rc

    return {
        "imports_mesa": False,
        "subclasses_model": False,
        "uses_decimal": False,
        "has_josh_files": has_josh,
        "has_jshd_files": has_jshd,
        "josh_validate_exit_code": worst_exit,
        "josh_validate_per_file": per_file_exits,
        "josh_files_counted": [str(p.relative_to(workspace)) for p in josh_files],
        "target_conformance": has_josh and any_validates,
    }


def check(workspace: Path, target: str) -> dict:
    """Run the mechanical conformance check for the given target.

    Returns a dict suitable for direct inclusion in the scorer JSON
    record. Always includes a `target_conformance` boolean rollup, plus
    target-specific evidence fields (so a reviewer can see *what*
    conformance check passed or failed).
    """
    workspace = workspace.resolve()
    if target == "mesa":
        out = _check_mesa(workspace)
    elif target in ("josh", "josh-mcp"):
        # josh-mcp emits identical Josh artifacts (.josh + .jshd); same check.
        out = _check_josh(workspace)
    else:
        raise ValueError(f"unknown target: {target!r}")
    out["target"] = target
    return out

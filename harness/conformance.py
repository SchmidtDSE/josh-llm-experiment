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

from _files import enumerate_source_files


_MESA_IMPORT_RE = re.compile(r"^\s*(?:import\s+mesa|from\s+mesa(?:\.[\w.]+)?\s+import)\b", re.M)
_MESA_SUBCLASS_RE = re.compile(
    r"class\s+\w+\s*\([^)]*\b(?:mesa\.)?(?:Model|Agent)\b[^)]*\)",
    re.M,
)


def _check_mesa(workspace: Path) -> dict:
    py_files = enumerate_source_files(workspace, "mesa")
    imports_mesa = False
    subclasses_model = False
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _MESA_IMPORT_RE.search(text):
            imports_mesa = True
        if _MESA_SUBCLASS_RE.search(text):
            subclasses_model = True
    return {
        "imports_mesa": imports_mesa,
        "subclasses_model": subclasses_model,
        "has_josh_files": False,
        "has_jshd_files": False,
        "josh_validate_exit_code": None,
        "py_files_counted": [str(p.relative_to(workspace)) for p in py_files],
        "target_conformance": imports_mesa and subclasses_model,
    }


def _check_josh(workspace: Path, parse_timeout_s: int = 30) -> dict:
    josh_files = [p for p in enumerate_source_files(workspace, "josh") if p.suffix == ".josh"]
    jshd_files = [p for p in enumerate_source_files(workspace, "josh") if p.suffix == ".jshd"]
    has_josh = len(josh_files) > 0
    has_jshd = len(jshd_files) > 0

    worst_exit: int | None = None
    if has_josh:
        worst_exit = 0
        for path in josh_files:
            try:
                proc = subprocess.run(
                    ["josh", "validate", str(path)],
                    cwd=str(workspace),
                    capture_output=True,
                    timeout=parse_timeout_s,
                )
                if proc.returncode != 0:
                    worst_exit = proc.returncode
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                worst_exit = -1
                break
            except Exception:
                worst_exit = -1
                break

    return {
        "imports_mesa": False,
        "subclasses_model": False,
        "has_josh_files": has_josh,
        "has_jshd_files": has_jshd,
        "josh_validate_exit_code": worst_exit,
        "josh_files_counted": [str(p.relative_to(workspace)) for p in josh_files],
        "target_conformance": has_josh and (worst_exit == 0),
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
    elif target == "josh":
        out = _check_josh(workspace)
    else:
        raise ValueError(f"unknown target: {target!r}")
    out["target"] = target
    return out

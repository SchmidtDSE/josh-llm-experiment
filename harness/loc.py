"""Relevant-LOC count, identical rules across targets.

Strips: blank lines, full-line `#` or `//` comments, import-only lines.
Inline trailing comments are NOT stripped (too brittle across languages).

The file selection is delegated to `_files.enumerate_source_files` so loc
and entropy agree on which files count.
"""

from __future__ import annotations

import re
from pathlib import Path

from _files import enumerate_source_files

_BLANK = re.compile(r"^\s*$")
_LINE_COMMENT = re.compile(r"^\s*(#|//)")
_IMPORT_ONLY = re.compile(r"^\s*(import|from)\s")


def _is_relevant(line: str) -> bool:
    if _BLANK.match(line):
        return False
    if _LINE_COMMENT.match(line):
        return False
    if _IMPORT_ONLY.match(line):
        return False
    return True


def count(workspace: Path, target: str) -> dict:
    workspace = workspace.resolve()
    files = enumerate_source_files(workspace, target)

    relevant = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if _is_relevant(line):
                relevant += 1

    return {
        "relevant_loc": relevant,
        "loc_files_counted": [str(p.relative_to(workspace)) for p in files],
    }

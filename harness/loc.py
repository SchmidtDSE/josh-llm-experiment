"""Lines-of-code by category, identical rules across targets.

Classifies each non-blank line as one of:
- `imports_loc`: lines matching `^\\s*(import|from)\\s`
- `comment_loc`: lines matching `^\\s*(#|//)` (full-line only; inline
  trailing comments are not stripped — too brittle across languages)
- `src_loc`: everything else (the substantive code lines)

All three are reported; readers can sum to get total non-blank LOC.
File selection is delegated to `_files.enumerate_source_files` so loc
and entropy agree on which files count.
"""

from __future__ import annotations

import re
from pathlib import Path

from _files import enumerate_source_files

_BLANK = re.compile(r"^\s*$")
_LINE_COMMENT = re.compile(r"^\s*(#|//)")
_IMPORT_ONLY = re.compile(r"^\s*(import|from)\s")


def _classify(line: str) -> str | None:
    """Return one of 'imports', 'comment', 'src', or None for blank."""
    if _BLANK.match(line):
        return None
    if _IMPORT_ONLY.match(line):
        return "imports"
    if _LINE_COMMENT.match(line):
        return "comment"
    return "src"


def count(workspace: Path, target: str) -> dict:
    workspace = workspace.resolve()
    files = enumerate_source_files(workspace, target)

    counts = {"src": 0, "comment": 0, "imports": 0}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            category = _classify(line)
            if category is not None:
                counts[category] += 1

    return {
        "src_loc": counts["src"],
        "comment_loc": counts["comment"],
        "imports_loc": counts["imports"],
        "loc_files_counted": [str(p.relative_to(workspace)) for p in files],
    }

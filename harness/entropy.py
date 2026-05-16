"""Token-level Shannon entropy of generated source.

Uses tiktoken `cl100k_base` as a fixed generic tokenizer so the metric is
comparable across Python (.py) and Josh (.josh / .jshd) source. The file
selection mirrors `loc.py` via the shared `_files` helper.

Returns 0.0 when there are no source files (empty workspace / no relevant
files for the target).
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

import tiktoken

from _files import enumerate_source_files

_ENCODING_NAME = "cl100k_base"
_encoding = tiktoken.get_encoding(_ENCODING_NAME)


def compute(workspace: Path, target: str) -> dict:
    workspace = workspace.resolve()
    files = enumerate_source_files(workspace, target)

    concatenated = []
    for path in files:
        try:
            concatenated.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue

    if not concatenated:
        return {"entropy_bits": 0.0}

    tokens = _encoding.encode("\n".join(concatenated))
    if not tokens:
        return {"entropy_bits": 0.0}

    counts = Counter(tokens)
    total = sum(counts.values())
    entropy = -sum((c / total) * math.log2(c / total) for c in counts.values())

    return {"entropy_bits": round(entropy, 6)}

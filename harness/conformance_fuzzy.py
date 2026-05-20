"""LLM-judge fuzzy conformance — schema placeholder.

Returns a stable null record so the scorer JSON schema is complete and
downstream consumers (the manifest aggregator, batch report) don't
have to special-case a missing field.

The actual LLM-judge passes (the two questions described in
SCORING.md §LLM-judge passes) run **host-side** via opencode against
the completed run's `workspace/`, and write a sibling file
`scorer.fuzzy.json` next to `scorer.json`. They do not flow back
through this module — it just keeps `scorer.json`'s shape stable.
The `target_conformance_fuzzy` field staying `null` here is
intentional: the LLM-judge answers live in `scorer.fuzzy.json`,
not in `scorer.json`.
"""

from __future__ import annotations

from pathlib import Path


def check(workspace: Path, target: str) -> dict:
    return {
        "target_conformance_fuzzy": None,
        "target_conformance_fuzzy_reason": "deferred",
    }

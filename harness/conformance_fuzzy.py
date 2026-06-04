"""LLM-judge fuzzy conformance — schema-shape stub.

Returns a stable null record so the scorer JSON schema is complete
and downstream consumers (manifest aggregator, headline notebook)
don't have to special-case a missing field.

The actual LLM-judge passes (Q1–Q4 in EXPERIMENTAL_DESIGN.md §Scoring
§LLM-judge passes) run **in-Pod** via `containers/run-judge.sh` inside the
scorer container, and write a sibling file `scorer.fuzzy.json` next
to `scorer.json`. They do not flow back through this module — it
just keeps `scorer.json`'s shape stable. The `target_conformance_fuzzy`
field staying `null` here is intentional: the LLM-judge answers live
in `scorer.fuzzy.json`, not in `scorer.json`.
"""

from __future__ import annotations

from pathlib import Path


def check(workspace: Path, target: str) -> dict:
    return {
        "target_conformance_fuzzy": None,
        "target_conformance_fuzzy_reason": "deferred",
    }

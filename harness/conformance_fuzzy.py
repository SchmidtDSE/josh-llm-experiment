"""LLM-judge fuzzy conformance check — DEFERRED in this phase.

Returns a stable null record so the scorer JSON schema is complete and
downstream consumers (the recovery prompt's BINARY_OUTCOMES whitelist,
the manifest aggregator) don't have to special-case a missing field.

When implemented, this module will show the agent's workspace to a
capable model and ask "Does this implementation use $TARGET as its
primary modeling framework? yes / no / partial, one sentence."
"""

from __future__ import annotations

from pathlib import Path


def check(workspace: Path, target: str) -> dict:
    return {
        "target_conformance_fuzzy": None,
        "target_conformance_fuzzy_reason": "deferred",
    }

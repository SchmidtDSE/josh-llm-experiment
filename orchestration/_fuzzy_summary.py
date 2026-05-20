#!/usr/bin/env python3
"""Roll up a batch's per-cell scorer.fuzzy.json into fuzzy_summary.md.

Invoked by orchestration/run_fuzzy_judge.sh after all cells are judged.
Stdout = markdown; stderr = anything weird about specific cells.

The summary is a convenience artefact — short, scannable, organised
around (agent_model × target). The cross-tab of (agent_model ×
judge_model) goes here too so the same-model-self-judges signal is
visible at a glance. The full per-cell drill-down lives in
batch_report.md (generate_batch_report.py folds the same data in).
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_run_meta(cell_dir: Path) -> dict:
    """Return whatever's in run_meta.json (agent model, target, etc.)
    or an empty dict if the run is malformed."""
    meta_path = cell_dir / "run_meta.json"
    if not meta_path.is_file():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load_fuzzy(cell_dir: Path) -> dict | None:
    fz_path = cell_dir / "scorer.fuzzy.json"
    if not fz_path.is_file():
        return None
    try:
        return json.loads(fz_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warn: cannot read {fz_path}: {exc}", file=sys.stderr)
        return None


def collect(batch_dir: Path) -> list[dict]:
    rows = []
    for cell in sorted(batch_dir.iterdir()):
        if not cell.is_dir():
            continue
        if cell.name in {"cell-logs", ".opencode_judge", "fuzzy_opencode_data"}:
            continue
        if not (cell / "workspace").is_dir():
            continue
        meta = load_run_meta(cell)
        fz = load_fuzzy(cell)
        rows.append({
            "run_id": cell.name,
            "model": meta.get("model", "?"),
            "target": meta.get("target", "?"),
            "rung": meta.get("rung", "?"),
            "fuzzy": fz,
        })
    return rows


GLYPH = {"yes": "✓", "partial": "~", "no": "✗"}


def render_q1_distribution(rows: list[dict]) -> str:
    counter: Counter = Counter()
    for r in rows:
        fz = r["fuzzy"] or {}
        q1 = (fz.get("q1") or {})
        ans = q1.get("answer")
        if ans in {"yes", "no", "partial"}:
            counter[ans] += 1
        elif fz.get("parse_error"):
            counter["parse_error"] += 1
        else:
            counter["missing"] += 1
    if not counter:
        return "_(no fuzzy data)_"
    order = ["yes", "partial", "no", "parse_error", "missing"]
    lines = ["| Q1 answer | count |", "|---|---|"]
    for k in order:
        if counter.get(k):
            lines.append(f"| {k} | {counter[k]} |")
    return "\n".join(lines)


def render_cross_tab(rows: list[dict]) -> str:
    """agent_model rows × judge_model cols of Q1 yes/partial/no counts."""
    keyed: dict[tuple[str, str], Counter] = defaultdict(Counter)
    judges = set()
    agents = set()
    for r in rows:
        fz = r["fuzzy"] or {}
        q1 = (fz.get("q1") or {})
        ans = q1.get("answer")
        if ans not in {"yes", "partial", "no"}:
            continue
        judge_id = fz.get("judge_model_id") or "?"
        agents.add(r["model"])
        judges.add(judge_id)
        keyed[(r["model"], judge_id)][ans] += 1
    if not keyed:
        return "_(no parsable fuzzy data — nothing to cross-tabulate)_"

    judges_sorted = sorted(judges)
    agents_sorted = sorted(agents)
    header = "| agent ↓ / judge → | " + " | ".join(judges_sorted) + " |"
    sep = "|---|" + "|".join(["---"] * len(judges_sorted)) + "|"
    lines = [header, sep]
    for a in agents_sorted:
        cells = []
        for j in judges_sorted:
            c = keyed.get((a, j))
            if not c:
                cells.append("—")
            else:
                cells.append(
                    f"{c.get('yes', 0)}✓ {c.get('partial', 0)}~ {c.get('no', 0)}✗"
                )
        lines.append(f"| {a} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_per_cell_table(rows: list[dict]) -> str:
    lines = [
        "| run | model | target | Q1 | Q1 justification | Q2 observations |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["model"], x["target"], x["run_id"])):
        fz = r["fuzzy"]
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        rid_link = f"[`{rid_short}`]({report_rel})"
        if fz is None:
            lines.append(
                f"| {rid_link} | {r['model']} | {r['target']} | "
                f"_(missing)_ | _(missing)_ | _(missing)_ |"
            )
            continue
        if fz.get("parse_error"):
            lines.append(
                f"| {rid_link} | {r['model']} | {r['target']} | "
                f"⚠ parse | {_md_escape(fz.get('parse_error', ''))} | — |"
            )
            continue
        q1 = fz.get("q1") or {}
        q2 = fz.get("q2") or {}
        ans = q1.get("answer", "?")
        glyph = GLYPH.get(ans, "?")
        lines.append(
            f"| {rid_link} | {r['model']} | {r['target']} | "
            f"{glyph} {ans} | {_md_escape(q1.get('justification', ''))} | "
            f"{_md_escape(q2.get('observations', ''))} |"
        )
    return "\n".join(lines)


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def build(batch_dir: Path) -> str:
    rows = collect(batch_dir)
    total = len(rows)
    judged = sum(1 for r in rows if r["fuzzy"] is not None)
    parse_errors = sum(
        1 for r in rows if (r["fuzzy"] or {}).get("parse_error")
    )

    sections = [
        f"# Fuzzy judge summary: `{batch_dir.name}`",
        (
            f"**Cells**: {total} · "
            f"**judged**: {judged}/{total} · "
            f"**parse errors**: {parse_errors}"
        ),
        "## Q1 distribution",
        render_q1_distribution(rows),
        "## Cross-tab (agent × judge model)",
        render_cross_tab(rows),
        "## Per-cell answers",
        render_per_cell_table(rows),
    ]
    return "\n\n".join(sections)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: _fuzzy_summary.py <batch-dir>", file=sys.stderr)
        return 2
    batch_dir = Path(sys.argv[1])
    if not batch_dir.is_dir():
        print(f"not a directory: {batch_dir}", file=sys.stderr)
        return 2
    sys.stdout.write(build(batch_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

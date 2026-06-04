#!/usr/bin/env python3
"""Classify `no_scorer` cells by *why* they failed, so the right remedy is
applied to each — re-score the partial vs. re-run the agent.

A cell is `no_scorer` when `workspace/results/scorer.json` is absent (same rule
as analysis/aggregate.py). This walks already-pulled `runs/<batch>/` dirs and
labels each such cell from its saved artifacts:

  RUNTIME_KILL   — agent ran cleanly (>=1 step exit 0) and was killed mid-step
                   (top step dir has no step_meta.json), no provider errors.
                   A legitimate 2h-deadline timeout → RE-SCORE the partial
                   workspace in place (orchestration/rescore-manifest.csv).
  THROTTLE       — >=1 'AI_APICallError' in step stderr and zero clean steps
                   (e.g. free-tier 429 lockout). Provider infra, no scorable
                   work → reported here; re-run is covered by the deficit
                   re-rep that analysis/00_apply_scoring.ipynb computes.
  INFRA_NONSTART — no agent_meta/ at all (e.g. the api-key Secret-key bug):
                   agent container never started → same (deficit re-rep).
  REVIEW         — none of the above (e.g. all 8 steps present but every
                   exit_code != 0). Printed only — inspect by hand. Expected
                   empty once olmo is dropped.

This script writes only orchestration/rescore-manifest.csv (the RUNTIME_KILL
cells to re-score in place). The throttle/infra/deficit re-rep matrix is owned
by analysis/00_apply_scoring.ipynb so there is a single source of truth for "what
to launch to reach N reps".

Olmo is dropped entirely: any cell whose model is not in PANEL_MODELS is
SKIPPED before classification (reported in the summary, not silently).

Usage:
    python orchestration/classify_no_scorer.py runs/headline-*
    python orchestration/classify_no_scorer.py runs/headline-stage1-20260601 runs/headline-rerep-202606020525
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Reuse aggregate.py's canonical cell-id parser (handles the `josh-mcp` before
# `josh` substring trap) rather than re-deriving the regex here.
sys.path.insert(0, str(REPO_ROOT / "analysis"))
from aggregate import _parse_cell_id  # noqa: E402

# The active 9-model panel (mirrors 01_headline.ipynb MODEL_ORDER). Olmo was
# dropped 2026-06-02 (failed across the board) and is excluded here too.
PANEL_MODELS = {
    "sonnet", "gemma", "kimi", "minimax", "mistral",
    "glm", "qwen", "nemotron", "deepseek",
}

RESCORE_MANIFEST = REPO_ROOT / "orchestration" / "rescore-manifest.csv"


def _step_index(step_dir: Path) -> int:
    try:
        return int(step_dir.name.split("_")[-1])
    except ValueError:
        return -1


def classify(cell_dir: Path) -> tuple[str, dict]:
    """Return (label, info). info carries steps_ok / last_step / api_errs for
    the summary table."""
    am = cell_dir / "agent_meta"
    if not am.is_dir():
        return "INFRA_NONSTART", {"steps_ok": 0, "last_step": "-", "api_errs": 0}

    steps = sorted((am / "steps").glob("step_*"), key=_step_index)
    api_errs = 0
    steps_ok = 0
    for sd in steps:
        log = sd / "agent_stderr.log"
        if log.is_file() and "AI_APICallError" in log.read_text(errors="ignore"):
            api_errs += 1
        meta = sd / "step_meta.json"
        if meta.is_file():
            try:
                if json.loads(meta.read_text()).get("exit_code") == 0:
                    steps_ok += 1
            except (ValueError, OSError):
                pass

    if not steps:
        return "INFRA_NONSTART", {"steps_ok": 0, "last_step": "-", "api_errs": api_errs}

    last = steps[-1]
    last_has_meta = (last / "step_meta.json").is_file()
    info = {"steps_ok": steps_ok, "last_step": last.name, "api_errs": api_errs}

    if api_errs > 0 and steps_ok == 0:
        return "THROTTLE", info
    if (not last_has_meta) and steps_ok >= 1:
        return "RUNTIME_KILL", info
    return "REVIEW", info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch_dirs", nargs="*", type=Path,
                    help="runs/<batch> dirs. Default: glob runs/headline-*")
    args = ap.parse_args()

    batch_dirs = args.batch_dirs or sorted((REPO_ROOT / "runs").glob("headline-*"))
    batch_dirs = [d for d in batch_dirs if d.is_dir()]
    if not batch_dirs:
        raise SystemExit("no batch dirs found (pass runs/<batch> paths or glob runs/headline-*)")

    rescore_rows: list[dict] = []   # RUNTIME_KILL
    rerun_rows: list[tuple[str, str]] = []   # THROTTLE + INFRA_NONSTART → (model, target)
    review: list[tuple] = []
    skipped = 0
    table: list[tuple] = []

    for bd in batch_dirs:
        batch_tag = bd.name
        for cell_dir in sorted(p for p in bd.iterdir() if p.is_dir()):
            if not ((cell_dir / "agent_meta").is_dir() or (cell_dir / "workspace").is_dir()):
                continue
            # Idempotency: only classify cells that lack a scorer.json.
            if (cell_dir / "workspace" / "results" / "scorer.json").is_file():
                continue
            run_id = cell_dir.name
            try:
                model, target, _rep = _parse_cell_id(batch_tag, run_id)
            except ValueError:
                skipped += 1  # unparseable / off-panel model (e.g. olmo if absent from models.yaml)
                continue
            if model not in PANEL_MODELS:
                skipped += 1  # olmo dropped entirely
                continue

            label, info = classify(cell_dir)
            table.append((batch_tag, run_id, f"{model}/{target}", label,
                          info["steps_ok"], info["last_step"], info["api_errs"]))
            if label == "RUNTIME_KILL":
                rescore_rows.append({"orig_batch_tag": batch_tag, "run_id": run_id,
                                     "target": target, "minio_prefix": batch_tag})
            elif label in ("THROTTLE", "INFRA_NONSTART"):
                rerun_rows.append((model, target))
            else:
                review.append((batch_tag, run_id, f"{model}/{target}"))

    # --- write output (rescore manifest only) ---
    RESCORE_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with RESCORE_MANIFEST.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["orig_batch_tag", "run_id", "target", "minio_prefix"])
        w.writeheader()
        w.writerows(rescore_rows)

    # --- summary ---
    print(f"Scanned {len(batch_dirs)} batch(es): {', '.join(d.name for d in batch_dirs)}")
    print()
    hdr = ("batch", "run_id", "model/target", "label", "steps_ok", "last_step", "api_errs")
    widths = [max(len(str(r[i])) for r in (table + [hdr])) for i in range(len(hdr))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*hdr))
    for row in sorted(table, key=lambda r: (r[3], r[0], r[1])):
        print(fmt.format(*[str(x) for x in row]))
    print()
    counts: dict[str, int] = {}
    for r in table:
        counts[r[3]] = counts.get(r[3], 0) + 1
    print("Totals:")
    for label in ("RUNTIME_KILL", "THROTTLE", "INFRA_NONSTART", "REVIEW"):
        print(f"  {label:<14} {counts.get(label, 0)}")
    print(f"  {'SKIPPED':<14} {skipped}  (off-panel models, e.g. olmo — dropped entirely)")
    print()
    print(f"→ {RESCORE_MANIFEST.relative_to(REPO_ROOT)}: {len(rescore_rows)} RUNTIME_KILL cell(s) to re-score")
    print(f"  {len(rerun_rows)} THROTTLE/INFRA cell(s) (no scorable work) — re-run via the "
          f"00_apply_scoring.ipynb deficit re-rep, not a separate matrix")
    if review:
        print()
        print(f"⚠ {len(review)} REVIEW cell(s) need hand inspection (written to neither file):")
        for b, r, mt in review:
            print(f"    {b}/{r}  ({mt})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

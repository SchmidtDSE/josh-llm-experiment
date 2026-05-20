#!/usr/bin/env python3
"""Batch-level rollup of `manifest.jsonl` into a single `batch_report.md`.

Designed for at-a-glance review of an entire batch in one document, with
links into each cell's `report.md` for drill-down. Auto-invoked by
`launch_batch.py` after `aggregate_manifest`; can also be run standalone
against any prior batch dir:

    uv run orchestration/generate_batch_report.py runs/<batch-tag>

The output `batch_report.md` is regenerated on every call; nothing else
in the batch dir is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _fmt_num(v, prec=".3f"):
    if v is None:
        return "—"
    try:
        f = float(v)
        return f"{f:{prec}}"
    except (TypeError, ValueError):
        return "—"


def _fmt_bool(v):
    if v is True:
        return "✓"
    if v is False:
        return "✗"
    return "—"


def _short(s, n):
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _scorer(row):
    return row.get("scorer") or {}


def _consistency(row):
    s = _scorer(row)
    return s.get("consistency") or {}


def _steps(row):
    return row.get("steps") or {}


def _plan_todos(row):
    return row.get("plan_todos") or {}


FUZZY_GLYPH = {"yes": "✓", "partial": "~", "no": "✗"}


def _load_fuzzy(batch_dir: Path, run_id: str) -> dict | None:
    """Read <batch_dir>/<run_id>/scorer.fuzzy.json or None.

    Sidecar file — lives next to scorer.json so re-running the judge
    doesn't touch the mechanical scorer's frozen output.
    """
    fz_path = batch_dir / run_id / "scorer.fuzzy.json"
    if not fz_path.is_file():
        return None
    try:
        return json.loads(fz_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _q1_glyph(fz: dict | None) -> str:
    if fz is None:
        return ""
    if fz.get("parse_error"):
        return "⚠"
    q1 = fz.get("q1") or {}
    return FUZZY_GLYPH.get(q1.get("answer"), "?")


def _fmt_step_glyphs(row) -> str:
    """One-character-per-step status glyph string. ✓ exit 0, ✗ non-zero,
    · step never ran (loop aborted before reaching it). Always 8 wide
    for visual alignment."""
    steps = _steps(row).get("per_step") or []
    exits = {str(s.get("n")).lstrip("0") or "0": s.get("exit_code") for s in steps}
    out = []
    for i in range(1, 9):
        exit_code = exits.get(str(i))
        if exit_code is None:
            out.append("·")
        elif exit_code == 0:
            out.append("✓")
        else:
            out.append("✗")
    return "".join(out)


def _first_error(row):
    s = _scorer(row)
    errs = s.get("csv_schema_errors") or []
    if errs:
        return errs[0]
    tail = (s.get("stderr_tail") or "").strip()
    if tail:
        return f"stderr: {tail.splitlines()[-1] if tail else ''}"
    return ""


def render_matrix(rows: list[dict]) -> str:
    """A pass/fail matrix: rows=model, cols=(rung, target). Cells = "k/N"."""
    models = sorted({r["model"] for r in rows})
    cells = sorted({(r["rung"], r["target"]) for r in rows})

    if not cells:
        return "_(no cells)_"

    header = "| model | " + " | ".join(f"r{r} {t}" for (r, t) in cells) + " |"
    sep = "|---|" + "|".join(["---"] * len(cells)) + "|"
    lines = [header, sep]
    for m in models:
        row_cells = []
        for (rg, tg) in cells:
            matched = [r for r in rows if r["model"] == m and r["rung"] == rg and r["target"] == tg]
            n = len(matched)
            k = sum(1 for r in matched if _scorer(r).get("did_run"))
            cell = f"{k}/{n}"
            if n and k == n:
                cell = f"**{cell}** ✓"
            elif k == 0 and n:
                cell = f"{cell} ✗"
            row_cells.append(cell)
        lines.append(f"| {m} | " + " | ".join(row_cells) + " |")
    return "\n".join(lines)


def render_per_cell_table(rows: list[dict]) -> str:
    header = (
        "| run | model | r | tgt | steps | todos | conf | x_bit | schema | run | h@10 | h✓ | occ@10 | occ✓ | "
        "gr_neg% | gr_ovr% | sp_T | sp_P | dropped | first_error | report |"
    )
    sep = "|" + "|".join(["---"] * 21) + "|"
    lines = [header, sep]
    for r in sorted(rows, key=lambda x: (x["model"], x["rung"], x["target"], x["run_id"])):
        s = _scorer(r)
        c = _consistency(r)
        steps = _steps(r)
        todos = _plan_todos(r)
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        rid_link = f"[`{rid_short}`]({report_rel})"
        report_link = f"[📄 open]({report_rel})"
        first_err = _short(_first_error(r), 50).replace("|", "\\|")
        if steps:
            step_summary = (
                f"{steps.get('completed_count', 0)}/{steps.get('step_count', 0)} "
                f"`{_fmt_step_glyphs(r)}`"
            )
        else:
            step_summary = "—"
        if todos:
            todo_summary = f"{todos.get('checked', 0)}/{todos.get('total', 0)}"
        else:
            todo_summary = "—"
        lines.append("| " + " | ".join([
            rid_link,
            r["model"],
            str(r["rung"]),
            r["target"],
            step_summary,
            todo_summary,
            _fmt_bool(s.get("target_conformance")),
            _fmt_bool(s.get("script_was_executable")),
            _fmt_bool(s.get("csv_schema_ok")),
            _fmt_bool(s.get("did_run")),
            _fmt_num(s.get("height_year10_mean"), ".2f"),
            _fmt_bool(s.get("height_in_range")),
            _fmt_num(s.get("occupancy_year10_mean"), ".2f"),
            _fmt_bool(s.get("occupancy_in_range")),
            _fmt_num(c.get("growth_rate_negative_frac"), ".2f"),
            _fmt_num(c.get("growth_rate_above_ceiling_frac"), ".2f"),
            _fmt_num(c.get("growth_temp_spearman"), ".2f"),
            _fmt_num(c.get("growth_precip_spearman"), ".2f"),
            str(s.get("csv_rows_dropped_nan") or 0),
            first_err,
            report_link,
        ]) + " |")
    return "\n".join(lines)


def render_multi_invocation_section(rows: list[dict]) -> str:
    """Per-cell rundown of the 8-step planning flow: glyph row showing
    per-step exit, todos checked in PLAN.md, link to PLAN.md and to the
    per-step trajectory dir."""
    rows_with_steps = [r for r in rows if _steps(r)]
    if not rows_with_steps:
        return "_(no multi-invocation data — runs predate the planning flow)_"

    header = "| run | model | r | tgt | step exits (1..8) | done | todos `[x]` | links |"
    sep = "|" + "|".join(["---"] * 8) + "|"
    lines = [header, sep]
    for r in sorted(
        rows_with_steps,
        key=lambda x: (x["model"], x["rung"], x["target"], x["run_id"]),
    ):
        steps = _steps(r)
        todos = _plan_todos(r) or {}
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        plan_rel = f"./{r['run_id']}/workspace/PLAN.md"
        steps_dir_rel = f"./{r['run_id']}/agent_artifacts/steps/"
        lines.append("| " + " | ".join([
            f"[`{rid_short}`]({report_rel})",
            r["model"],
            str(r["rung"]),
            r["target"],
            f"`{_fmt_step_glyphs(r)}`",
            f"{steps.get('completed_count', 0)}/{steps.get('step_count', 0)}",
            f"{todos.get('checked', 0)}/{todos.get('total', '—')}"
            if todos else "—",
            f"[📋 PLAN.md]({plan_rel}) · [🪜 steps/]({steps_dir_rel})",
        ]) + " |")
    legend = (
        "\n_Glyphs: `✓` = exit 0, `✗` = non-zero exit, `·` = step never reached "
        "(loop aborted earlier under `FAIL_FAST_ON_STEP_ERROR=true`)._"
    )
    return "\n".join(lines) + legend


def render_failure_tally(rows: list[dict]) -> str:
    counter: Counter = Counter()
    for r in rows:
        if _scorer(r).get("did_run"):
            continue
        err = _first_error(r) or "(no first error captured)"
        counter[_short(err, 100)] += 1
    if not counter:
        return "_(no failures)_"
    lines = ["| count | first observed error |", "|---|---|"]
    for err, n in counter.most_common():
        lines.append(f"| {n} | {err.replace('|', chr(0x7c))} |")
    return "\n".join(lines)


def render_promising_callouts(rows: list[dict]) -> str:
    """Cells that passed target_conformance but didn't complete — most
    interesting drill-down candidates."""
    promising = [
        r for r in rows
        if _scorer(r).get("target_conformance")
        and not _scorer(r).get("did_run")
    ]
    if not promising:
        return "_(none — every target-conforming cell completed, or no target-conforming cells exist)_"
    lines = []
    for r in sorted(promising, key=lambda x: (x["model"], x["rung"], x["target"])):
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        err = _short(_first_error(r), 100)
        lines.append(
            f"- `{rid_short}` — {r['model']} r{r['rung']} {r['target']} — "
            f"`{err}` — [📄 open report]({report_rel})"
        )
    return "\n".join(lines)


def render_passing_callouts(rows: list[dict]) -> str:
    passing = [r for r in rows if _scorer(r).get("did_run")]
    if not passing:
        return "_(no passing cells)_"
    lines = []
    for r in sorted(passing, key=lambda x: (x["model"], x["rung"], x["target"])):
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        s = _scorer(r)
        c = _consistency(r)
        lines.append(
            f"- `{rid_short}` {r['model']} r{r['rung']} {r['target']} — "
            f"h@10={_fmt_num(s.get('height_year10_mean'), '.2f')}, "
            f"occ={_fmt_num(s.get('occupancy_year10_mean'), '.1f')}, "
            f"sp_P={_fmt_num(c.get('growth_precip_spearman'), '.2f')}, "
            f"gr_ovr={_fmt_num(c.get('growth_rate_above_ceiling_frac'), '.2f')} — "
            f"[📄 open report]({report_rel})"
        )
    return "\n".join(lines)


def render_fuzzy_matrix(rows: list[dict], fuzzy_by_id: dict[str, dict]) -> str:
    """At-a-glance grid of Q1 answers per (model, rung+target) cell.

    Cell value: "Yp/Pp/Np" giving counts of yes/partial/no across that
    cell's replicates (parse_error and missing not shown — they pull
    the total below the replicate count, which is the signal).
    """
    models = sorted({r["model"] for r in rows})
    cells = sorted({(r["rung"], r["target"]) for r in rows})
    if not cells:
        return "_(no cells)_"
    header = "| model | " + " | ".join(f"r{r} {t}" for (r, t) in cells) + " |"
    sep = "|---|" + "|".join(["---"] * len(cells)) + "|"
    lines = [header, sep]
    for m in models:
        row_cells = []
        for (rg, tg) in cells:
            matched = [
                r for r in rows
                if r["model"] == m and r["rung"] == rg and r["target"] == tg
            ]
            counts = Counter()
            for r in matched:
                fz = fuzzy_by_id.get(r["run_id"])
                if fz is None:
                    counts["missing"] += 1
                elif fz.get("parse_error"):
                    counts["parse_error"] += 1
                else:
                    ans = (fz.get("q1") or {}).get("answer")
                    if ans in {"yes", "partial", "no"}:
                        counts[ans] += 1
            yes = counts.get("yes", 0)
            partial = counts.get("partial", 0)
            no = counts.get("no", 0)
            cell = f"{yes}✓ {partial}~ {no}✗"
            row_cells.append(cell)
        lines.append(f"| {m} | " + " | ".join(row_cells) + " |")
    return "\n".join(lines)


def render_fuzzy_per_cell(rows: list[dict], fuzzy_by_id: dict[str, dict]) -> str:
    lines = [
        "| run | model | r | tgt | Q1 | Q1 justification | Q2 observations |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["model"], x["rung"], x["target"], x["run_id"])):
        fz = fuzzy_by_id.get(r["run_id"])
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        rid_link = f"[`{rid_short}`]({report_rel})"
        if fz is None:
            lines.append(
                f"| {rid_link} | {r['model']} | {r['rung']} | {r['target']} | "
                f"_(missing)_ | — | — |"
            )
            continue
        if fz.get("parse_error"):
            lines.append(
                f"| {rid_link} | {r['model']} | {r['rung']} | {r['target']} | "
                f"⚠ parse | {_short(fz.get('parse_error', ''), 80)} | — |"
            )
            continue
        q1 = fz.get("q1") or {}
        q2 = fz.get("q2") or {}
        ans = q1.get("answer", "?")
        glyph = FUZZY_GLYPH.get(ans, "?")
        just = (q1.get("justification") or "").replace("|", "\\|").replace("\n", " ")
        obs = (q2.get("observations") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {rid_link} | {r['model']} | {r['rung']} | {r['target']} | "
            f"{glyph} {ans} | {_short(just, 120)} | {_short(obs, 200)} |"
        )
    return "\n".join(lines)


def render_fuzzy_crosstab(rows: list[dict], fuzzy_by_id: dict[str, dict]) -> str:
    keyed: dict[tuple[str, str], Counter] = defaultdict(Counter)
    judges = set()
    agents = set()
    for r in rows:
        fz = fuzzy_by_id.get(r["run_id"])
        if fz is None:
            continue
        ans = (fz.get("q1") or {}).get("answer")
        if ans not in {"yes", "partial", "no"}:
            continue
        judge_id = fz.get("judge_model_id") or "?"
        keyed[(r["model"], judge_id)][ans] += 1
        agents.add(r["model"])
        judges.add(judge_id)
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


def build_report(batch_dir: Path, manifest_path: Path | None = None) -> str:
    if manifest_path is None:
        manifest_path = batch_dir / "manifest.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found at {manifest_path}")

    rows = []
    with manifest_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    fuzzy_by_id: dict[str, dict] = {}
    for r in rows:
        fz = _load_fuzzy(batch_dir, r["run_id"])
        if fz is not None:
            fuzzy_by_id[r["run_id"]] = fz
    has_fuzzy = bool(fuzzy_by_id)

    total = len(rows)
    did_run = sum(1 for r in rows if _scorer(r).get("did_run"))
    schema_ok = sum(1 for r in rows if _scorer(r).get("csv_schema_ok"))
    target_conf = sum(1 for r in rows if _scorer(r).get("target_conformance"))

    # Multi-invocation rollups
    rows_with_steps = [r for r in rows if _steps(r)]
    step_summary_line = ""
    if rows_with_steps:
        total_attempted_steps = sum(_steps(r).get("step_count", 0) for r in rows_with_steps)
        total_passing_steps = sum(_steps(r).get("completed_count", 0) for r in rows_with_steps)
        cells_all_8_passing = sum(
            1 for r in rows_with_steps
            if _steps(r).get("step_count", 0) == 8
            and _steps(r).get("completed_count", 0) == 8
        )
        total_todos_checked = sum(
            (_plan_todos(r) or {}).get("checked", 0) for r in rows_with_steps
        )
        total_todos_possible = sum(
            (_plan_todos(r) or {}).get("total", 0) for r in rows_with_steps
        )
        step_summary_line = (
            f"\n**Multi-invocation**: "
            f"{cells_all_8_passing}/{len(rows_with_steps)} cells with all 8 steps exit 0 · "
            f"steps {total_passing_steps}/{total_attempted_steps} attempted-exit-0 · "
            f"PLAN.md todos {total_todos_checked}/{total_todos_possible} checked"
        )

    fuzzy_summary_line = ""
    if has_fuzzy:
        q1_counts: Counter = Counter()
        for r in rows:
            fz = fuzzy_by_id.get(r["run_id"])
            if fz is None:
                continue
            if fz.get("parse_error"):
                q1_counts["parse_error"] += 1
                continue
            ans = (fz.get("q1") or {}).get("answer")
            if ans in {"yes", "partial", "no"}:
                q1_counts[ans] += 1
        fuzzy_summary_line = (
            f"\n**Fuzzy judge Q1**: "
            f"{q1_counts.get('yes', 0)}✓ {q1_counts.get('partial', 0)}~ "
            f"{q1_counts.get('no', 0)}✗ "
            f"(parse_error={q1_counts.get('parse_error', 0)}, "
            f"missing={total - len(fuzzy_by_id)})"
        )

    sections = [
        f"# Batch: `{batch_dir.name}`",
        (
            f"**Cells**: {total} · "
            f"**did_run**: {did_run}/{total} · "
            f"**schema_ok**: {schema_ok}/{total} · "
            f"**target_conformance**: {target_conf}/{total}"
            + step_summary_line
            + fuzzy_summary_line
        ),
        "## At a glance",
        render_matrix(rows),
        "## Multi-invocation step status",
        render_multi_invocation_section(rows),
        "## Passing cells",
        render_passing_callouts(rows),
        "## Target-conforming but not passing (drill-down candidates)",
        render_promising_callouts(rows),
        "## Failure-mode tally",
        render_failure_tally(rows),
        "## Per-cell detail",
        render_per_cell_table(rows),
    ]
    if has_fuzzy:
        sections += [
            "## Fuzzy judge — at a glance",
            render_fuzzy_matrix(rows, fuzzy_by_id),
            "## Fuzzy judge — per cell",
            render_fuzzy_per_cell(rows, fuzzy_by_id),
            "## Fuzzy judge — agent × judge cross-tab",
            render_fuzzy_crosstab(rows, fuzzy_by_id),
        ]
    sections.append(f"\n_Generated from `{manifest_path.name}` ({total} rows)._")
    return "\n\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="generate_batch_report")
    p.add_argument("batch_dir", type=Path, help="Path to runs/<batch-tag>/")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output markdown path (default: <batch_dir>/batch_report.md)",
    )
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "Manifest JSONL to read (default: <batch_dir>/manifest.jsonl). "
            "Used by rescore_batch.sh to render against manifest.rescored.jsonl. "
            "Relative paths resolve against <batch_dir>."
        ),
    )
    args = p.parse_args(argv)

    if not args.batch_dir.is_dir():
        print(f"error: {args.batch_dir} is not a directory", file=sys.stderr)
        return 2

    manifest_arg: Path | None = args.manifest
    if manifest_arg is not None and not manifest_arg.is_absolute():
        manifest_arg = args.batch_dir / manifest_arg

    md = build_report(args.batch_dir, manifest_path=manifest_arg)
    out_path = args.output or args.batch_dir / "batch_report.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

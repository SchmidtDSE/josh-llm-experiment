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
        "| run | model | r | tgt | conf | x_bit | schema | run | h@10 | h✓ | occ@10 | occ✓ | "
        "gr_neg% | gr_ovr% | sp_T | sp_P | dropped | first_error | report |"
    )
    sep = "|" + "|".join(["---"] * 19) + "|"
    lines = [header, sep]
    for r in sorted(rows, key=lambda x: (x["model"], x["rung"], x["target"], x["run_id"])):
        s = _scorer(r)
        c = _consistency(r)
        rid_short = r["run_id"][:8]
        report_rel = f"./{r['run_id']}/report.md"
        rid_link = f"[`{rid_short}`]({report_rel})"
        report_link = f"[📄 open]({report_rel})"
        first_err = _short(_first_error(r), 50).replace("|", "\\|")
        lines.append("| " + " | ".join([
            rid_link,
            r["model"],
            str(r["rung"]),
            r["target"],
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


def build_report(batch_dir: Path) -> str:
    manifest_path = batch_dir / "manifest.jsonl"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest.jsonl not found at {manifest_path}")

    rows = []
    with manifest_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    total = len(rows)
    did_run = sum(1 for r in rows if _scorer(r).get("did_run"))
    schema_ok = sum(1 for r in rows if _scorer(r).get("csv_schema_ok"))
    target_conf = sum(1 for r in rows if _scorer(r).get("target_conformance"))

    return "\n\n".join([
        f"# Batch: `{batch_dir.name}`",
        (
            f"**Cells**: {total} · "
            f"**did_run**: {did_run}/{total} · "
            f"**schema_ok**: {schema_ok}/{total} · "
            f"**target_conformance**: {target_conf}/{total}"
        ),
        "## At a glance",
        render_matrix(rows),
        "## Passing cells",
        render_passing_callouts(rows),
        "## Target-conforming but not passing (drill-down candidates)",
        render_promising_callouts(rows),
        "## Failure-mode tally",
        render_failure_tally(rows),
        "## Per-cell detail",
        render_per_cell_table(rows),
        f"\n_Generated from `{manifest_path.name}` ({total} rows)._",
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="generate_batch_report")
    p.add_argument("batch_dir", type=Path, help="Path to runs/<batch-tag>/")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output markdown path (default: <batch_dir>/batch_report.md)",
    )
    args = p.parse_args(argv)

    if not args.batch_dir.is_dir():
        print(f"error: {args.batch_dir} is not a directory", file=sys.stderr)
        return 2

    md = build_report(args.batch_dir)
    out_path = args.output or args.batch_dir / "batch_report.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

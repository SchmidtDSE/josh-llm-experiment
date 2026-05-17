#!/usr/bin/env python3
"""Render a single-file Markdown report from a finished runs/<RUN_ID>/ tree.

Designed for CI artifact review: a reviewer can read report.md and see
the full prompt the agent received, every file in the resulting workspace,
the sequence of tool calls, the model's final message, and the scorer
record — without rerunning the workflow.

Defensive parsing: opencode's JSON event shape is loosely versioned. We
walk trajectory.jsonl looking for anything that resembles a tool call or
an assistant message and skip events we don't recognize. If a file is
missing or malformed we render a `_missing_` line rather than crashing.

Usage:
  generate_run_report.py <run-dir>      # writes Markdown to stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FILE_TRUNCATE_BYTES = 2048
TOOL_INPUT_TRUNCATE_CHARS = 400
TEXT_TRUNCATE_CHARS = 4000


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _truncate_bytes(text: str, limit: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="replace"), True


def _render_metadata(run_dir: Path) -> str:
    meta = _read_json(run_dir / "run_meta.json") or {}
    final = _read_json(run_dir / "run_meta.final.json") or {}
    rows = [
        ("run_id", meta.get("run_id")),
        ("model", meta.get("model")),
        ("resolved_model_id", meta.get("resolved_model_id")),
        ("rung", meta.get("rung")),
        ("target", meta.get("target")),
        ("started_at", meta.get("started_at")),
        ("ended_at", final.get("ended_at")),
        ("agent_exit_code", final.get("agent_exit_code")),
        ("wall_clock_backstop_sec", meta.get("wall_clock_backstop_sec")),
        ("stream_stalled", final.get("stream_stalled")),
        ("trajectory_size_bytes", final.get("trajectory_size_bytes")),
        ("stderr_size_bytes", final.get("stderr_size_bytes")),
    ]
    body = ["| Field | Value |", "|---|---|"]
    for key, value in rows:
        body.append(f"| `{key}` | `{value}` |")
    return "\n".join(body)


def _render_prompt(run_dir: Path) -> str:
    prompt = _read_text(run_dir / "prompt.md")
    if prompt is None:
        return "_prompt.md missing_"
    return (
        "<details>\n<summary>Rendered prompt</summary>\n\n"
        "```markdown\n"
        f"{prompt}\n"
        "```\n"
        "</details>"
    )


def _walk_workspace(workspace: Path) -> list[Path]:
    if not workspace.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            files.append(path)
    return files


def _render_workspace(run_dir: Path) -> str:
    workspace = run_dir / "workspace"
    files = _walk_workspace(workspace)
    if not files:
        return "_workspace is empty or missing_"
    parts: list[str] = [f"Files in workspace: **{len(files)}**", ""]
    for path in files:
        rel = path.relative_to(workspace)
        size = path.stat().st_size
        text = _read_text(path)
        if text is None:
            body = "_binary or unreadable_"
            truncated = False
        else:
            body, truncated = _truncate_bytes(text, FILE_TRUNCATE_BYTES)
        suffix = f" — {size} bytes"
        if truncated:
            suffix += " (truncated, full file in artifact)"
        parts.append(
            f"<details>\n<summary><code>{rel}</code>{suffix}</summary>\n\n"
            f"```\n{body}\n```\n"
            "</details>"
        )
    return "\n\n".join(parts)


def _iter_trajectory(run_dir: Path):
    path = run_dir / "trajectory.jsonl"
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _walk(obj, depth: int = 0):
    if depth > 8:
        return
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, depth + 1)


def _extract_tool_calls(run_dir: Path) -> list[tuple[str, str]]:
    """Return ordered list of (tool_name, input_summary).

    Walks the event tree defensively: any dict that looks like a tool
    invocation (has tool/name + input/arguments) gets recorded. Dedupes
    consecutive duplicates that opencode emits when it streams updates.
    """
    calls: list[tuple[str, str]] = []
    for event in _iter_trajectory(run_dir):
        for node in _walk(event):
            tool_name = None
            for key in ("tool", "name", "toolName"):
                if isinstance(node.get(key), str):
                    tool_name = node[key]
                    break
            if not tool_name:
                continue
            tool_input = None
            for key in ("input", "arguments", "args", "params"):
                if key in node:
                    tool_input = node[key]
                    break
            if tool_input is None:
                continue
            try:
                serialized = json.dumps(tool_input, default=str)
            except (TypeError, ValueError):
                serialized = repr(tool_input)
            if len(serialized) > TOOL_INPUT_TRUNCATE_CHARS:
                serialized = serialized[:TOOL_INPUT_TRUNCATE_CHARS] + "…"
            entry = (tool_name, serialized)
            if not calls or calls[-1] != entry:
                calls.append(entry)
    return calls


def _render_trajectory(run_dir: Path) -> str:
    calls = _extract_tool_calls(run_dir)
    if not calls:
        return "_no tool calls parsed from trajectory.jsonl_"
    lines = []
    for i, (tool, inp) in enumerate(calls, 1):
        lines.append(f"{i}. **{tool}** — `{inp}`")
    return "\n".join(lines)


def _extract_last_assistant_text(run_dir: Path) -> str | None:
    last: str | None = None
    for event in _iter_trajectory(run_dir):
        for node in _walk(event):
            role = node.get("role")
            text = node.get("text")
            if isinstance(text, str) and (role == "assistant" or "assistant" in str(node.get("type", ""))):
                last = text
            elif isinstance(node.get("content"), str) and role == "assistant":
                last = node["content"]
    return last


def _render_assistant_text(run_dir: Path) -> str:
    text = _extract_last_assistant_text(run_dir)
    if not text:
        return "_no assistant text found in trajectory_"
    body, truncated = _truncate_bytes(text, TEXT_TRUNCATE_CHARS)
    suffix = "\n\n_(truncated)_" if truncated else ""
    return f"```\n{body}\n```{suffix}"


def _render_scorer(run_dir: Path) -> str:
    scorer = _read_json(run_dir / "scorer.json")
    if scorer is None:
        return "_scorer.json missing or unreadable_"
    fields = [
        "schema_version",
        "target",
        "target_year",
        "did_run",
        "exit_code",
        "wall_time_seconds",
        "timed_out",
        "csv_exists",
        "csv_row_count",
        "csv_schema_ok",
        "csv_schema_errors",
        "height_year10_mean",
        "occupancy_year10_mean",
        "height_in_range",
        "occupancy_in_range",
        "src_loc",
        "comment_loc",
        "imports_loc",
        "entropy_bits",
        "harness_errors",
    ]
    rows = ["| Field | Value |", "|---|---|"]
    for key in fields:
        if key not in scorer:
            continue
        value = scorer[key]
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        rows.append(f"| `{key}` | `{value}` |")
    return "\n".join(rows)


def render(run_dir: Path) -> str:
    sections = [
        f"# Run report — `{run_dir.name}`",
        "",
        "## Metadata",
        _render_metadata(run_dir),
        "",
        "## Prompt",
        _render_prompt(run_dir),
        "",
        "## Workspace inventory",
        _render_workspace(run_dir),
        "",
        "## Tool-call trajectory",
        _render_trajectory(run_dir),
        "",
        "## Last assistant text",
        _render_assistant_text(run_dir),
        "",
        "## Scorer results",
        _render_scorer(run_dir),
        "",
    ]
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(prog="generate_run_report")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    if not args.run_dir.is_dir():
        print(f"not a directory: {args.run_dir}", file=sys.stderr)
        return 2
    sys.stdout.write(render(args.run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

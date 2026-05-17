#!/usr/bin/env python3
"""Render a single-file Markdown report from a finished runs/<RUN_ID>/.

Designed for CI artifact review: a reviewer opens report.md and the very
first thing they see is a verdict card (did the agent ship a working
implementation?), followed by the workspace files claude actually
produced. The full transcript — prompt, every tool call's input JSON,
DNS log — is collapsed into a single bottom "Diagnostics" section so it
doesn't bury the answer.

Data flow:
- `opencode export` (called inside the agent container by
  agent-entrypoint.sh) emits a normalized JSON session into
  <RUN_DIR>/agent_artifacts/session_export.json. We read that here
  rather than walking the streaming `trajectory.jsonl` event log.
- A Jinja2 template at orchestration/templates/report.md.j2 owns the
  report's shape; this script just collects values, decides on per-tool
  compact labels, and hands them to the renderer.

Usage:
  generate_run_report.py <run-dir>      # writes Markdown to stdout
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# Per-file body cap inside the workspace fold. Generous because the content
# is hidden behind <details>; tightened only to avoid a 10MB single-file
# blowup making the report unviewable.
FILE_TRUNCATE_BYTES = 64 * 1024
TEXT_TRUNCATE_CHARS = 8 * 1024  # last assistant message — visible by default

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAME = "report.md.j2"


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


def _metadata(run_dir: Path) -> dict:
    """Flat key→value map for the metadata table. None means 'not recorded'."""
    meta = _read_json(run_dir / "run_meta.json") or {}
    final = _read_json(run_dir / "run_meta.final.json") or {}
    return {
        "run_id": meta.get("run_id"),
        "model": meta.get("model"),
        "resolved_model_id": meta.get("resolved_model_id"),
        "rung": meta.get("rung"),
        "target": meta.get("target"),
        "started_at": meta.get("started_at"),
        "ended_at": final.get("ended_at"),
        "agent_exit_code": final.get("agent_exit_code"),
        "wall_clock_backstop_sec": meta.get("wall_clock_backstop_sec"),
        "stream_stalled": final.get("stream_stalled"),
        "trajectory_size_bytes": final.get("trajectory_size_bytes"),
        "stderr_size_bytes": final.get("stderr_size_bytes"),
    }


def _workspace_files(run_dir: Path) -> list[dict]:
    """Files the agent produced in workspace/, biggest first.

    Sorting by size desc puts the deliverable (typically a multi-KB .py
    or .josh) above bookkeeping files like run.sh.
    """
    workspace = run_dir / "workspace"
    if not workspace.is_dir():
        return []
    files: list[dict] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        text = _read_text(path)
        if text is None:
            body, truncated = "(binary or unreadable)", False
        else:
            body, truncated = _truncate_bytes(text, FILE_TRUNCATE_BYTES)
        files.append(
            {
                "path": str(path.relative_to(workspace)),
                "size": path.stat().st_size,
                "body": body,
                "truncated": truncated,
            }
        )
    files.sort(key=lambda f: (-f["size"], f["path"]))
    return files


def _iter_messages(export: dict) -> list[dict]:
    """Return opencode export's `messages` list (empty if absent)."""
    messages = export.get("messages")
    return messages if isinstance(messages, list) else []


def _msg_role(msg: dict) -> str | None:
    """Role lives at `messages[].info.role` per opencode message-v2 schema."""
    info = msg.get("info")
    if isinstance(info, dict):
        role = info.get("role")
        if isinstance(role, str):
            return role
    return None


def _ellipsize(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _tool_call_label(tool: str, tool_input: object) -> str:
    """One-line label for a tool call. Tries to surface the most useful arg.

    Falls back to "<tool> <comma-separated input keys>" for tools we
    don't have a specific format for, so the trajectory stays scannable
    even when opencode adds a new tool.
    """
    if not isinstance(tool_input, dict):
        return tool

    def s(key: str) -> str | None:
        value = tool_input.get(key)
        return value if isinstance(value, str) else None

    if tool == "write":
        path = s("filePath") or s("path") or "?"
        content = tool_input.get("content")
        size = len(content) if isinstance(content, str) else None
        return f"write {path}" + (f" ({size} bytes)" if size is not None else "")
    if tool == "edit":
        return f"edit {s('filePath') or s('path') or '?'}"
    if tool == "read":
        return f"read {s('filePath') or s('path') or '?'}"
    if tool == "bash":
        return f"bash $ {_ellipsize(s('command') or '', 80)}"
    if tool == "glob":
        return f"glob {s('pattern') or '?'}"
    if tool == "grep":
        return f"grep {s('pattern') or '?'}"
    if tool == "webfetch":
        return f"webfetch {s('url') or '?'}"
    if tool == "todowrite":
        todos = tool_input.get("todos")
        n = len(todos) if isinstance(todos, list) else None
        return f"todowrite ({n} todos)" if n is not None else "todowrite"
    if tool == "invalid":
        attempted = s("tool")
        return f"invalid (tried '{attempted}')" if attempted else "invalid"
    keys = ", ".join(sorted(tool_input.keys())) or "(empty)"
    return f"{tool} [{keys}]"


def _tool_calls_from_export(export: dict) -> list[dict]:
    """Extract tool invocations as a flat list, with a compact label per call.

    Per opencode's ToolPart schema (message-v2.ts upstream):
      { "type": "tool", "tool": "<name>",
        "state": { "status": ..., "input": {...}, ... } }
    """
    calls: list[dict] = []
    for msg in _iter_messages(export):
        parts = msg.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            tool_name = part.get("tool")
            if not isinstance(tool_name, str):
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_input = state.get("input")
            try:
                input_json = json.dumps(tool_input, indent=2, default=str)
            except (TypeError, ValueError):
                input_json = repr(tool_input)
            calls.append(
                {
                    "tool": tool_name,
                    "label": _tool_call_label(tool_name, tool_input),
                    "input_json": input_json,
                }
            )
    return calls


def _tool_call_counts(calls: list[dict]) -> list[tuple[str, int]]:
    """[(tool_name, count), ...] sorted by count desc, tie-break by name."""
    counter = Counter(call["tool"] for call in calls)
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def _last_assistant_text(export: dict) -> tuple[str, bool]:
    """Concatenate `type: text` parts of the last assistant message."""
    for msg in reversed(_iter_messages(export)):
        if _msg_role(msg) != "assistant":
            continue
        parts = msg.get("parts")
        if not isinstance(parts, list):
            continue
        texts = [
            part["text"]
            for part in parts
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ]
        joined = "\n".join(texts).strip()
        if joined:
            return _truncate_bytes(joined, TEXT_TRUNCATE_CHARS)
    return "", False


SCORER_FIELDS = [
    "schema_version", "target", "target_year", "did_run",
    "exit_code", "wall_time_seconds", "timed_out",
    "csv_exists", "csv_row_count", "csv_schema_ok", "csv_schema_errors",
    "height_year10_mean", "occupancy_year10_mean",
    "height_in_range", "occupancy_in_range",
    "src_loc", "comment_loc", "imports_loc",
    "entropy_bits", "harness_errors",
]


def _scorer_record(run_dir: Path) -> dict | None:
    scorer = _read_json(run_dir / "scorer.json")
    if scorer is None:
        return None
    return {k: scorer[k] for k in SCORER_FIELDS if k in scorer}


def _verdict(scorer: dict | None) -> list[dict]:
    """Top-of-report ✓/✗ rows. Empty list if no scorer.json."""
    if not scorer:
        return []
    rows: list[dict] = []

    def row(label: str, value):
        rows.append({"label": label, "ok": bool(value), "value": value})

    row("did_run", scorer.get("did_run"))
    row("csv_schema_ok", scorer.get("csv_schema_ok"))
    row("height_in_range", scorer.get("height_in_range"))
    row("occupancy_in_range", scorer.get("occupancy_in_range"))
    return rows


_DNS_QUERY_RE = re.compile(r"query\[[A-Z]+\]\s+(\S+)\s+from")


def _dns_summary(run_dir: Path) -> list[tuple[str, int]] | None:
    """Aggregate dnsmasq query log into [(host, count), ...] desc by count.

    Returns None if dns.log doesn't exist (e.g., phase-4b sidecar wasn't
    wired in for this run). Returns [] if the file exists but had no
    matching queries.
    """
    path = run_dir / "dns.log"
    if not path.is_file():
        return None
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    hosts = Counter(_DNS_QUERY_RE.findall(body))
    return sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))


def render(run_dir: Path) -> str:
    export = _read_json(run_dir / "agent_artifacts" / "session_export.json") or {}
    text, text_truncated = _last_assistant_text(export)
    metadata = _metadata(run_dir)
    # Prefer the recorded run_id over the mount-dir name — the workflow
    # mounts <RUN_DIR> at /run, so run_dir.name is "run" inside CI.
    run_id = metadata.get("run_id") or run_dir.name
    scorer = _scorer_record(run_dir)
    tool_calls = _tool_calls_from_export(export)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    template = env.get_template(TEMPLATE_NAME)
    return template.render(
        run_id=run_id,
        metadata=metadata,
        prompt=(_read_text(run_dir / "prompt.md") or ""),
        workspace_files=_workspace_files(run_dir),
        tool_calls=tool_calls,
        tool_call_counts=_tool_call_counts(tool_calls),
        last_assistant_text=text,
        last_assistant_text_truncated=text_truncated,
        scorer=scorer,
        verdict=_verdict(scorer),
        dns_summary=_dns_summary(run_dir),
    )


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

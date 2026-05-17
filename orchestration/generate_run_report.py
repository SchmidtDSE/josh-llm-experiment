#!/usr/bin/env python3
"""Render a single-file Markdown report from a finished runs/<RUN_ID>/.

Designed for CI artifact review: a reviewer can read report.md and see
the full prompt the agent received, every file in the resulting workspace,
the sequence of tool calls, the model's final message, and the scorer
record — without rerunning the workflow.

Data flow:
- `opencode export` (called inside the agent container by
  agent-entrypoint.sh) emits a normalized JSON session into
  <RUN_DIR>/agent_artifacts/session_export.json. We read that here
  rather than walking the streaming `trajectory.jsonl` event log.
- A Jinja2 template at orchestration/templates/report.md.j2 owns the
  report's shape; this script just collects values and hands them to
  the renderer.

Usage:
  generate_run_report.py <run-dir>      # writes Markdown to stdout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

FILE_TRUNCATE_BYTES = 2048
TOOL_INPUT_TRUNCATE_CHARS = 400
TEXT_TRUNCATE_CHARS = 4000

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
    """Flat key→str map for the metadata table. None means 'not recorded'."""
    meta = _read_json(run_dir / "run_meta.json") or {}
    final = _read_json(run_dir / "run_meta.final.json") or {}
    rows = {
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
    return rows


def _workspace_files(run_dir: Path) -> list[dict]:
    workspace = run_dir / "workspace"
    if not workspace.is_dir():
        return []
    files: list[dict] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace)
        text = _read_text(path)
        if text is None:
            body, truncated = "_binary or unreadable_", False
        else:
            body, truncated = _truncate_bytes(text, FILE_TRUNCATE_BYTES)
        files.append(
            {
                "path": str(rel),
                "size": path.stat().st_size,
                "body": body,
                "truncated": truncated,
            }
        )
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


def _tool_calls_from_export(export: dict) -> list[dict]:
    """Pull tool invocations out of every message's parts.

    Per opencode's ToolPart schema:
      { "type": "tool", "tool": "<name>", "state": { "status": ..., "input": {...}, ... } }
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
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_input = state.get("input")
            if not isinstance(tool_name, str):
                continue
            try:
                serialized = json.dumps(tool_input, default=str)
            except (TypeError, ValueError):
                serialized = repr(tool_input)
            if len(serialized) > TOOL_INPUT_TRUNCATE_CHARS:
                serialized = serialized[:TOOL_INPUT_TRUNCATE_CHARS] + "…"
            calls.append({"tool": tool_name, "input": serialized})
    return calls


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
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
        ]
        joined = "\n".join(texts).strip()
        if joined:
            return _truncate_bytes(joined, TEXT_TRUNCATE_CHARS)
    return "", False


def _scorer_record(run_dir: Path) -> dict | None:
    """Flat key→value map of fields the report surfaces."""
    scorer = _read_json(run_dir / "scorer.json")
    if scorer is None:
        return None
    fields = [
        "schema_version", "target", "target_year", "did_run",
        "exit_code", "wall_time_seconds", "timed_out",
        "csv_exists", "csv_row_count", "csv_schema_ok", "csv_schema_errors",
        "height_year10_mean", "occupancy_year10_mean",
        "height_in_range", "occupancy_in_range",
        "src_loc", "comment_loc", "imports_loc",
        "entropy_bits", "harness_errors",
    ]
    out: dict = {}
    for key in fields:
        if key not in scorer:
            continue
        value = scorer[key]
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        out[key] = value
    return out


def render(run_dir: Path) -> str:
    export = _read_json(run_dir / "agent_artifacts" / "session_export.json") or {}
    text, text_truncated = _last_assistant_text(export)
    metadata = _metadata(run_dir)
    # Prefer the recorded run_id over the mount-dir name — the workflow
    # mounts <RUN_DIR> at /run, so run_dir.name is "run" inside CI.
    run_id = metadata.get("run_id") or run_dir.name

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
        tool_calls=_tool_calls_from_export(export),
        last_assistant_text=text,
        last_assistant_text_truncated=text_truncated,
        scorer=_scorer_record(run_dir),
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

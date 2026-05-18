#!/usr/bin/env python3
"""Render an opencode session export into a readable Markdown transcript.

Reads `<RUN_DIR>/agent_artifacts/session_export.json` and writes
`<RUN_DIR>/transcript.md`. The export is opencode's canonical post-hoc dump
of the conversation; this script is just a presentation layer.

The transcript is the lossless human-readable companion to `report.md`.
Where the report optimises for "what happened in this cell?", the transcript
optimises for "what did the model actually say at each step?".

Schema notes (opencode 1.14.50):
  messages[].info.role               — user / assistant / system
  messages[].info.time.created       — ms epoch
  messages[].parts[].type            — text / tool / step-start / step-finish
  parts[type=text].text              — Markdown body
  parts[type=tool].tool              — tool name (webfetch, bash, read, …)
  parts[type=tool].state.input       — tool input (dict)
  parts[type=tool].state.output      — tool output (string)
  parts[type=tool].state.time.{start,end}  — ms epoch
  parts[type=step-start|step-finish] — assistant-step bookends; skipped

Usage:
  extract_transcript.py <RUN_DIR>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOL_OUTPUT_TRUNCATE = 4096  # chars; long blobs get a notice + truncation


def fmt_ts(ms: int | None) -> str:
    """Format ms-epoch as `YYYY-MM-DD HH:MM:SSZ`. None → empty."""
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%SZ"
    )


def fence(body: str, lang: str = "") -> str:
    """Wrap `body` in a fenced code block, picking a fence length that
    won't collide with backtick runs inside the body. Most outputs are
    fine with three backticks; long tool outputs occasionally include
    triple-backtick fences themselves."""
    body = body or ""
    n = 3
    while ("`" * n) in body:
        n += 1
    fence = "`" * n
    return f"{fence}{lang}\n{body}\n{fence}"


def render_text_part(part: dict) -> str:
    return (part.get("text") or "").strip()


def render_tool_part(part: dict) -> str:
    tool = part.get("tool") or "unknown"
    state = part.get("state") or {}
    status = state.get("status") or "?"
    inp = state.get("input")
    out = state.get("output") or ""
    t = state.get("time") or {}
    duration_ms = (t.get("end") or 0) - (t.get("start") or 0)
    dur_label = f" · {duration_ms} ms" if duration_ms > 0 else ""

    out_truncated = len(out) > TOOL_OUTPUT_TRUNCATE
    if out_truncated:
        out = (
            out[:TOOL_OUTPUT_TRUNCATE]
            + f"\n... [truncated {len(state.get('output', '')) - TOOL_OUTPUT_TRUNCATE} chars]"
        )

    chunks = [f"**Tool call:** `{tool}` ({status}{dur_label})"]
    if inp:
        chunks.append("Input:")
        chunks.append(fence(json.dumps(inp, indent=2), "json"))
    if out:
        chunks.append("Output:")
        chunks.append(fence(out))
    return "\n\n".join(chunks)


def render_message(msg: dict) -> str:
    info = msg.get("info") or {}
    role = info.get("role") or "?"
    ts_ms = (info.get("time") or {}).get("created")
    body_chunks: list[str] = []
    for part in msg.get("parts") or []:
        ptype = part.get("type")
        if ptype == "text":
            text = render_text_part(part)
            if text:
                body_chunks.append(text)
        elif ptype == "tool":
            body_chunks.append(render_tool_part(part))
        # step-start, step-finish: internal bookends, no user-visible content
    if not body_chunks:
        # Skip messages that carry only step markers (very common — every
        # assistant turn opens with step-start; we don't want a forest of
        # empty "## assistant" headers in the transcript).
        return ""
    header = f"## {role}"
    if ts_ms:
        header += f"  · {fmt_ts(ts_ms)}"
    return header + "\n\n" + "\n\n".join(body_chunks)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_transcript.py <RUN_DIR>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1]).resolve()
    export_path = run_dir / "agent_artifacts" / "session_export.json"
    out_path = run_dir / "transcript.md"

    if not export_path.exists():
        print(f"No session export at {export_path}", file=sys.stderr)
        return 3

    try:
        export = json.loads(export_path.read_text())
    except json.JSONDecodeError as e:
        print(f"Failed to parse {export_path}: {e}", file=sys.stderr)
        return 4

    info = export.get("info") or {}
    messages = export.get("messages") or []
    model = (info.get("model") or {}).get("id") or "?"
    title = info.get("title") or info.get("slug") or run_dir.name
    cost = info.get("cost")
    tokens = info.get("tokens") or {}

    header_lines = [
        f"# Transcript — {title}",
        "",
        f"- **Session ID:** `{info.get('id', '?')}`",
        f"- **Model:** `{model}`",
        f"- **Messages:** {len(messages)}",
    ]
    if cost is not None:
        header_lines.append(f"- **Cost:** ${cost:.4f}")
    if tokens:
        cache = tokens.get("cache") or {}
        header_lines.append(
            f"- **Tokens:** in={tokens.get('input', 0)} "
            f"out={tokens.get('output', 0)} "
            f"reasoning={tokens.get('reasoning', 0)} "
            f"cache_read={cache.get('read', 0)} "
            f"cache_write={cache.get('write', 0)}"
        )
    header_lines.append("")
    header_lines.append("---")
    header_lines.append("")

    rendered = [render_message(m) for m in messages]
    rendered = [r for r in rendered if r]

    out_path.write_text("\n".join(header_lines) + "\n\n".join(rendered) + "\n")
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

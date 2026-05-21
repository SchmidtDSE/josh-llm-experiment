#!/usr/bin/env python3
"""Render an opencode session export into a readable Markdown transcript.

Writes `<RUN_DIR>/transcript.md` from whichever session-export layout
the cell carries. Two layouts are supported, tried in order:

  1. **Per-step (k8s).** `<RUN_DIR>/agent_meta/steps/step_NN/session_export.json`
     — one export per opencode invocation in the 8-step multi-invocation
     flow. The script concatenates messages from all step exports in
     order. This is the layout the k8s scorer container sees.
  2. **Single export (legacy / local-orchestration).**
     `<RUN_DIR>/agent_artifacts/session_export.json` — opencode's
     canonical post-hoc dump from `opencode export`. Used by older
     batches before the k8s refactor (Phase 6) put the per-step exports
     in `agent_meta/`.

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


def _discover_sources(run_dir: Path) -> tuple[str, list[Path]]:
    """Find the session-export files for this run. Returns (layout, paths).

    Tries k8s `agent_meta/steps/step_NN/session_export.json` first; falls
    back to the legacy single `agent_artifacts/session_export.json`. The
    layout label drives header rendering downstream.
    """
    steps_dir = run_dir / "agent_meta" / "steps"
    if steps_dir.is_dir():
        step_exports = sorted(steps_dir.glob("step_*/session_export.json"))
        if step_exports:
            return "per-step", step_exports
    legacy = run_dir / "agent_artifacts" / "session_export.json"
    if legacy.exists():
        return "single", [legacy]
    return "missing", []


def _load_export(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"Failed to parse {path}: {e}", file=sys.stderr)
        return None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_transcript.py <RUN_DIR>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1]).resolve()
    out_path = run_dir / "transcript.md"

    layout, paths = _discover_sources(run_dir)
    if layout == "missing":
        print(
            f"No session export under {run_dir} "
            f"(checked agent_meta/steps/ + agent_artifacts/)",
            file=sys.stderr,
        )
        return 3

    exports: list[tuple[Path, dict]] = []
    for p in paths:
        d = _load_export(p)
        if d is None:
            return 4
        exports.append((p, d))

    # Aggregate header values across step exports. The per-step exports
    # each carry their own info/cost/tokens; we sum where it makes
    # sense and prefer the last step's model/title as the canonical
    # cell-level label (all steps run against the same agent slug).
    all_messages: list[dict] = []
    total_cost = 0.0
    tot = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    final_info: dict = {}
    for _, export in exports:
        all_messages.extend(export.get("messages") or [])
        info = export.get("info") or {}
        final_info = info or final_info
        try:
            total_cost += float(info.get("cost") or 0.0)
        except (TypeError, ValueError):
            pass
        tokens = info.get("tokens") or {}
        cache = tokens.get("cache") or {}
        tot["input"] += int(tokens.get("input") or 0)
        tot["output"] += int(tokens.get("output") or 0)
        tot["reasoning"] += int(tokens.get("reasoning") or 0)
        tot["cache_read"] += int(cache.get("read") or 0)
        tot["cache_write"] += int(cache.get("write") or 0)

    model = (final_info.get("model") or {}).get("id") or "?"
    title = final_info.get("title") or final_info.get("slug") or run_dir.name

    if layout == "per-step":
        source_label = f"{len(paths)} per-step exports under agent_meta/steps/"
    else:
        source_label = "agent_artifacts/session_export.json"

    header_lines = [
        f"# Transcript — {title}",
        "",
        f"- **Source:** {source_label}",
        f"- **Session ID:** `{final_info.get('id', '?')}`",
        f"- **Model:** `{model}`",
        f"- **Messages:** {len(all_messages)}",
        f"- **Cost (sum):** ${total_cost:.4f}",
        (
            f"- **Tokens (sum):** in={tot['input']} out={tot['output']} "
            f"reasoning={tot['reasoning']} cache_read={tot['cache_read']} "
            f"cache_write={tot['cache_write']}"
        ),
        "",
        "---",
        "",
    ]

    rendered = [render_message(m) for m in all_messages]
    rendered = [r for r in rendered if r]

    out_path.write_text("\n".join(header_lines) + "\n\n".join(rendered) + "\n")
    print(f"Wrote {out_path} ({layout} layout, {len(all_messages)} messages)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

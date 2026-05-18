#!/usr/bin/env python3
"""Compute a time breakdown for one experimental cell.

Reads the cell's `run_meta.json`, `run_meta.final.json`, and
`agent_artifacts/session_export.json` and writes
`<RUN_DIR>/time_breakdown.json`. The goal is to disambiguate

  wall  =  boot  +  tool  +  other

so the operator can tell apart a slow cell because the LLM was actually
working (high `other`, healthy `tool_calls`) from a slow cell that
stalled (high `other`, low `tool_calls`, `stream_stalled=true`).

We don't have direct timestamps on opencode's `step-start`/`step-finish`
parts (they carry no `time.created`), so we can't perfectly separate
inference time from idle time. `other_seconds` is the residual after
subtracting tool time and boot, and is the right thing to inspect
alongside `stream_stalled` when triaging.

Fields written:
  wall_time_seconds              cell start → cell end (run_meta)
  boot_time_seconds              cell start → first message timestamp
  tool_time_seconds              sum of (tool.state.time.end - .start)
  other_seconds                  wall - boot - tool (mixed inference + idle)
  stream_stalled                 from run_meta.final.json (idle-watcher fired)
  tool_calls                     count of tool parts
  tool_breakdown_seconds         {tool_name: seconds} per tool
  messages                       {user: N, assistant: N}
  tokens                         {input, output, reasoning, cache_read, cache_write}

Usage:
  extract_time_breakdown.py <RUN_DIR>
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_iso(s: str | None) -> float | None:
    """ISO-8601 (with or without trailing Z) → epoch seconds, or None."""
    if not s:
        return None
    # Python's fromisoformat doesn't accept the trailing Z in 3.10 and earlier.
    s_norm = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        return datetime.fromisoformat(s_norm).timestamp()
    except ValueError:
        return None


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: extract_time_breakdown.py <RUN_DIR>", file=sys.stderr)
        return 2
    run_dir = Path(sys.argv[1]).resolve()
    meta_path = run_dir / "run_meta.json"
    final_path = run_dir / "run_meta.final.json"
    export_path = run_dir / "agent_artifacts" / "session_export.json"
    out_path = run_dir / "time_breakdown.json"

    if not meta_path.exists():
        print(f"No run_meta.json at {meta_path}", file=sys.stderr)
        return 3

    meta = json.loads(meta_path.read_text())
    final = json.loads(final_path.read_text()) if final_path.exists() else {}
    export = json.loads(export_path.read_text()) if export_path.exists() else {}

    start_epoch = parse_iso(meta.get("started_at"))
    end_epoch = parse_iso(final.get("ended_at"))
    wall = (
        round(end_epoch - start_epoch, 3)
        if (start_epoch and end_epoch)
        else None
    )

    messages = export.get("messages") or []
    info = export.get("info") or {}

    # First-message timestamp marks "agent reached opencode prompt loop".
    # Anything before that is boot (docker run, opencode CLI init, npm shim).
    first_msg_ms = None
    if messages:
        ti = (messages[0].get("info") or {}).get("time") or {}
        first_msg_ms = ti.get("created")
    boot = (
        round(first_msg_ms / 1000 - start_epoch, 3)
        if (first_msg_ms and start_epoch)
        else None
    )

    tool_total_ms = 0
    tool_breakdown: dict[str, int] = {}
    tool_calls = 0
    for msg in messages:
        for part in msg.get("parts") or []:
            if part.get("type") != "tool":
                continue
            tool_calls += 1
            state = part.get("state") or {}
            t = state.get("time") or {}
            dur = (t.get("end") or 0) - (t.get("start") or 0)
            if dur > 0:
                tool_total_ms += dur
                name = part.get("tool") or "unknown"
                tool_breakdown[name] = tool_breakdown.get(name, 0) + dur

    tool_seconds = round(tool_total_ms / 1000.0, 3)
    tool_breakdown_seconds = {
        k: round(v / 1000.0, 3) for k, v in tool_breakdown.items()
    }
    other = None
    if wall is not None and boot is not None:
        # other = wall - boot - tool. Clamp at zero defensively; with clock
        # skew between the host (run_meta) and the agent container (export
        # timestamps) this can come out very slightly negative.
        other = max(0.0, round(wall - boot - tool_seconds, 3))

    msg_counts = {"user": 0, "assistant": 0, "system": 0}
    for msg in messages:
        role = ((msg.get("info") or {}).get("role")) or "?"
        if role in msg_counts:
            msg_counts[role] += 1

    tokens = info.get("tokens") or {}
    cache = tokens.get("cache") or {}
    token_summary = {
        "input": tokens.get("input", 0),
        "output": tokens.get("output", 0),
        "reasoning": tokens.get("reasoning", 0),
        "cache_read": cache.get("read", 0),
        "cache_write": cache.get("write", 0),
    }

    out = {
        "run_id": meta.get("run_id"),
        "model": meta.get("model"),
        "rung": meta.get("rung"),
        "target": meta.get("target"),
        "wall_time_seconds": wall,
        "boot_time_seconds": boot,
        "tool_time_seconds": tool_seconds,
        "other_seconds": other,
        "stream_stalled": bool(final.get("stream_stalled", False)),
        "agent_exit_code": final.get("agent_exit_code"),
        "tool_calls": tool_calls,
        "tool_breakdown_seconds": tool_breakdown_seconds,
        "messages": msg_counts,
        "tokens": token_summary,
        "cost_usd": info.get("cost"),
    }
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"Wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

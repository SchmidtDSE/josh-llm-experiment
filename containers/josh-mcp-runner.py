"""Generic MCP-call forwarder for the josh-mcp arm's scorer-time run.

Installed into /sandbox/runner.py by the per-cell setup initContainer when
TARGET=josh-mcp (alongside a one-line /sandbox/run.sh shim that execs us).
The agent does not edit this file; the agent authors /sandbox/mcp_calls.json
instead.

Contract:
- mcp_calls.json is a JSON array of {"tool": <name>, "arguments": {...}}
  objects mirroring the MCP `tools/call` interface. Entries are executed
  in order, top to bottom.
- This runner opens one `josh mcp` stdio session, forwards every entry to
  `session.call_tool(name, arguments)` in order, and aborts on the first
  isError. No translation, no schema validation — what's in the JSON is
  what gets sent.

Two ergonomic mutations the runner does for the agent:

1. **Strip a leading `josh_` from tool names.** During iteration the
   agent sees its tools prefixed with the server name (`josh_preprocess_data`
   etc. — opencode does that). When this runner talks to `josh mcp`
   directly the server-side names are bare (`preprocess_data` etc.).
   The agent can record either form in the JSON; the runner strips the
   prefix if present so both work.

2. **Override `arguments.replicates` from the N_REPLICATES env var on any
   `run_simulation` call.** The scorer's harness/runner.py sets
   N_REPLICATES=100 on the subprocess env before invoking `./run.sh`;
   the shim execs us, so we inherit it. Mirrors the bash arms' contract
   exactly — agents write whatever replicate count they self-tested at
   (likely 2) and the scoring run silently overrides to 100, so the
   agent never has to deal with env quoting or sentinel strings.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

WORKSPACE = Path("/sandbox")
CONFIG_PATH = WORKSPACE / "mcp_calls.json"


async def main() -> int:
    if not CONFIG_PATH.is_file():
        print(f"ERROR: {CONFIG_PATH} not found — agent must author the MCP "
              f"call list before scoring (see prompts/targets/josh-mcp.md)",
              file=sys.stderr)
        return 2

    n_replicates = int(os.environ.get("N_REPLICATES", "2"))
    calls = json.loads(CONFIG_PATH.read_text())
    if not isinstance(calls, list):
        print(f"ERROR: {CONFIG_PATH} must be a JSON array; "
              f"got {type(calls).__name__}", file=sys.stderr)
        return 2

    params = StdioServerParameters(command="josh", args=["mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for i, entry in enumerate(calls, start=1):
                tool = entry["tool"]
                if tool.startswith("josh_"):
                    tool = tool[len("josh_"):]
                arguments = dict(entry["arguments"])
                # Scoring-time replicate override — mirrors the bash arm's
                # ${N_REPLICATES:-2} substitution in run.sh. Agent's
                # authored value is replaced silently; the agent never has
                # to thread an env var through the JSON.
                if tool == "run_simulation":
                    arguments["replicates"] = n_replicates
                print(f"[{i}/{len(calls)}] call_tool({tool!r})", flush=True)
                result = await session.call_tool(tool, arguments)
                if result.isError:
                    print(f"ERROR: {tool} returned isError=True:", file=sys.stderr)
                    for block in result.content:
                        text = getattr(block, "text", repr(block))
                        print(f"  {text}", file=sys.stderr)
                    return 1
    print(f"OK: {len(calls)} calls completed, N_REPLICATES={n_replicates}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

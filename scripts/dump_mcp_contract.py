"""Dump the live `josh mcp` tool schemas as markdown for prompts/targets/josh-mcp.md.

Run after bumping JOSH_JAR_SHA256 in the Dockerfile to refresh the
authoritative tool contract appended to the josh-mcp directive. Pipes
markdown to stdout; redirect or paste under the `## MCP tool reference`
heading in prompts/targets/josh-mcp.md.

Invocation (against the scorer image with the new jar baked in):

    SHA=$(git rev-parse --short HEAD)
    docker run --rm --network=none \\
      -v $(pwd)/scripts/dump_mcp_contract.py:/tmp/dump.py \\
      ghcr.io/schmidtdse/josh-llm-experiment/fortree-scorer:$SHA \\
      python /tmp/dump.py 2>/dev/null > /tmp/mcp_contract.md
"""
from __future__ import annotations

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def fmt_field(name: str, schema: dict, required: list[str]) -> str:
    typ = schema.get("type", "?")
    desc = (schema.get("description") or "").strip()
    flag = "**required**" if name in required else "optional"
    return f"  - `{name}` ({typ}, {flag}) — {desc}"


async def main() -> None:
    params = StdioServerParameters(command="josh", args=["mcp"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools

    # Order: the two tools the agent actually calls first, then validate/discover.
    priority = ["preprocess_data", "run_simulation", "validate_simulation", "discover_config"]
    tools = sorted(tools, key=lambda t: (priority.index(t.name) if t.name in priority else 99, t.name))

    out: list[str] = []
    for t in tools:
        sch = t.inputSchema or {}
        required = sch.get("required", [])
        props = sch.get("properties", {})
        out.append(f"### `josh_{t.name}`")
        out.append("")
        out.append((t.description or "").strip())
        out.append("")
        out.append("Arguments:")
        for name, p in props.items():
            out.append(fmt_field(name, p, required))
        out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    asyncio.run(main())

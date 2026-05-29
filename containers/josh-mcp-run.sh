#!/usr/bin/env bash
# ForeverTree simulation entrypoint — josh-mcp arm.
#
# The scorer invokes ./run.sh under N_REPLICATES=100. For the
# constrained josh-mcp arm, ./run.sh execs the harness-supplied
# generic MCP runner (/sandbox/runner.py — do not modify), which
# reads agent-authored /sandbox/mcp_calls.json and forwards every
# entry to the `josh mcp` server via the Python MCP client.
#
# The agent's deliverable for this arm is the `.josh` source +
# `mcp_calls.json`; runner.py is the runtime that ties them
# together. See prompts/targets/josh-mcp.md.

set -euo pipefail
cd "$(dirname "$0")"
N_REPLICATES="${N_REPLICATES:-2}"
exec python /sandbox/runner.py

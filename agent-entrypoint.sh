#!/usr/bin/env bash
# Agent-mode entrypoint. Lives only in `fortree:agent` (added by the agent
# Dockerfile stage). The scorer image does not carry this file.
#
# Invocation (from orchestration/run_agent.sh):
#   docker run --rm --env-file .env \
#     -v <RUN_DIR>/workspace:/sandbox \
#     -v <RUN_DIR>/.opencode:/root/.config/opencode \
#     -v <RUN_DIR>/prompt.md:/opt/prompt.md:ro \
#     -v <RUN_DIR>:/opt/agent_meta \
#     fortree:agent /opt/agent-entrypoint.sh
#
# Sequence:
#   1. opencode run — streams JSON events to stdout, captured by the
#      caller into trajectory.jsonl; stderr → agent_stderr.log.
#   2. opencode export — dumps the just-finished session as a single JSON
#      blob to /opt/agent_meta/session_export.json. With no session ID
#      argument the latest session is used, which in this fresh --rm
#      container is the one we just ran. TUI noise is on stderr; we
#      redirect that to /dev/null so the file contains JSON only.
#
# Exit code reflects opencode run's status (the export is best-effort).
set -uo pipefail

opencode run --dir /sandbox --agent coder --format json --print-logs \
  "$(cat /opt/prompt.md)"
RUN_EXIT=$?

if [ -d /opt/agent_meta ]; then
  opencode export 2>/dev/null > /opt/agent_meta/session_export.json || true
fi

exit "$RUN_EXIT"

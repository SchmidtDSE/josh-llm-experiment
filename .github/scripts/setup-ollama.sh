#!/usr/bin/env bash
# Bring up an Ollama service container, wait for it to accept requests, pull
# the requested model, and write an .env file that the agent container can
# use to reach ollama from inside the default docker bridge network.
#
# Args:
#   $1   ollama image reference (pinned, with sha256 digest)
#   $2   model short-name (matches a config/models.yaml ollama-* entry)
#
# Output side-effects:
#   - Starts a container named `ollama` (host port 11434 published).
#   - Writes ./.env with OLLAMA_HOST set to the docker bridge gateway.
#
# Maps the project's ollama-* short names to their underlying ollama
# model tags. Keep in sync with config/models.yaml.
set -euo pipefail

OLLAMA_IMAGE="${1:?ollama image reference required as $1}"
MODEL_SHORTNAME="${2:?model short-name required as $2}"

case "$MODEL_SHORTNAME" in
  ollama-qwen-coder-7b)   OLLAMA_MODEL_TAG="qwen2.5-coder:7b"   ;;
  ollama-qwen-coder-1_5b) OLLAMA_MODEL_TAG="qwen2.5-coder:1.5b" ;;
  *) echo "Unknown ollama-* short name: $MODEL_SHORTNAME" >&2; exit 2 ;;
esac

echo "▶ Starting ollama ($OLLAMA_IMAGE)"
docker run -d --name ollama -p 11434:11434 \
  -v ollama-data:/root/.ollama \
  "$OLLAMA_IMAGE"

echo "▶ Waiting for ollama to accept requests"
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:11434/api/tags > /dev/null; then
    echo "  ollama up"
    break
  fi
  sleep 2
done

echo "▶ Pulling $OLLAMA_MODEL_TAG"
docker exec ollama ollama pull "$OLLAMA_MODEL_TAG"
docker exec ollama ollama list

echo "▶ Writing .env (OLLAMA_HOST = docker bridge gateway)"
# The agent container runs on the default bridge; reach the host's
# published 11434 via the bridge gateway IP (typically 172.17.0.1).
BRIDGE_GW=$(docker network inspect bridge \
  --format '{{(index .IPAM.Config 0).Gateway}}')
echo "  bridge gateway: $BRIDGE_GW"
cat > .env <<ENV
OPENROUTER_API_KEY=unused-for-ollama-path
OLLAMA_HOST=http://${BRIDGE_GW}:11434
ENV

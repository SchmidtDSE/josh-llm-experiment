#!/usr/bin/env bash
# Prepare the inference provider for an integration run by writing
# ./.env with whatever credentials/host the agent needs.
# Dispatches on the MODEL short-name:
#
#   - ollama-*  → start an ollama service container, pull the matching
#                 model, set OLLAMA_HOST=http://host.docker.internal:11434
#                 so the agent (joined to the dnsmasq sidecar's netns)
#                 can reach the host-published port.
#   - anything else  → write OPENROUTER_API_KEY from the env so opencode
#                      talks to OpenRouter. Workflow exposes the secret
#                      via `env: OPENROUTER_API_KEY: ${{ secrets... }}`.
#
# Args:
#   $1   ollama image reference (only used on the ollama branch; ok to
#        pass an empty string for the openrouter branch).
#   $2   MODEL short-name (matches a config/models.yaml entry).
set -euo pipefail

OLLAMA_IMAGE="${1:-}"
MODEL_SHORTNAME="${2:?model short-name required as $2}"

write_env_openrouter() {
  if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "setup-provider: OPENROUTER_API_KEY not set in env; cannot run model '$MODEL_SHORTNAME'." >&2
    echo "  Workflow must export it via env: OPENROUTER_API_KEY: \${{ secrets.OPENROUTER_API_KEY }}" >&2
    exit 1
  fi
  echo "▶ Writing .env (OPENROUTER_API_KEY from workflow secret)"
  cat > .env <<ENV
OPENROUTER_API_KEY=$OPENROUTER_API_KEY
OLLAMA_HOST=http://unused.invalid
ENV
}

setup_ollama() {
  local model_tag
  case "$MODEL_SHORTNAME" in
    ollama-qwen-coder-7b)   model_tag="qwen2.5-coder:7b"   ;;
    ollama-qwen-coder-1_5b) model_tag="qwen2.5-coder:1.5b" ;;
    *) echo "setup-provider: unknown ollama-* short name: $MODEL_SHORTNAME" >&2; exit 2 ;;
  esac

  if [ -z "$OLLAMA_IMAGE" ]; then
    echo "setup-provider: \$1 (ollama image ref) required on the ollama branch" >&2
    exit 2
  fi

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

  echo "▶ Pulling $model_tag"
  docker exec ollama ollama pull "$model_tag"
  docker exec ollama ollama list

  echo "▶ Writing .env (OLLAMA_HOST = host.docker.internal)"
  # run_agent.sh joins the sidecar's netns and docker propagates the
  # sidecar's /etc/hosts (which has host.docker.internal:host-gateway
  # via --add-host) into the agent. So this hostname resolves to the
  # docker host, where ollama publishes :11434.
  cat > .env <<ENV
OPENROUTER_API_KEY=unused-for-ollama-path
OLLAMA_HOST=http://host.docker.internal:11434
ENV
}

case "$MODEL_SHORTNAME" in
  ollama-*) setup_ollama ;;
  *)        write_env_openrouter ;;
esac

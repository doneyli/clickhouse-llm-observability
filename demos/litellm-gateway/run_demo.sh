#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Error: ANTHROPIC_API_KEY is not set. Add it to .env, then rerun." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is not running." >&2
  exit 1
fi

# Respect DEPLOY_MODE before assuming a local Langfuse: in cloud mode the health
# check must target the cloud/remote host, not localhost.
DEPLOY_MODE="${DEPLOY_MODE:-self-hosted}"
if [ "$DEPLOY_MODE" = "cloud" ]; then
  LANGFUSE_URL="${LANGFUSE_BASE_URL:-${LANGFUSE_HOST:-https://cloud.langfuse.com}}"
else
  LANGFUSE_URL="${LANGFUSE_BASE_URL:-${LANGFUSE_HOST:-http://localhost:3001}}"
fi
if ! curl -sf --max-time 5 "$LANGFUSE_URL/api/public/health" >/dev/null; then
  echo "Error: Langfuse is not reachable at $LANGFUSE_URL." >&2
  echo "Run ./setup.sh first, then rerun this script." >&2
  exit 1
fi

docker compose --profile demo up -d litellm-proxy

LITELLM_PORT="${LITELLM_PORT:-4000}"
export LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"
for _ in $(seq 1 60); do
  if curl -sf --max-time 2 "$LITELLM_BASE_URL/health/liveliness" >/dev/null; then
    exec python3 demos/litellm-gateway/client.py "$@"
  fi
  sleep 1
done

echo "Error: LiteLLM did not become ready at $LITELLM_BASE_URL." >&2
docker compose --profile demo logs --tail 50 litellm-proxy >&2
exit 1

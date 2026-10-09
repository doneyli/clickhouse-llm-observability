#!/usr/bin/env bash
# Run the SDK-instrumented half of the comparison.
#
# Runs inside the pinned LiteLLM image so the litellm + OTEL dependencies are
# already present — no local venv or pip install. The proxy is NOT involved:
# this process calls Anthropic directly and exports its own spans to Langfuse.
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

# Same project as the proxy path, so both appear in one trace list. Prefer the
# gateway-specific keys and fall back to the stack-wide ones, exactly like
# client.py does.
PUBLIC_KEY="${LITELLM_LANGFUSE_PUBLIC_KEY:-${LANGFUSE_PUBLIC_KEY:-}}"
SECRET_KEY="${LITELLM_LANGFUSE_SECRET_KEY:-${LANGFUSE_SECRET_KEY:-}}"
# Container-side URL: must be reachable from inside the container, so it uses the
# same var the proxy service uses, not the host-side LITELLM_LANGFUSE_BASE_URL.
OTEL_HOST="${LITELLM_LANGFUSE_INTERNAL_URL:-${LANGFUSE_INTERNAL_URL:-http://langfuse-web:3000}}"

if [ -z "$PUBLIC_KEY" ] || [ -z "$SECRET_KEY" ]; then
  echo "Error: no Langfuse keys found (LITELLM_LANGFUSE_* or LANGFUSE_*)." >&2
  exit 1
fi

# Join the Compose network only when exporting to a self-hosted Langfuse, which
# is reachable by container name; a cloud host needs plain outbound internet.
NETWORK_ARGS=()
case "$OTEL_HOST" in
  *localhost*|*127.0.0.1*)
    echo "Error: LITELLM_LANGFUSE_INTERNAL_URL is $OTEL_HOST — that is the" >&2
    echo "container's own localhost, not Langfuse. Use the service name" >&2
    echo "(http://langfuse-web:3000) or the cloud URL." >&2
    exit 1
    ;;
  http://langfuse-web*|http://langfuse*)
    NETWORK=$(docker network ls --format '{{.Name}}' | grep -E 'langfuse|clickhouse-llm' | head -1)
    if [ -n "$NETWORK" ]; then
      NETWORK_ARGS=(--network "$NETWORK")
    fi
    ;;
esac

# Prefer running inside the already-running proxy container. Spawning a SECOND
# full LiteLLM image costs ~900MB, which OOM-kills the run on a Docker host that
# is already near its memory ceiling (the failure is silent: the container dies
# with status 137 and prints nothing). Reusing the running container adds only a
# Python process. The proxy is still NOT involved in the call path — this is a
# separate process that imports the litellm SDK and calls Anthropic directly.
if [ -z "${SDK_DEMO_FORCE_NEW_CONTAINER:-}" ] \
   && [ "$(docker inspect -f '{{.State.Running}}' litellm-proxy 2>/dev/null)" = "true" ]; then
  docker cp "$ROOT_DIR/demos/litellm-gateway/sdk_client.py" \
    litellm-proxy:/tmp/sdk_client.py >/dev/null
  exec docker exec \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    -e LANGFUSE_PUBLIC_KEY="$PUBLIC_KEY" \
    -e LANGFUSE_SECRET_KEY="$SECRET_KEY" \
    -e LANGFUSE_OTEL_HOST="$OTEL_HOST" \
    -e LITELLM_UPSTREAM_MODEL="${LITELLM_UPSTREAM_MODEL:-anthropic/claude-sonnet-4-6}" \
    -e OTEL_SERVICE_NAME=litellm-sdk-demo \
    -e SDK_DEMO_SETTLE_SECONDS="${SDK_DEMO_SETTLE_SECONDS:-6}" \
    litellm-proxy python /tmp/sdk_client.py "$@"
fi

# Fallback: no proxy container running, so start a throwaway one.
# The +"..." form is required: under `set -u`, macOS's bash 3.2 treats an empty
# array expansion as an unbound variable and aborts.
exec docker run --rm \
  ${NETWORK_ARGS[@]+"${NETWORK_ARGS[@]}"} \
  -e SDK_DEMO_SETTLE_SECONDS="${SDK_DEMO_SETTLE_SECONDS:-6}" \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e LANGFUSE_PUBLIC_KEY="$PUBLIC_KEY" \
  -e LANGFUSE_SECRET_KEY="$SECRET_KEY" \
  -e LANGFUSE_OTEL_HOST="$OTEL_HOST" \
  -e LITELLM_UPSTREAM_MODEL="${LITELLM_UPSTREAM_MODEL:-anthropic/claude-sonnet-4-6}" \
  -e OTEL_SERVICE_NAME=litellm-sdk-demo \
  -v "$ROOT_DIR/demos/litellm-gateway/sdk_client.py:/app/sdk_client.py:ro" \
  --entrypoint python \
  ghcr.io/berriai/litellm:v1.93.0 \
  /app/sdk_client.py "$@"

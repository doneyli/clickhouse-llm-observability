#!/usr/bin/env bash
# Run the gateway demo, which is also its readiness check: one PASS / FAIL line
# per check, then READY or NOT READY. Exits 1 when a required check fails.
#   ./demos/litellm-gateway/run_demo.sh [prompt] [--links] [--skip-trace-check]
# The preflight checks are here; client.py makes the gateway call and reads the
# trace back from Langfuse, and prints the closing verdict.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

# The root .env is the only source of LANGFUSE_*: a shell-exported key for
# another project would make the read-back look in a different project from the
# one the proxy exports to (the container reads .env via env_file).
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PREFLIGHT_PASSED=0
pass() {
  echo "  PASS  $1"
  PREFLIGHT_PASSED=$((PREFLIGHT_PASSED + 1))
}
fail() {
  echo "  FAIL  $1  ->  $2"
  echo
  echo "NOT READY ($PREFLIGHT_PASSED/$((PREFLIGHT_PASSED + 1)) checks passed, 1 required failed)"
  exit 1
}

echo "litellm-gateway readiness"
echo "Preflight"

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  fail "ANTHROPIC_API_KEY is not set" "add it to .env, then rerun"
fi
pass "ANTHROPIC_API_KEY set"

if ! docker info >/dev/null 2>&1; then
  fail "Docker is not running" "start Docker, then rerun"
fi
pass "Docker running"

# Respect DEPLOY_MODE before assuming a local Langfuse: in cloud mode the health
# check must target the cloud/remote host, not localhost.
DEPLOY_MODE="${DEPLOY_MODE:-self-hosted}"
if [ "$DEPLOY_MODE" = "cloud" ]; then
  LANGFUSE_URL="${LANGFUSE_BASE_URL:-${LANGFUSE_HOST:-https://cloud.langfuse.com}}"
else
  LANGFUSE_URL="${LANGFUSE_BASE_URL:-${LANGFUSE_HOST:-http://localhost:3001}}"
fi
if ! curl -sf --max-time 5 "$LANGFUSE_URL/api/public/health" >/dev/null; then
  fail "Langfuse is not reachable at $LANGFUSE_URL" "run ./setup.sh first, then rerun"
fi
pass "Langfuse reachable at $LANGFUSE_URL ($DEPLOY_MODE)"

docker compose --profile demo up -d litellm-proxy

LITELLM_PORT="${LITELLM_PORT:-4000}"
export LITELLM_BASE_URL="${LITELLM_BASE_URL:-http://localhost:$LITELLM_PORT}"
for _ in $(seq 1 60); do
  if curl -sf --max-time 2 "$LITELLM_BASE_URL/health/liveliness" >/dev/null; then
    pass "LiteLLM proxy live at $LITELLM_BASE_URL"
    export GATEWAY_PREFLIGHT_PASSED="$PREFLIGHT_PASSED"
    exec python3 demos/litellm-gateway/client.py "$@"
  fi
  sleep 1
done

docker compose --profile demo logs --tail 50 litellm-proxy >&2
fail "LiteLLM did not become ready at $LITELLM_BASE_URL within 60s" \
  "read the proxy logs above; a first boot with a database runs migrations for 30-60s"

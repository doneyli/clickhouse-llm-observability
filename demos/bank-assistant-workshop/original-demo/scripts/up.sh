#!/usr/bin/env bash
# Start local services: n8n + APM stand-in (default), or add the self-hosted
# Langfuse EE stack with --selfhosted.
# The Enterprise license key is read at runtime from the repo-root .env, so it
# is never copied into this demo's files.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL  # shell keys must not leak in
MAIN_CHECKOUT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
LICENSE_ENV="${NORTHWIND_LICENSE_ENV_FILE:-$MAIN_CHECKOUT/.env}"
if [[ -z "${LANGFUSE_EE_LICENSE_KEY:-}" && -f "$LICENSE_ENV" ]]; then
  LANGFUSE_EE_LICENSE_KEY="$(grep -E '^LANGFUSE_EE_LICENSE_KEY=' "$LICENSE_ENV" | head -1 | cut -d= -f2- | tr -d '"')"
  export LANGFUSE_EE_LICENSE_KEY
fi
[[ -n "${LANGFUSE_EE_LICENSE_KEY:-}" ]] && echo "✓ Enterprise license key found" || echo "! No EE license key — running as OSS"
if [[ "${1:-}" == "--selfhosted" ]]; then shift; docker compose --profile selfhosted up -d "$@"; else
  docker compose up -d n8n jaeger "$@"; echo "✓ n8n http://localhost:5678 · Jaeger http://localhost:16686 (Langfuse: Cloud project in .env.cloud)"; exit 0; fi
echo "Waiting for Langfuse (http://localhost:3100) ..."
for i in $(seq 1 90); do
  curl -sf http://localhost:3100/api/public/health >/dev/null && { echo "✓ Langfuse healthy"; break; }
  sleep 2
done
curl -sf http://localhost:3100/api/public/ready >/dev/null && echo "✓ Langfuse ready" || echo "! /api/public/ready not OK yet"

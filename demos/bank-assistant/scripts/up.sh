#!/usr/bin/env bash
# Start local services: n8n + infrastructure observability (Jaeger). With --selfhosted, also
# make sure the ONE local Langfuse shared by every standalone demo is up and that this demo
# has a project there (.env.selfhosted) — scripts/langfuse_local.py reuses a healthy v4
# instance or starts the shared one, reading the Enterprise license from the repo-root .env.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL  # shell keys must not leak in
if [[ "${1:-}" == "--selfhosted" ]]; then
  shift
  python3 ../../scripts/langfuse_local.py provision bank-assistant
fi
docker compose up -d n8n jaeger "$@"
echo "✓ n8n http://localhost:5678 · Jaeger http://localhost:16686"
for f in .env.cloud .env.selfhosted; do
  [[ -f "$f" ]] && echo "  Langfuse ($f): $(grep -E '^LANGFUSE_(BASE_URL|HOST)=' "$f" | head -1 | cut -d= -f2-)"
done
exit 0

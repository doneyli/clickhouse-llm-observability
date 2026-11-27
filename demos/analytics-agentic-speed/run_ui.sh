#!/usr/bin/env bash
# Live view of the Analytics at Agentic Speed demo: http://localhost:${PORT:-8099}
set -euo pipefail
cd "$(dirname "$0")"
# The demo's .env must win over shell-exported Langfuse keys (see the root CLAUDE.md).
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
exec .venv/bin/python -m uvicorn webapp.server:app --host 127.0.0.1 --port "${PORT:-8099}"

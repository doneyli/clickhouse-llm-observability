#!/usr/bin/env bash
# Seed the LOCAL self-hosted Enterprise instance — the one Langfuse on this machine shared
# by every standalone demo (scripts/langfuse_local.py) — with the same demo state as Cloud,
# plus Enterprise-only state: RBAC users, project-level role override, protected
# production label, audit-log activity.
#   ./scripts/bootstrap_selfhosted.sh          (idempotent)
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL  # shell keys must not leak in
# Reuses a healthy local Langfuse v4 (or starts the shared one) and writes .env.selfhosted.
python3 ../../scripts/langfuse_local.py provision bank-assistant
export NORTHWIND_PROFILE=selfhosted NORTHWIND_MCP_URL=http://localhost:8766/mcp NORTHWIND_MCP_PORT=8766
PY=.venv/bin/python
mkdir -p logs
if ! lsof -i :8766 -sTCP:LISTEN >/dev/null 2>&1; then
  nohup $PY -m northwind.mcp_server > logs/mcp-selfhosted.log 2>&1 &
  sleep 3
fi
$PY scripts/seed_selfhosted_rbac.py
$PY scripts/seed_prompts.py
$PY scripts/seed_evals.py
$PY scripts/seed_compliance_dashboard.py
$PY scripts/seed_datasets.py
$PY scripts/generate_traffic.py --scenario all --n 14
# audit-log activity: a promotion and a rollback of the protected label
$PY scripts/prompt_label.py --promote staging
$PY scripts/prompt_label.py --rollback
URL="$(grep -E '^LANGFUSE_BASE_URL=' .env.selfhosted | cut -d= -f2-)"
echo "Self-hosted ready: $URL  (Settings → Members, Audit logs, Data retention; Prompts → protected label)"

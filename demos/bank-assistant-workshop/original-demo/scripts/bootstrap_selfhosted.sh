#!/usr/bin/env bash
# Seed the LOCAL self-hosted Enterprise instance (http://localhost:3100) with the
# same demo state as Cloud, plus Enterprise-only state: RBAC users, project-level
# role override, protected production label, audit-log activity.
set -euo pipefail
cd "$(dirname "$0")/.."
export NORTHWIND_PROFILE=selfhosted NORTHWIND_MCP_URL=http://localhost:8766/mcp NORTHWIND_MCP_PORT=8766
PY=.venv/bin/python
if ! lsof -i :8766 -sTCP:LISTEN >/dev/null 2>&1; then
  nohup $PY -m northwind.mcp_server > logs/mcp-selfhosted.log 2>&1 &
  sleep 3
fi
$PY scripts/seed_prompts.py
$PY scripts/seed_evals.py
$PY scripts/seed_datasets.py
$PY scripts/seed_selfhosted_rbac.py
$PY scripts/generate_traffic.py --scenario all --n 14
# audit-log activity: a promotion and a rollback of the protected label
$PY scripts/prompt_label.py --promote staging
$PY scripts/prompt_label.py --rollback
echo "Self-hosted ready: http://localhost:3100  (Settings → Members, Audit logs, Data retention; Prompts → protected label)"

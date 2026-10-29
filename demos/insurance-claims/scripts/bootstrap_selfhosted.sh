#!/usr/bin/env bash
# Seed the LOCAL self-hosted Langfuse ENTERPRISE instance — the one Langfuse on this machine shared by every
# standalone demo (scripts/langfuse_local.py) — with the claims demo state: the same prompts, judges, datasets
# and dashboards as Cloud, a traffic batch, plus Enterprise-only governance — projects per environment/team,
# RBAC users, protected `production` label, retention, and audit-log activity (a promotion and a rollback).
#   ./scripts/bootstrap_selfhosted.sh          (idempotent)
# A newly started shared instance reads the license key from the repo-root .env, never from this demo.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL   # shell keys must not leak in
# Reuses a healthy local Langfuse v4 (or starts the shared one) and writes .env.selfhosted.
python3 ../../scripts/langfuse_local.py provision insurance-claims
set -a; . ./.env; set +a
PY=.venv/bin/python
export CLAIMS_PROFILE=selfhosted CLAIMS_MCP_PORT=8768 DOC_INTAKE_PORT=9211
export CLAIMS_MCP_URL=http://localhost:8768/mcp DOC_INTAKE_URL=http://localhost:9211
mkdir -p logs
lsof -tiTCP:8768 -sTCP:LISTEN >/dev/null 2>&1 || { nohup $PY -m claims.mcp_server > logs/mcp-selfhosted.log 2>&1 & disown; }
lsof -tiTCP:9211 -sTCP:LISTEN >/dev/null 2>&1 || { nohup doc_intake/.venv/bin/python -m doc_intake.service > logs/doc-intake-selfhosted.log 2>&1 & disown; }
sleep 4
$PY scripts/seed_selfhosted_rbac.py
$PY scripts/seed_selfhosted_annuities.py
$PY scripts/seed_prompts.py
$PY scripts/seed_evals.py
$PY scripts/seed_datasets.py
$PY scripts/seed_dashboards.py
$PY -u scripts/generate_traffic.py --scenario baseline --limit 16 --concurrency 4
$PY -u scripts/generate_traffic.py --scenario canary --limit 4 --concurrency 4
# audit-log activity on the protected label: a promotion of the candidate, then the rollback
$PY scripts/prompt_label.py --promote staging || true
$PY scripts/prompt_label.py --rollback || true
URL="$(grep -E '^LANGFUSE_BASE_URL=' .env.selfhosted | cut -d= -f2-)"
echo "Self-hosted ready: $URL — sign in as ${LANGFUSE_INIT_USER_EMAIL:-platform.admin@northwind-life.example} (password in .env)"
echo "  Settings → Members (project-level roles), Audit logs, Data retention · Prompts → protected 'production' label"

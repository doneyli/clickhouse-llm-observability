#!/usr/bin/env bash
# Bring up the LOCAL self-hosted Langfuse ENTERPRISE instance (http://localhost:3200) with the claims demo
# state: the same prompts, judges, datasets and dashboards as Cloud, a traffic batch, plus Enterprise-only
# governance — projects per environment/team, RBAC users, protected `production` label, retention, and
# audit-log activity (a promotion and a rollback).
#   ./scripts/bootstrap_selfhosted.sh          (idempotent)
# The license key is read at runtime from the repo-root .env, never copied into this demo.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL   # shell keys must not leak in
MAIN="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
LANGFUSE_EE_LICENSE_KEY="${LANGFUSE_EE_LICENSE_KEY:-$(grep -E '^LANGFUSE_EE_LICENSE_KEY=' "$MAIN/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')}"
export LANGFUSE_EE_LICENSE_KEY
[ -n "$LANGFUSE_EE_LICENSE_KEY" ] && echo "✓ Enterprise license key found" || echo "! no license key — Enterprise features stay off"
docker compose --profile selfhosted up -d
for i in $(seq 1 90); do curl -sf http://localhost:3200/api/public/health >/dev/null && break; sleep 2; done
curl -sf http://localhost:3200/api/public/health >/dev/null && echo "✓ Langfuse healthy on :3200"
set -a; . ./.env; set +a
cat > .env.selfhosted <<EOT
# Local self-hosted Enterprise instance — written by scripts/bootstrap_selfhosted.sh
LANGFUSE_BASE_URL=http://localhost:3200
LANGFUSE_PROJECT_ID=claims-prod
LANGFUSE_PUBLIC_KEY=$SELFHOSTED_PUBLIC_KEY
LANGFUSE_SECRET_KEY=$SELFHOSTED_SECRET_KEY
EOT
PY=.venv/bin/python
export CLAIMS_PROFILE=selfhosted CLAIMS_MCP_PORT=8768 DOC_INTAKE_PORT=9211
export CLAIMS_MCP_URL=http://localhost:8768/mcp DOC_INTAKE_URL=http://localhost:9211
mkdir -p logs
lsof -tiTCP:8768 -sTCP:LISTEN >/dev/null 2>&1 || { nohup $PY -m claims.mcp_server > logs/mcp-selfhosted.log 2>&1 & disown; }
lsof -tiTCP:9211 -sTCP:LISTEN >/dev/null 2>&1 || { nohup doc_intake/.venv/bin/python -m doc_intake.service > logs/doc-intake-selfhosted.log 2>&1 & disown; }
sleep 4
$PY scripts/seed_selfhosted_rbac.py
$PY scripts/seed_prompts.py
$PY scripts/seed_evals.py
$PY scripts/seed_datasets.py
$PY scripts/seed_dashboards.py
$PY -u scripts/generate_traffic.py --scenario baseline --limit 16 --concurrency 4
$PY -u scripts/generate_traffic.py --scenario canary --limit 4 --concurrency 4
# audit-log activity on the protected label: a promotion of the candidate, then the rollback
$PY scripts/prompt_label.py --promote staging || true
$PY scripts/prompt_label.py --rollback || true
echo "Self-hosted ready: http://localhost:3200 — sign in as $LANGFUSE_INIT_USER_EMAIL (password in .env)"
echo "  Settings → Members (project-level roles), Audit logs, Data retention · Prompts → protected 'production' label"

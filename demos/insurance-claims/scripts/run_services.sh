#!/usr/bin/env bash
# Start (or restart) the demo's services, detached, logs in logs/:
#   policy-admin MCP server :8767 (app venv)   doc-intake A2A service :9210 (Strands venv)
#   the same pair for the self-hosted Enterprise target on :8768 / :9211
#   examiner workbench portal :8095 (app venv, if portal/server.py exists)
# Processes do NOT hot-reload: restart after every code change.
#   ./scripts/run_services.sh          start/restart all
#   ./scripts/run_services.sh stop     stop all
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
stop_port() { local pids; pids=$(lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null || true); [ -n "$pids" ] && kill $pids 2>/dev/null || true; }
for p in 8767 9210 8095 8768 9211 8769; do stop_port $p; done
[ "${1:-}" = "stop" ] && { echo "stopped"; exit 0; }
sleep 1
# Services for the self-hosted Enterprise target run as a second set (their spans export to :3200).
CLAIMS_PROFILE=selfhosted CLAIMS_MCP_PORT=8768 nohup .venv/bin/python -m claims.mcp_server > logs/mcp-selfhosted.log 2>&1 & disown
CLAIMS_PROFILE=selfhosted DOC_INTAKE_PORT=9211 nohup doc_intake/.venv/bin/python -m doc_intake.service > logs/doc-intake-selfhosted.log 2>&1 & disown
# The retirement / annuities project on the self-hosted instance has its own keys, so its own MCP server (:8769).
[ -f .env.selfhosted-annuities ] && { CLAIMS_PROFILE=selfhosted-annuities CLAIMS_MCP_PORT=8769 nohup .venv/bin/python -m claims.mcp_server > logs/mcp-annuities.log 2>&1 & disown; }
nohup .venv/bin/python -m claims.mcp_server > logs/mcp.log 2>&1 & disown
nohup doc_intake/.venv/bin/python -m doc_intake.service > logs/doc-intake.log 2>&1 & disown
if [ -f portal/server.py ]; then
  nohup .venv/bin/python -m uvicorn portal.server:app --host 127.0.0.1 --port 8095 > logs/portal.log 2>&1 & disown
fi
for i in $(seq 1 30); do
  up=0
  curl -s -o /dev/null -m 2 http://127.0.0.1:9210/.well-known/agent-card.json && up=$((up+1))
  curl -s -o /dev/null -m 2 -X POST http://127.0.0.1:8767/mcp && up=$((up+1))
  [ "$up" = 2 ] && break; sleep 1
done
echo "policy-admin MCP  http://127.0.0.1:8767/mcp   (logs/mcp.log)"
echo "doc-intake A2A    http://127.0.0.1:9210/      (logs/doc-intake.log)"
echo "self-hosted set   MCP :8768, A2A :9211        (CLAIMS_PROFILE=selfhosted CLAIMS_MCP_URL=http://localhost:8768/mcp DOC_INTAKE_URL=http://localhost:9211)"
[ -f portal/server.py ] && echo "workbench portal  http://127.0.0.1:8095/      (logs/portal.log)"
exit 0

#!/usr/bin/env bash
# Start the Northwind Bank presenter portal on http://localhost:8090
#
#   scripts/run_portal.sh            start (or restart) in the background — survives closing the terminal
#   scripts/run_portal.sh --fg       run in the foreground (Ctrl-C to stop)
#   scripts/run_portal.sh --stop     stop the portal (leaves the MCP server running)
#   scripts/run_portal.sh --status   show what is running
#
# Also makes sure the core-banking MCP server is up on :8765 (logs/mcp.log).
# Portal log: logs/portal.log
set -euo pipefail
cd "$(dirname "$0")/.."
DEMO_DIR="$(pwd)"
PY="$DEMO_DIR/.venv/bin/python"
PORT="${PORTAL_PORT:-8090}"
MCP_PORT=8765
mkdir -p logs

# Shell-exported Langfuse keys must never leak into the demo processes.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL

[[ -x "$PY" ]] || { echo "✗ $PY not found — create the venv first (python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt)"; exit 1; }

listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -1; }

stop_portal() {
  local pids
  pids="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "• Stopping process on :$PORT (pid $(echo $pids | tr '\n' ' '))"
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 20); do [[ -z "$(listening "$PORT")" ]] && break; sleep 0.25; done
    [[ -n "$(listening "$PORT")" ]] && kill -9 $(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t) 2>/dev/null || true
  fi
}

case "${1:-}" in
  --stop) stop_portal; echo "✓ portal stopped"; exit 0 ;;
  --status)
    echo "portal :$PORT  → $( [[ -n "$(listening "$PORT")" ]] && echo "running (pid $(listening "$PORT"))" || echo "not running")"
    echo "MCP    :$MCP_PORT  → $( [[ -n "$(listening "$MCP_PORT")" ]] && echo "running (pid $(listening "$MCP_PORT"))" || echo "not running")"
    exit 0 ;;
esac

# ── 1. Core-banking MCP server ──────────────────────────────────────────────
if [[ -n "$(listening "$MCP_PORT")" ]]; then
  echo "✓ MCP server already running on :$MCP_PORT (pid $(listening "$MCP_PORT"))"
else
  echo "• Starting MCP server on :$MCP_PORT (logs/mcp.log)"
  nohup "$PY" -m northwind.mcp_server >> logs/mcp.log 2>&1 < /dev/null &
  disown $! 2>/dev/null || true
  for _ in $(seq 1 60); do [[ -n "$(listening "$MCP_PORT")" ]] && break; sleep 0.25; done
  if [[ -n "$(listening "$MCP_PORT")" ]]; then echo "✓ MCP server up"; else echo "✗ MCP server did not start — see logs/mcp.log"; tail -20 logs/mcp.log; exit 1; fi
fi

# ── 2. Which Langfuse are we talking to? (no secrets printed) ───────────────
"$PY" - <<'EOF'
import os
from northwind import config
label = os.environ.get("NORTHWIND_PROMPT_LABEL", "production")
print(f"✓ Langfuse target : profile={config.PROFILE}  {config.LANGFUSE_BASE_URL}")
print(f"  environment     : {config.ENVIRONMENT}   release: {config.RELEASE}")
print(f"  prompt label    : {label}   (northwind-assistant-system)")
print(f"  agent model     : {config.AGENT_MODEL}")
print(f"  keys present    : langfuse={'yes' if config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY else 'NO'}"
      f"  anthropic={'yes' if config.ANTHROPIC_API_KEY else 'NO'}  openai={'yes' if config.OPENAI_API_KEY else 'no'}")
EOF

# ── 3. Portal ───────────────────────────────────────────────────────────────
stop_portal
UVICORN=("$PY" -m uvicorn portal.server:app --host 127.0.0.1 --port "$PORT" --log-level warning --timeout-graceful-shutdown 3)

if [[ "${1:-}" == "--fg" ]]; then
  echo "• Portal → http://localhost:$PORT  (foreground, Ctrl-C to stop)"
  exec "${UVICORN[@]}"
fi

echo "• Starting portal on :$PORT (logs/portal.log)"
nohup "${UVICORN[@]}" >> logs/portal.log 2>&1 < /dev/null &
disown $! 2>/dev/null || true
for _ in $(seq 1 80); do
  curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 0.25
done
if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo ""
  echo "✓ Northwind Bank portal → http://localhost:$PORT"
  echo "  restart: scripts/run_portal.sh   stop: scripts/run_portal.sh --stop   log: tail -f logs/portal.log"
else
  echo "✗ Portal did not come up — last log lines:"; tail -30 logs/portal.log; exit 1
fi

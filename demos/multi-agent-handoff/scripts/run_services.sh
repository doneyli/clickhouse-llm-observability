#!/usr/bin/env bash
# Start / stop the A2A agent services (billing :9310, tech-support :9311).
#
#   ./scripts/run_services.sh          # start (idempotent), wait until each agent card answers
#   ./scripts/run_services.sh stop
#   ./scripts/run_services.sh status
#
# Fault F5 (slow billing): ./scripts/run_services.sh stop && BILLING_FAULT_LATENCY_MS=8000 ./scripts/run_services.sh
#
# Logs and pids go to .run/. Pass --stub to serve the deterministic stand-ins instead.
set -euo pipefail
cd "$(dirname "$0")/.."
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
mkdir -p .run
PY=.venv/bin/python
AGENTS=(billing tech-support)
port_of() { case "$1" in billing) echo "${BILLING_PORT:-9310}";; tech-support) echo "${TECH_SUPPORT_PORT:-9311}";; esac; }

stop() {
  for a in "${AGENTS[@]}"; do
    if [[ -f .run/$a.pid ]] && kill -0 "$(cat .run/$a.pid)" 2>/dev/null; then
      pid=$(cat ".run/$a.pid")
      kill "$pid"
      # wait for the port to be released, or a `stop && start` sees the dying process as "already up"
      for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid"
      echo "stopped $a"
    fi
    rm -f ".run/$a.pid"
  done
}

status() {
  for a in "${AGENTS[@]}"; do
    p=$(port_of "$a")
    if curl -sf "http://127.0.0.1:$p/.well-known/agent-card.json" >/dev/null; then echo "$a :$p up"; else echo "$a :$p down"; fi
  done
}

start() {
  for a in "${AGENTS[@]}"; do
    p=$(port_of "$a")
    if curl -sf "http://127.0.0.1:$p/.well-known/agent-card.json" >/dev/null; then echo "$a :$p already up"; continue; fi
    nohup "$PY" -m handoff.serve --agent "$a" "$@" >".run/$a.log" 2>&1 &
    echo $! >".run/$a.pid"
    disown || true
  done
  for a in "${AGENTS[@]}"; do
    p=$(port_of "$a")
    for _ in $(seq 1 50); do
      curl -sf "http://127.0.0.1:$p/.well-known/agent-card.json" >/dev/null && { echo "$a :$p up"; break; }
      sleep 0.2
    done
  done
}

case "${1:-start}" in
  stop) stop ;;
  status) status ;;
  start) shift || true; start "$@" ;;
  --stub) start --stub ;;
  *) echo "usage: $0 [start|stop|status] [--stub]"; exit 2 ;;
esac

#!/usr/bin/env bash
# Launch the Property Concierge portal (the show-able app).
#   ./run_portal.sh            # http://localhost:8080
#   PORT=9000 ./run_portal.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "No .venv found. Create it first:"
  echo "  python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  exit 1
fi

PORT="${PORT:-8080}"
echo "▶ Property Concierge portal:  http://localhost:${PORT}"
echo "  (each chat message = one traced agent run in the 'real-estate' Langfuse project)"
exec ./.venv/bin/python -m uvicorn webapp.server:app --host 0.0.0.0 --port "${PORT}"

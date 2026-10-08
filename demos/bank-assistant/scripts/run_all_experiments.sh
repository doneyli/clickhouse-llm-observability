#!/usr/bin/env bash
# Regenerate every experiment run the demo script shows.
#   --fresh   delete the existing runs on the golden / Spanish / red-team datasets first
# Stream A (English): prompt A/B, model comparison, CI gate on development
# Stream B (Spanish + security): Spanish A/B, Spanish gate, red-team
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
mkdir -p logs
if [[ "${1:-}" == "--fresh" ]]; then
  $PY - <<'PYEOF'
import sys; sys.path.insert(0, ".")
from urllib.parse import quote
from northwind import config
for ds in ["northwind-golden-qa-v1", "northwind-golden-qa-es-v1", "northwind-redteam-v1"]:
    for r in config.api("GET", f"/api/public/datasets/{quote(ds, safe='')}/runs", params={"limit": 100})["data"]:
        config.api("DELETE", f"/api/public/datasets/{quote(ds, safe='')}/runs/{quote(r['name'], safe='')}")
        print("deleted", ds, "·", r["name"])
PYEOF
fi
(
  $PY scripts/run_experiment.py --prompt-label production
  $PY scripts/run_experiment.py --prompt-label staging
  $PY scripts/run_experiment.py --prompt-label production --model gpt-4.1
  $PY scripts/prompt_gate.py --prompt-label development; echo "GATE en development exit=$?"
) > logs/experiments-en.log 2>&1 &
A=$!
(
  $PY scripts/run_experiment.py --dataset northwind-golden-qa-es-v1 --prompt-label production
  $PY scripts/run_experiment.py --dataset northwind-golden-qa-es-v1 --prompt-label staging
  $PY scripts/prompt_gate.py --prompt-label development --dataset northwind-golden-qa-es-v1; echo "GATE es development exit=$?"
  $PY scripts/run_experiment.py --dataset northwind-redteam-v1 --prompt-label production
) > logs/experiments-es.log 2>&1 &
B=$!
wait $A $B
echo "all experiments done — logs/experiments-en.log, logs/experiments-es.log"

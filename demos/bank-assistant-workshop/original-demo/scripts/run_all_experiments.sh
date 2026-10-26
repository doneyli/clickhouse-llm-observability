#!/usr/bin/env bash
# Regenerate every experiment run the demo script shows.
#   --fresh   delete the existing experiments on the golden / Spanish / red-team datasets
#             first. Langfuse v4 has no experiment-delete API, so this deletes each
#             experiment's TRACES — and with them their observations and scores. The
#             experiment itself then drops out of GET /experiments (it is derived from
#             its traces; verified for real-estate, docs/LANGFUSE_V4_MIGRATION_SPEC.md §15).
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
# GET/DELETE /datasets/{name}/runs are gone in v4 (404 on a v4 server; removed from
# Langfuse Cloud on 2026-11-16). Experiments API for the reads; DELETE /traces (at
# most 1,000 ids per request) for the deletion.
SINCE = "2026-01-01T00:00:00Z"  # the experiments API requires a start-time lower bound


def pages(path, **params):
    out, cur = [], None
    while True:
        d = config.api("GET", path, params={**params, "limit": 100, **({"cursor": cur} if cur else {})})
        out += d.get("data") or []
        cur = (d.get("meta") or {}).get("cursor")
        if not cur or not d.get("data"):
            return out


for ds in ["northwind-golden-qa-v1", "northwind-golden-qa-es-v1", "northwind-redteam-v1"]:
    dsid = config.api("GET", f"/api/public/v2/datasets/{quote(ds, safe='')}")["id"]
    for e in pages("/api/public/experiments", datasetId=dsid, fromStartTime=SINCE, fields="core"):
        tids = list(dict.fromkeys(i["traceId"] for i in pages(
            "/api/public/experiment-items", experimentId=e["id"], fromStartTime=SINCE, fields="core")))
        for n in range(0, len(tids), 1000):
            config.api("DELETE", "/api/public/traces", {"traceIds": tids[n:n + 1000]})
        print("deleted", ds, "·", e["name"], f"({len(tids)} traces)")
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

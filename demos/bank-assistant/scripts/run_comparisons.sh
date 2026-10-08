#!/usr/bin/env bash
# Re-create the comparison runs shown in the demo (prompt A/B, model comparison, red-team).
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
$PY scripts/run_experiment.py --prompt-label production
$PY scripts/run_experiment.py --prompt-label staging
$PY scripts/run_experiment.py --prompt-label production --model gpt-4.1
$PY scripts/run_experiment.py --dataset northwind-redteam-v1 --prompt-label production

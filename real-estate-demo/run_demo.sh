#!/usr/bin/env bash
# One-shot demo prep. Run this BEFORE presenting so Langfuse is full of data.
#
#   ./run_demo.sh                 # full prep (~10-15 min): traffic + both experiments
#   ./run_demo.sh --quick         # skip the experiments (traffic + evaluators only)
#   ./run_demo.sh --no-judge      # skip the custom SDK judges on live traffic (faster)
#   ./run_demo.sh --no-gpt        # skip the GPT comparison run
set -euo pipefail
cd "$(dirname "$0")"
PY=./.venv/bin/python

[ -d .venv ] || { echo "No .venv. Run: python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"; exit 1; }

QUICK=0; JUDGE_FLAG=""; GPT=1
for a in "$@"; do
  [ "$a" = "--quick" ] && QUICK=1
  [ "$a" = "--no-judge" ] && JUDGE_FLAG="--no-judge"
  [ "$a" = "--no-gpt" ] && GPT=0
done

echo "════════════════════════════════════════════════════════"
echo " Property Concierge — demo data prep"
echo "════════════════════════════════════════════════════════"

echo -e "\n[1/6] Seeding evaluation dataset (10 items)…"
$PY scripts/seed_dataset.py

echo -e "\n[2/6] Provisioning managed LLM-as-a-Judge evaluators (Anthropic)…"
./scripts/seed_managed_evaluators.sh || echo "  (managed evaluators step skipped — check Docker/Postgres)"

echo -e "\n[3/6] Generating live traffic (traces + code scores + a session)…"
$PY scripts/run_live_traffic.py $JUDGE_FLAG

echo -e "\n[4/6] Setting up the human annotation queue (+ items from live traffic)…"
$PY scripts/seed_annotation_queue.py

if [ "$QUICK" = "1" ]; then
  echo -e "\n[5/6] --quick: skipping experiments."
else
  echo -e "\n[5/6] Experiment run — agent on Claude…"
  $PY scripts/run_experiment.py --model claude-sonnet-4-6
  if [ "$GPT" = "1" ]; then
    echo -e "\n[6/6] Experiment run — agent on GPT-4o (for the compare-runs view)…"
    $PY scripts/run_experiment.py --model gpt-4o || echo "  (GPT run skipped — is OPEN_AI_API_KEY set in .env?)"
  else
    echo -e "\n[6/6] --no-gpt: skipping the GPT comparison run."
  fi
fi

echo -e "\n✓ Done. Open Langfuse (http://localhost:3001) → project 'real-estate'."
echo "  Evaluators, Datasets > Runs (compare Claude vs gpt-4o), Annotation Queues, Tracing."
echo "  Then start the portal:  ./run_portal.sh   → http://localhost:8080"

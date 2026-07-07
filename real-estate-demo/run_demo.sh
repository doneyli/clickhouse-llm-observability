#!/usr/bin/env bash
# One-shot demo prep. Run this BEFORE presenting so Langfuse is full of data.
#
#   ./run_demo.sh                  # full prep (~10-15 min): traffic + both experiments
#   ./run_demo.sh --quick          # skip the experiments (traffic + evaluators only)
#   ./run_demo.sh --no-judge       # skip the custom SDK judges on live traffic (faster)
#   ./run_demo.sh --no-gpt         # skip the GPT comparison run
#   ./run_demo.sh --prompt-variant # ALSO run the candidate prompt (closes the loop:
#                                  #   production vs candidate compare, same model)
set -euo pipefail
cd "$(dirname "$0")"
PY=./.venv/bin/python

[ -d .venv ] || { echo "No .venv. Run: python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"; exit 1; }

QUICK=0; JUDGE_FLAG=""; GPT=1; PROMPT_VARIANT=0
for a in "$@"; do
  [ "$a" = "--quick" ] && QUICK=1
  [ "$a" = "--no-judge" ] && JUDGE_FLAG="--no-judge"
  [ "$a" = "--no-gpt" ] && GPT=0
  [ "$a" = "--prompt-variant" ] && PROMPT_VARIANT=1
done

echo "════════════════════════════════════════════════════════"
echo " Property Concierge — demo data prep"
echo "════════════════════════════════════════════════════════"

echo -e "\n[1/7] Seeding prompts into Langfuse Prompt Management (production + candidate)…"
$PY scripts/seed_prompts.py

echo -e "\n[2/7] Seeding evaluation dataset (10 items)…"
$PY scripts/seed_dataset.py

echo -e "\n[3/7] Provisioning managed LLM-as-a-Judge evaluators (Anthropic)…"
./scripts/seed_managed_evaluators.sh || echo "  (managed evaluators step skipped — check Docker/Postgres)"

echo -e "\n[4/7] Generating live traffic (traces + code scores + a session)…"
$PY scripts/run_live_traffic.py $JUDGE_FLAG

echo -e "\n[5/7] Setting up the human annotation queue (+ items from live traffic)…"
$PY scripts/seed_annotation_queue.py

if [ "$QUICK" = "1" ]; then
  echo -e "\n[6/7] --quick: skipping experiments."
else
  echo -e "\n[6/7] Experiment run — agent on Claude (production prompt)…"
  $PY scripts/run_experiment.py --model claude-sonnet-4-6
  if [ "$GPT" = "1" ]; then
    echo -e "\n[7/7] Experiment run — agent on GPT-4o (for the compare-runs view)…"
    $PY scripts/run_experiment.py --model gpt-4o || echo "  (GPT run skipped — is OPEN_AI_API_KEY set in .env?)"
  else
    echo -e "\n[7/7] --no-gpt: skipping the GPT comparison run."
  fi
  if [ "$PROMPT_VARIANT" = "1" ]; then
    echo -e "\n[+]   Experiment run — CANDIDATE prompt on Claude (closes the loop: production vs candidate)…"
    $PY scripts/run_experiment.py --model claude-sonnet-4-6 --prompt-label candidate
  fi
fi

echo -e "\n✓ Done. Open Langfuse (http://localhost:3001) → project 'real-estate'."
echo "  Prompts, Evaluators, Datasets > Runs (compare Claude vs gpt-4o, production vs candidate),"
echo "  Annotation Queues, Tracing."
echo "  Then start the portal:  ./run_portal.sh   → http://localhost:8080"

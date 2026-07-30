#!/usr/bin/env bash
# One-shot demo prep. Run this BEFORE presenting so Langfuse is full of data.
#
#   ./run_demo.sh                  # full prep (~10-15 min): traffic + both experiments
#   ./run_demo.sh --quick          # skip the experiments (traffic + evaluators only)
#   ./run_demo.sh --no-judge       # skip the custom SDK judges on live traffic (faster)
#   ./run_demo.sh --no-gpt         # skip the GPT comparison run
#   ./run_demo.sh --prompt-variant # ALSO run the candidate prompt (closes the loop:
#                                  #   production vs candidate compare, same model)
#   ./run_demo.sh --lifecycle      # ALSO run the naive first-draft prompt AND a
#                                  #   same-prompt control run. This is the prep for
#                                  #   docs/LIFECYCLE_FEEDBACK_RUNBOOK.md: first-draft
#                                  #   vs production is a VISIBLE deterministic win,
#                                  #   and the control run proves the judge noise
#                                  #   floor is wider than any judge delta.
set -euo pipefail
cd "$(dirname "$0")"
PY=./.venv/bin/python

[ -d .venv ] || { echo "No .venv. Run: python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"; exit 1; }

QUICK=0; JUDGE_FLAG=""; GPT=1; PROMPT_VARIANT=0; LIFECYCLE=0
for a in "$@"; do
  [ "$a" = "--quick" ] && QUICK=1
  [ "$a" = "--no-judge" ] && JUDGE_FLAG="--no-judge"
  [ "$a" = "--no-gpt" ] && GPT=0
  [ "$a" = "--prompt-variant" ] && PROMPT_VARIANT=1
  [ "$a" = "--lifecycle" ] && LIFECYCLE=1
done

echo "════════════════════════════════════════════════════════"
echo " Property Concierge — demo data prep"
echo "════════════════════════════════════════════════════════"

echo -e "\n[1/7] Seeding prompts into Langfuse Prompt Management (first-draft + production + candidate)…"
$PY scripts/seed_prompts.py

echo -e "\n[2/7] Seeding evaluation dataset (18 items)…"
$PY scripts/seed_dataset.py

echo -e "\n[3/7] Provisioning managed LLM-as-a-Judge evaluators (Anthropic)…"
./scripts/seed_managed_evaluators.sh || echo "  (managed evaluators step skipped — self-hosted: check Docker/Postgres; cloud: see steps above)"

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
  if [ "$LIFECYCLE" = "1" ]; then
    echo -e "\n[+]   Experiment run — FIRST-DRAFT prompt on Claude (the 'before': visible deterministic win)…"
    $PY scripts/run_experiment.py --model claude-sonnet-4-6 --prompt-label first-draft
    # The control. Without a same-prompt repeat you cannot tell a judge delta from
    # noise — and citing one anyway is the exact mistake the runbook warns about.
    echo -e "\n[+]   Experiment run — PRODUCTION repeat (the control: judge noise floor)…"
    $PY scripts/run_experiment.py --model claude-sonnet-4-6 --prompt-label production \
      --run-name production-repeat
  fi
fi

LF_HOST=$(grep -E '^LANGFUSE_HOST=' .env 2>/dev/null | tail -1 | cut -d= -f2-)
LF_PROJECT=$(grep -E '^LANGFUSE_PROJECT_NAME=' .env 2>/dev/null | tail -1 | cut -d= -f2-)
echo -e "\n✓ Done. Open Langfuse (${LF_HOST:-http://localhost:3001}) → project '${LF_PROJECT:-real-estate}'."
echo "  Prompts, Evaluators, Datasets > Runs (compare Claude vs gpt-4o, production vs candidate),"
echo "  Annotation Queues, Tracing."
echo "  Then start the portal:  ./run_portal.sh   → http://localhost:8080"

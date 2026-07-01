#!/usr/bin/env bash
# Provision Langfuse-managed LLM-as-a-Judge evaluators for the 'real-estate'
# project so they run AUTOMATICALLY on live traffic (visible under Evaluators).
#
# These are Langfuse-native evaluators (not the client-side judges the demo also
# ships): the Langfuse worker runs them on new traces tagged 'real-estate' using
# the Anthropic LLM connection you configured, and writes scores back.
#
# There is no public REST API for managed evaluators in this Langfuse version,
# so — like this repo's other evaluator seeders — we insert directly into the
# Langfuse Postgres. Idempotent.
#
# Usage:  ./scripts/seed_managed_evaluators.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && source .env && set +a

PG_CONTAINER="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
REDIS_CONTAINER="${LANGFUSE_REDIS_CONTAINER:-langfuse-redis}"
EVAL_MODEL="${MANAGED_EVAL_MODEL:-claude-sonnet-4-6}"

green(){ printf '  \033[0;32m✓\033[0m %s\n' "$1"; }
warn(){  printf '  \033[1;33m⚠\033[0m %s\n' "$1"; }

docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$" \
  || { echo "Postgres container ${PG_CONTAINER} not running."; exit 1; }

q(){ docker exec -i "$PG_CONTAINER" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }
# Escape single quotes for safe interpolation into SQL string literals.
sql_esc(){ printf "%s" "$1" | sed "s/'/''/g"; }

EXPECTED_PROJECT="real-estate"
PK_ESC=$(sql_esc "${LANGFUSE_PUBLIC_KEY}")
PROJECT_ID=$(echo "SELECT project_id FROM api_keys WHERE public_key='${PK_ESC}' LIMIT 1;" | q)
[ -n "$PROJECT_ID" ] || { echo "Could not resolve project for the configured public key."; exit 1; }

# Key-isolation guard (mirrors config.verify_project): refuse to write to any
# project other than 'real-estate' so a stale/wrong shell key can't pollute another.
PID_ESC=$(sql_esc "${PROJECT_ID}")
PROJECT_NAME=$(echo "SELECT name FROM projects WHERE id='${PID_ESC}' LIMIT 1;" | q)
if [ "$PROJECT_NAME" != "$EXPECTED_PROJECT" ]; then
  echo "Refusing: keys resolve to project '${PROJECT_NAME:-?}' (id ${PROJECT_ID}), expected '${EXPECTED_PROJECT}'."
  exit 1
fi
green "Project: ${PROJECT_ID} (${PROJECT_NAME})"

echo ""
echo "Provisioning managed LLM-as-a-Judge evaluators (model: ${EVAL_MODEL})…"

# 1) Default evaluation model — powered by the Anthropic LLM connection.
LLM_KEY_ID=$(echo "SELECT id FROM llm_api_keys WHERE project_id='${PROJECT_ID}' AND adapter='anthropic' LIMIT 1;" | q)
if [ -z "$LLM_KEY_ID" ]; then
  warn "No Anthropic LLM connection on this project — add one in Settings > LLM Connections, then re-run."
  exit 1
fi
EVAL_MODEL_ESC=$(sql_esc "${EVAL_MODEL}")
LLM_KEY_ID_ESC=$(sql_esc "${LLM_KEY_ID}")
echo "INSERT INTO default_llm_models (id, project_id, llm_api_key_id, provider, adapter, model, model_params, created_at, updated_at)
      SELECT 'default-eval-${PID_ESC}', '${PID_ESC}', '${LLM_KEY_ID_ESC}', provider, adapter, '${EVAL_MODEL_ESC}', '{}'::jsonb, now(), now()
      FROM llm_api_keys WHERE id='${LLM_KEY_ID_ESC}'
      ON CONFLICT (project_id) DO UPDATE SET llm_api_key_id=EXCLUDED.llm_api_key_id, model=EXCLUDED.model, updated_at=now();" | q > /dev/null
green "Default evaluation model set (${EVAL_MODEL})"

# 2) Trace-level judges over 'real-estate' traffic. Clean mapping:
#    query = trace.input (the question), generation = trace.output (the answer).
MAP='[{"templateVariable":"query","langfuseObject":"trace","selectedColumnId":"input"},{"templateVariable":"generation","langfuseObject":"trace","selectedColumnId":"output"}]'
FILTER='[{"type":"arrayOptions","value":["real-estate"],"column":"tags","operator":"any of"}]'

seed_judge(){
  local cfg="$1" template="$2" score="$3"
  local tid
  tid=$(echo "SELECT id FROM eval_templates WHERE project_id IS NULL AND name='${template}' AND type='LLM_AS_JUDGE' AND coalesce(partner,'')='' ORDER BY version DESC LIMIT 1;" | q)
  if [ -z "$tid" ]; then warn "template '${template}' not found — skipped"; return; fi
  echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
        VALUES ('${cfg}', '${PROJECT_ID}', 'EVAL', '${tid}', '${score}', '${FILTER}'::jsonb, 'trace', '${MAP}'::jsonb, 1.0, 0, 'ACTIVE', ARRAY['NEW','EXISTING'], now(), now())
        ON CONFLICT (id) DO UPDATE SET eval_template_id=EXCLUDED.eval_template_id, filter=EXCLUDED.filter, variable_mapping=EXCLUDED.variable_mapping, status='ACTIVE', blocked_at=NULL, block_reason=NULL, block_message=NULL, time_scope=EXCLUDED.time_scope, updated_at=now();" | q > /dev/null
  green "${score} (managed, trace-level)"
}

# Both score 1 = good, consistent with the code + custom scores. We intentionally
# do NOT seed the built-in "Hallucination" template here: it scores 1 = BAD
# (inverted), which is confusing next to everything else — the custom
# 'groundedness' judge (1 = good) covers that concern in the consistent direction.
seed_judge "re-managed-helpfulness"   "Helpfulness"   "Helpfulness"
seed_judge "re-managed-relevance"     "Relevance"     "Relevance"

# 3) Clear the worker's "no evaluators" cache so it picks up traffic now.
if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
  docker exec "$REDIS_CONTAINER" redis-cli del \
    "langfuse:eval:no-event-and-experiment-job-configs:${PROJECT_ID}" \
    "langfuse:eval:no-trace-and-dataset-job-configs:${PROJECT_ID}" > /dev/null 2>&1 || true
  green "Cleared evaluator config cache"
fi

echo ""
green "Managed evaluators active. View: ${LANGFUSE_HOST:-http://localhost:3001}/project/${PROJECT_ID}/evals"
echo "  They score NEW + EXISTING traces tagged 'real-estate' automatically."

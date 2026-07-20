#!/usr/bin/env bash
# Provision Langfuse-managed LLM-as-a-Judge evaluators for the demo's project
# (default 'real-estate', override via LANGFUSE_PROJECT_NAME) so they run
# AUTOMATICALLY on live traffic (visible under Evaluators).
#
# These are Langfuse-native evaluators (not the client-side judges the demo also
# ships): the Langfuse worker runs them on new traces tagged 'real-estate' using
# the Anthropic LLM connection you configured, and writes scores back.
#
# Two modes:
#   self-hosted (localhost) — managed evaluators have no public REST API, so
#     like this repo's other evaluator seeders we insert directly into the
#     Langfuse Postgres. Idempotent.
#   remote / Langfuse Cloud — no DB access: upsert the LLM connection via the
#     public API and print the short UI recipe for the judges.
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

EXPECTED_PROJECT="${LANGFUSE_PROJECT_NAME:-real-estate}"

# ---- Langfuse Cloud / remote host: no direct DB access ----------------------
# job_configurations have no public API, so on Cloud the two judges are set up
# in the UI. We still provision what the API allows (the Anthropic LLM
# connection) and print the exact remaining steps. Exit 0 so run_demo.sh flows.
case "${LANGFUSE_HOST:-http://localhost:3001}" in
  http://localhost*|https://localhost*|http://127.0.0.1*|https://127.0.0.1*)
    ;;  # self-hosted: fall through to DB seeding
  *)
    echo "Remote Langfuse host detected (${LANGFUSE_HOST}) — managed evaluators can't be DB-seeded."
    # Key-isolation guard (mirrors config.verify_project): resolve the keys'
    # project via the public API and refuse to write anywhere unexpected —
    # the PUT below uploads a real Anthropic secret. `|| true` guards keep
    # set -e from killing the script before the fallback guidance prints.
    projects=$(curl -s -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" \
      "${LANGFUSE_HOST}/api/public/projects" || true)
    if ! printf '%s' "$projects" | grep -Eq "\"name\":[[:space:]]*\"${EXPECTED_PROJECT}\""; then
      echo "Refusing: keys do not resolve to project '${EXPECTED_PROJECT}' on ${LANGFUSE_HOST}."
      echo "  API response: ${projects:-<no response — host unreachable?>}"
      exit 1
    fi
    green "Project verified: ${EXPECTED_PROJECT} @ ${LANGFUSE_HOST}"
    if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
      json_esc(){ printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
      code=$(curl -s -o /tmp/lf-llmconn.json -w '%{http_code}' -X PUT \
        -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" \
        -H 'Content-Type: application/json' \
        "${LANGFUSE_HOST}/api/public/llm-connections" \
        -d "{\"provider\":\"anthropic\",\"adapter\":\"anthropic\",\"secretKey\":\"$(json_esc "${ANTHROPIC_API_KEY}")\"}") \
        || code="000"
      if [ "$code" = "200" ] || [ "$code" = "201" ]; then
        green "Anthropic LLM connection upserted via API"
      else
        warn "Could not upsert LLM connection (HTTP ${code}) — add it in Settings > LLM Connections"
      fi
    else
      warn "ANTHROPIC_API_KEY not set — add the connection in Settings > LLM Connections"
    fi
    cat <<STEPS

  Finish in the Langfuse UI (~2 min), project '${EXPECTED_PROJECT}':
    1. Settings > LLM Connections — confirm the 'anthropic' connection exists.
    2. Evaluators (Evals) > Default evaluation model — pick ${EVAL_MODEL}.
    3. Evaluators > + New evaluator, twice — templates 'Helpfulness' and 'Relevance':
         target        = live tracing data (New + Existing traces)
         filter        = tag 'real-estate'
         variable map  = query -> trace input, generation -> trace output
         sampling      = 100%
  Everything else (prompts, datasets, traffic, experiments, annotation queue,
  code + SDK judge scores) seeds via the public API — no UI steps needed.
STEPS
    exit 0
    ;;
esac

docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$" \
  || { echo "Postgres container ${PG_CONTAINER} not running."; exit 1; }

q(){ docker exec -i "$PG_CONTAINER" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }
# Escape single quotes for safe interpolation into SQL string literals.
sql_esc(){ printf "%s" "$1" | sed "s/'/''/g"; }

PK_ESC=$(sql_esc "${LANGFUSE_PUBLIC_KEY}")
PROJECT_ID=$(echo "SELECT project_id FROM api_keys WHERE public_key='${PK_ESC}' LIMIT 1;" | q)
[ -n "$PROJECT_ID" ] || { echo "Could not resolve project for the configured public key."; exit 1; }

# Key-isolation guard (mirrors config.verify_project): refuse to write to any
# project other than the expected one so a stale/wrong shell key can't pollute another.
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

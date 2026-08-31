#!/usr/bin/env bash
# Provision the observation-level, categorical LLM-as-a-Judge `route-plausibility`
# for the query-router demo — a ground-truth-FREE proxy for *silent
# misclassification*. It reads the router's own `route-query` generation
# (question <- input.question, decision <- output {route, rationale}) and judges
# whether the chosen route is plausible, emitting one of the taxonomy categories
# ∪ {ambiguous}. A judged `ambiguous`, or a judged route != chosen route, is the
# curation signal that feeds the router-accuracy dataset + annotation queue.
#
# Mechanism mirrors scripts/seed-agentic-rag-evaluators.sh: a Postgres upsert
# into eval_templates + job_configurations (schema-coupled to the langfuse:3
# image), sampling 0.25, time_scope=NEW (only NEW traffic is scored — regenerate
# router traffic to see it), the default-eval-model reuse, and the Redis
# evaluator-cache clear. Idempotent. Self-hosted only; prints UI steps in cloud.
#
# NOTE: the custom-template INSERT is schema-coupled to the langfuse image; it is
# guarded so a schema mismatch prints UI guidance instead of aborting the stack.
#
# Usage: ./scripts/seed-router-judge.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$SCRIPT_DIR/.."
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
ok(){ echo -e "  ${GREEN}✓${NC} $1"; }; warn(){ echo -e "  ${YELLOW}⚠${NC} $1"; }
info(){ echo -e "  ${BLUE}ℹ${NC} $1"; }; fail(){ echo -e "  ${RED}✗${NC} $1"; exit 1; }
[ -f .env ] && set -a && source .env && set +a

PG="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
REDIS="${LANGFUSE_REDIS_CONTAINER:-langfuse-redis}"
EVAL_MODEL="${LANGFUSE_EVAL_MODEL:-claude-haiku-4-5-20251001}"
SCORE_NAME="route-plausibility"
CATEGORIES="analytics_sql, docs_simple, docs_complex, out_of_scope, ambiguous"

echo ""
echo "Provisioning categorical LLM-as-a-Judge '${SCORE_NAME}' for query-router..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
  warn "DEPLOY_MODE=cloud — create this judge in the Langfuse UI:"
  echo "     Evaluators > New evaluator > Custom > target Observations, filter"
  echo "       type=GENERATION, name any-of [route-query], tags any-of [query-router]"
  echo "     Template (categorical): allowed categories = ${CATEGORIES}"
  echo "       variables: question <- input.question ; decision <- output"
  echo "       score name '${SCORE_NAME}', sampling 0.25, time scope NEW"
  exit 0
fi

docker ps --format '{{.Names}}' | grep -q "^${PG}$" || fail "Postgres ${PG} not running (run ./setup.sh first)"
pg(){ docker exec -i "$PG" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }

PID=$(echo "SELECT project_id FROM api_keys WHERE public_key='${LANGFUSE_PUBLIC_KEY:-pk-lf-1234567890}' LIMIT 1;" | pg)
PID="${PID:-demo-project}"; ok "Project: ${PID}"

# ── Default eval model (reuse the Anthropic connection setup.sh provisions) ──
if [ "$(echo "SELECT count(*) FROM default_llm_models WHERE project_id='${PID}';" | pg)" = "0" ]; then
  LLM_KEY_ID=$(echo "SELECT id FROM llm_api_keys WHERE project_id='${PID}' AND adapter='anthropic' LIMIT 1;" | pg)
  if [ -n "$LLM_KEY_ID" ]; then
    echo "INSERT INTO default_llm_models (id, project_id, llm_api_key_id, provider, adapter, model, model_params, created_at, updated_at)
          SELECT 'default-eval-model-${PID}','${PID}','${LLM_KEY_ID}',provider,adapter,'${EVAL_MODEL}','{}'::jsonb,now(),now()
          FROM llm_api_keys WHERE id='${LLM_KEY_ID}' ON CONFLICT (project_id) DO NOTHING;" | pg >/dev/null
    ok "Default eval model set (${EVAL_MODEL})"
  else
    warn "No Anthropic LLM connection — run ./setup.sh first; the judge stays blocked until a default eval model exists"
  fi
else
  ok "Default eval model already configured"
fi

TEMPLATE_ID="router-route-plausibility"
JOB_ID="router-route-plausibility-job"

# Judge prompt: reads {{question}} + {{decision}}, returns one category + reasoning.
read -r -d '' JUDGE_PROMPT <<PROMPT || true
You are auditing a query router. Given a user QUESTION and the router's DECISION
(its chosen route + rationale), decide whether the chosen route is PLAUSIBLE.
Answer with exactly ONE category:
- analytics_sql : the question needs live numbers from datasets
- docs_simple   : a single factual/definitional doc question
- docs_complex  : multi-part / comparative / accuracy-critical doc question
- out_of_scope  : small talk, unrelated domain, or unsafe
- ambiguous     : the question genuinely spans routes or is too vague to route confidently

Output the category that BEST fits the question. If it matches the router's
chosen route the routing was plausible; if not (or 'ambiguous'), it is a
misroute-risk worth review.

QUESTION: {{question}}
DECISION: {{decision}}
PROMPT

# output_schema: standard {score, reasoning} form; the score carries the chosen
# category string (categorical). vars: question, decision.
OUTPUT_SCHEMA='{"score":"One of: '"${CATEGORIES}"'","reasoning":"One sentence justification"}'
MODEL_PARAMS='{"temperature":0,"max_tokens":256}'

# Observation-level filter: the router's own route-query GENERATION on query-router traces.
FILTER='[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["route-query"],"column":"name","operator":"any of"},{"type":"arrayOptions","value":["query-router"],"column":"tags","operator":"any of"}]'
MAPPING='[{"templateVariable":"question","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"question"},{"templateVariable":"decision","langfuseObject":"event","selectedColumnId":"output"}]'

# Create the custom template (guarded: a schema mismatch must not abort the stack).
set +e
docker exec -i "$PG" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<SQL >/tmp/router_judge_tmpl.log 2>&1
INSERT INTO eval_templates (id, project_id, name, version, prompt, provider, model, model_params, vars, output_schema, type, created_at, updated_at)
VALUES ('${TEMPLATE_ID}', '${PID}', 'route-plausibility', 1, \$JUDGE\$${JUDGE_PROMPT}\$JUDGE\$, 'anthropic', '${EVAL_MODEL}', '${MODEL_PARAMS}'::jsonb, ARRAY['question','decision'], '${OUTPUT_SCHEMA}'::jsonb, 'LLM_AS_JUDGE', now(), now())
ON CONFLICT (project_id, name, version) DO UPDATE SET prompt=EXCLUDED.prompt, model=EXCLUDED.model, model_params=EXCLUDED.model_params, vars=EXCLUDED.vars, output_schema=EXCLUDED.output_schema, updated_at=now();
SQL
TMPL_RC=$?
set -e
if [ "$TMPL_RC" != "0" ]; then
  warn "Could not create the custom template via SQL (schema-coupled). Details: $(tr '\n' ' ' </tmp/router_judge_tmpl.log)"
  info "Create it once in the UI (Evaluators > New evaluator > Custom): categorical, categories = ${CATEGORIES},"
  info "  variables question<-input.question, decision<-output; then re-run this script to wire the job."
  exit 0
fi
ok "route-plausibility template (categorical: ${CATEGORIES})"

echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
      VALUES ('${JOB_ID}','${PID}','EVAL','${TEMPLATE_ID}','${SCORE_NAME}','${FILTER}'::jsonb,'event','${MAPPING}'::jsonb,0.25,0,'ACTIVE',ARRAY['NEW'],now(),now())
      ON CONFLICT (id) DO UPDATE SET eval_template_id=EXCLUDED.eval_template_id, score_name=EXCLUDED.score_name, filter=EXCLUDED.filter, variable_mapping=EXCLUDED.variable_mapping, sampling=0.25, status='ACTIVE', blocked_at=NULL, block_reason=NULL, block_message=NULL, updated_at=now();" | pg >/dev/null
ok "${SCORE_NAME} → observation-level judge on route-query (sampling 0.25, time_scope=NEW)"

# Clear the worker's "no evaluators" cache so the new config picks up traffic now.
if docker ps --format '{{.Names}}' | grep -q "^${REDIS}$"; then
  docker exec "$REDIS" redis-cli del \
    "langfuse:eval:no-event-and-experiment-job-configs:${PID}" \
    "langfuse:eval:no-trace-and-dataset-job-configs:${PID}" >/dev/null 2>&1 || true
  ok "Cleared evaluator config cache"
fi

echo ""
ok "route-plausibility seeded. Regenerate router traffic, then curate: a judged 'ambiguous'"
echo "  (or a judged route != chosen route) is the signal that feeds the dataset + annotation queue."
echo "  View: ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PID}/evals"
echo ""

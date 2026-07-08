#!/usr/bin/env bash
# Provision INDEPENDENT observation-level LLM-as-a-Judge evaluators for the
# agentic-rag demo — a Langfuse-side check that complements the agent's own
# IN-GRAPH self-grades (reflect `groundedness` + grade `retrieval_relevance`).
#
# Seeds three judges over the agentic-rag GENERATION "generate" observation
# (which exposes {question, context} on its input — see demos/agentic-rag/graph.py):
#   faithfulness      (Faithfulness,      {context,answer})     ↔ pairs with in-graph `groundedness`
#   context-relevance (Contextrelevance,  {query,context})      ↔ pairs with in-graph `retrieval_relevance`
#   answer-relevance  (Relevance,         {query,generation})   additive answer-quality signal
#
# Mechanism mirrors scripts/seed-llm-judge-evaluators.sh: Postgres upsert into
# job_configurations (schema-coupled to the langfuse:3 image). time_scope=NEW so
# only NEW traces are scored — regenerate agentic-rag traffic to see them.
# Idempotent. Self-hosted only (prints UI guidance in cloud mode).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$SCRIPT_DIR/.."
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok(){ echo -e "  ${GREEN}✓${NC} $1"; }; warn(){ echo -e "  ${YELLOW}⚠${NC} $1"; }; fail(){ echo -e "  ${RED}✗${NC} $1"; exit 1; }
[ -f .env ] && set -a && source .env && set +a

PG="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
REDIS="${LANGFUSE_REDIS_CONTAINER:-langfuse-redis}"
EVAL_MODEL="${LANGFUSE_EVAL_MODEL:-claude-haiku-4-5-20251001}"

echo ""
echo "Provisioning independent LLM-as-a-Judge evaluators for agentic-rag..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
  warn "DEPLOY_MODE=cloud — create these observation-level judges in the Langfuse UI:"
  echo "     Evaluators > New evaluator > target Observations, filter"
  echo "       type=GENERATION, name any-of [generate], tags any-of [agentic-rag]:"
  echo "       - Faithfulness      (context<-input.context, answer<-output)      score 'faithfulness'"
  echo "       - Contextrelevance  (query<-input.question, context<-input.context) score 'context-relevance'"
  echo "       - Relevance         (query<-input.question, generation<-output)   score 'answer-relevance'"
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
    warn "No Anthropic LLM connection — run ./setup.sh first; judges stay blocked until a default eval model exists"
  fi
else
  ok "Default eval model already configured"
fi

# ── Template lookup by name + a required var (disambiguates the two RAGAS
#    Faithfulness variants; unlike the main script we DON'T exclude partner
#    templates, because Faithfulness/Contextrelevance ship as partner=ragas). ──
tmpl(){ echo "SELECT id FROM eval_templates WHERE project_id IS NULL AND name='$1' AND '$2'=ANY(vars) ORDER BY version DESC LIMIT 1;" | pg; }

# All three watch the agentic-rag answer generation only (type GENERATION +
# name 'generate' + trace tag agentic-rag) — NOT the route/grade/rewrite/reflect
# LLM calls, which are also GENERATIONs.
FILTER='[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["generate"],"column":"name","operator":"any of"},{"type":"arrayOptions","value":["agentic-rag"],"column":"tags","operator":"any of"}]'

seed(){ # <config_id> <template_name> <required_var> <score_name> <mapping_json>
  local cid="$1" tname="$2" rvar="$3" sname="$4" map="$5" tid
  tid=$(tmpl "$tname" "$rvar")
  if [ -z "$tid" ]; then warn "template '${tname}' ({${rvar}}) not found — skipped"; return; fi
  echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
        VALUES ('${cid}','${PID}','EVAL','${tid}','${sname}','${FILTER}'::jsonb,'event','${map}'::jsonb,1.0,0,'ACTIVE',ARRAY['NEW'],now(),now())
        ON CONFLICT (id) DO UPDATE SET eval_template_id=EXCLUDED.eval_template_id, score_name=EXCLUDED.score_name, filter=EXCLUDED.filter, variable_mapping=EXCLUDED.variable_mapping, status='ACTIVE', blocked_at=NULL, block_reason=NULL, block_message=NULL, updated_at=now();" | pg >/dev/null
  ok "${sname} → observation-level judge (${tname})"
}

seed "arag-faithfulness" "Faithfulness" "context" "faithfulness" \
  '[{"templateVariable":"context","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"context"},{"templateVariable":"answer","langfuseObject":"event","selectedColumnId":"output"}]'

seed "arag-context-relevance" "Contextrelevance" "context" "context-relevance" \
  '[{"templateVariable":"query","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"question"},{"templateVariable":"context","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"context"}]'

seed "arag-answer-relevance" "Relevance" "query" "answer-relevance" \
  '[{"templateVariable":"query","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"question"},{"templateVariable":"generation","langfuseObject":"event","selectedColumnId":"output"}]'

# Clear the worker's "no evaluators" cache so the new configs pick up traffic now.
if docker ps --format '{{.Names}}' | grep -q "^${REDIS}$"; then
  docker exec "$REDIS" redis-cli del \
    "langfuse:eval:no-event-and-experiment-job-configs:${PID}" \
    "langfuse:eval:no-trace-and-dataset-job-configs:${PID}" >/dev/null 2>&1 || true
  ok "Cleared evaluator config cache"
fi

echo ""
ok "Independent judges seeded (time_scope=NEW). Regenerate agentic-rag traffic, then compare:"
echo "     self (in-graph)  vs  independent (managed)"
echo "     groundedness      ↔  faithfulness"
echo "     retrieval_relevance ↔ context-relevance"
echo "  View: ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PID}/evals"
echo ""

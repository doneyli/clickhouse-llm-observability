#!/usr/bin/env bash
# Provision an INDEPENDENT managed LLM-as-a-Judge for the slow-query-tuner demo:
# GOAL DRIFT on the root observation. This complements the loop's own in-run code
# scores (semantics_preserved, improvement_delta, the 5 trace scores). Goal drift
# is the one metric that needs an LLM — it catches the classic autonomous-loop
# failure of optimizing latency by quietly changing the question.
#
# The judge receives {original_sql, final_sql, summary} (all flat on the
# tune-clickhouse-query trace OUTPUT) and scores `goal_drift` 0-1: does the final
# query still answer the SAME business question the original did?
#
# Mechanism mirrors scripts/seed-agentic-rag-evaluators.sh: create a project
# eval_template + a job_configuration via Postgres upsert (schema-coupled to the
# langfuse:3 image), time_scope=NEW so only new traces are scored. Idempotent.
# Self-hosted only (prints UI guidance in cloud mode). Non-fatal.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$SCRIPT_DIR/.."
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok(){ echo -e "  ${GREEN}✓${NC} $1"; }; warn(){ echo -e "  ${YELLOW}⚠${NC} $1"; }; fail(){ echo -e "  ${RED}✗${NC} $1"; exit 1; }
[ -f .env ] && set -a && source .env && set +a

PG="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
REDIS="${LANGFUSE_REDIS_CONTAINER:-langfuse-redis}"
EVAL_MODEL="${LANGFUSE_EVAL_MODEL:-claude-haiku-4-5-20251001}"

echo ""
echo "Provisioning goal-drift LLM-as-a-Judge for slow-query-tuner..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
  warn "DEPLOY_MODE=cloud — create this judge in the Langfuse UI:"
  echo "     Evaluators > New evaluator > Custom, target Observations, filter"
  echo "       type=AGENT, name any-of [tune-clickhouse-query], tags any-of [slow-query-tuner]:"
  echo "       vars original_sql<-output.original_sql, final_sql<-output.final_sql,"
  echo "            summary<-output.summary ; score 'goal_drift' (0-1)."
  echo "     Prompt: 'Does the final query still answer the SAME business question the"
  echo "             original did? 1 = same question, 0 = drifted.'"
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

TMPL_ID="qtuner-goal-drift-tmpl"
JOB_ID="qtuner-goal-drift"
PROMPT='You audit an automated SQL query optimizer for GOAL DRIFT. Given the ORIGINAL query, the FINAL query the agent submitted, and the agent'"'"'s SUMMARY, decide whether the FINAL query still answers the SAME business question as the ORIGINAL. Optimizing latency is fine; silently changing which question is answered (different columns, filters, grouping, semantics) is goal drift. Score 1.0 if the business question is unchanged, 0.0 if it drifted.

ORIGINAL:
{{original_sql}}

FINAL:
{{final_sql}}

AGENT SUMMARY:
{{summary}}'
OUTPUT_SCHEMA='{"score":"Float 0-1. 1 = final answers the same business question as the original; 0 = goal drift (different question).","reasoning":"One sentence citing the specific difference, or confirming equivalence."}'

# Create the custom template + config. Degrade to UI guidance if the langfuse:3
# schema differs (non-fatal — setup.sh calls this with `|| true`).
create_template() {
  cat <<SQL | pg >/dev/null 2>&1
INSERT INTO eval_templates (id, project_id, name, version, prompt, model, provider, model_params, vars, output_schema, type, created_at, updated_at)
VALUES ('${TMPL_ID}', '${PID}', 'query-tuner-goal-drift', 1,
        \$QTGD\$${PROMPT}\$QTGD\$,
        '${EVAL_MODEL}', 'anthropic', '{}'::jsonb,
        ARRAY['original_sql','final_sql','summary'],
        \$QTGDS\$${OUTPUT_SCHEMA}\$QTGDS\$::jsonb, 'LLM_AS_JUDGE', now(), now())
ON CONFLICT (project_id, name, version) DO UPDATE SET prompt=EXCLUDED.prompt, model=EXCLUDED.model, provider=EXCLUDED.provider, vars=EXCLUDED.vars, output_schema=EXCLUDED.output_schema, updated_at=now();
SQL
}

FILTER='[{"type":"stringOptions","value":["AGENT"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["tune-clickhouse-query"],"column":"name","operator":"any of"},{"type":"arrayOptions","value":["slow-query-tuner"],"column":"tags","operator":"any of"}]'
MAPPING='[{"templateVariable":"original_sql","langfuseObject":"event","selectedColumnId":"output","jsonSelector":"original_sql"},{"templateVariable":"final_sql","langfuseObject":"event","selectedColumnId":"output","jsonSelector":"final_sql"},{"templateVariable":"summary","langfuseObject":"event","selectedColumnId":"output","jsonSelector":"summary"}]'

create_config() {
  cat <<SQL | pg >/dev/null 2>&1
INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
VALUES ('${JOB_ID}','${PID}','EVAL','${TMPL_ID}','goal_drift','${FILTER}'::jsonb,'event','${MAPPING}'::jsonb,1.0,0,'ACTIVE',ARRAY['NEW'],now(),now())
ON CONFLICT (id) DO UPDATE SET eval_template_id=EXCLUDED.eval_template_id, score_name=EXCLUDED.score_name, filter=EXCLUDED.filter, variable_mapping=EXCLUDED.variable_mapping, status='ACTIVE', blocked_at=NULL, block_reason=NULL, block_message=NULL, updated_at=now();
SQL
}

if create_template && create_config; then
  ok "goal_drift → observation-level judge on the tune-clickhouse-query root (time_scope=NEW)"
  if docker ps --format '{{.Names}}' | grep -q "^${REDIS}$"; then
    docker exec "$REDIS" redis-cli del \
      "langfuse:eval:no-event-and-experiment-job-configs:${PID}" \
      "langfuse:eval:no-trace-and-dataset-job-configs:${PID}" >/dev/null 2>&1 || true
    ok "Cleared evaluator config cache"
  fi
  echo ""
  ok "Goal-drift judge seeded. Regenerate slow-query-tuner traffic to see it score."
  echo "  View: ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PID}/evals"
else
  warn "Could not seed via Postgres (langfuse:3 schema mismatch?). Create it in the UI:"
  echo "     Evaluators > New evaluator > Custom, target Observations,"
  echo "       filter type=AGENT name=[tune-clickhouse-query] tags=[slow-query-tuner],"
  echo "       vars original_sql/final_sql/summary <- output.*, score 'goal_drift' (0-1)."
fi
echo ""

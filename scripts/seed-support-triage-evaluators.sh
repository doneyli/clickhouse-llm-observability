#!/usr/bin/env bash
# Provision the INDEPENDENT managed LLM-as-a-Judge for the support-triage-parallel
# demo — the "who checks the vote-counter" evaluator that complements the
# deterministic consensus-margin-guard code evaluator (evaluators/consensus-margin-guard.ts).
#
# Seeds one judge over the aggregator observation:
#   correlated-vote-risk  → scores 0–1 the RISK that the winning majority is a
#   *correlated* failure (all samples share the same wrong table / filter / bad
#   assumption — "confidently wrong consensus", the pattern's key failure mode).
#
# Because the app writes ALL candidates onto the `tally-votes` observation INPUT
# and the tally onto its METADATA (see demos/support-triage-parallel/sql_voting.py),
# the judge sees every sample + the vote in one place — an observation-level
# evaluator otherwise cannot pull in the N candidate child-observations.
#
# Mechanism mirrors scripts/seed-agentic-rag-evaluators.sh: Postgres upsert
# (schema-coupled to the langfuse:3 image). Unlike that script this judge has no
# stock template, so we CREATE a project-scoped LLM eval_template (prompt +
# output_schema) reusing the Anthropic connection setup.sh provisions.
# time_scope=NEW so only NEW traces are scored — regenerate traffic to see it.
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
echo "Provisioning the independent 'correlated-vote-risk' judge for support-triage-parallel..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
  warn "DEPLOY_MODE=cloud — create this observation-level judge in the Langfuse UI:"
  echo "     Evaluators > New evaluator > Custom > target Observations, filter"
  echo "       type=SPAN, name any-of [tally-votes], traceName any-of [triage-support-ticket]:"
  echo "       correlated-vote-risk — prompt scores 0–1 the risk the majority is a correlated"
  echo "       failure; map {{candidates}}<-input.candidates, {{tally}}<-metadata.votes; score 'correlated-vote-risk'"
  exit 0
fi

docker ps --format '{{.Names}}' | grep -q "^${PG}$" || fail "Postgres ${PG} not running (run ./setup.sh first)"
pg(){ docker exec -i "$PG" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }

PID=$(echo "SELECT project_id FROM api_keys WHERE public_key='${LANGFUSE_PUBLIC_KEY:-pk-lf-1234567890}' LIMIT 1;" | pg)
PID="${PID:-demo-project}"; ok "Project: ${PID}"

# ── Default eval model (reuse the Anthropic connection setup.sh provisions) ──
LLM_KEY_ID=$(echo "SELECT id FROM llm_api_keys WHERE project_id='${PID}' AND adapter='anthropic' LIMIT 1;" | pg)
if [ -z "$LLM_KEY_ID" ]; then
  warn "No Anthropic LLM connection — run ./setup.sh first; the judge stays blocked until a default eval model exists"
fi
if [ "$(echo "SELECT count(*) FROM default_llm_models WHERE project_id='${PID}';" | pg)" = "0" ] && [ -n "$LLM_KEY_ID" ]; then
  echo "INSERT INTO default_llm_models (id, project_id, llm_api_key_id, provider, adapter, model, model_params, created_at, updated_at)
        SELECT 'default-eval-model-${PID}','${PID}','${LLM_KEY_ID}',provider,adapter,'${EVAL_MODEL}','{}'::jsonb,now(),now()
        FROM llm_api_keys WHERE id='${LLM_KEY_ID}' ON CONFLICT (project_id) DO NOTHING;" | pg >/dev/null
  ok "Default eval model set (${EVAL_MODEL})"
else
  ok "Default eval model already configured (or no Anthropic connection yet)"
fi

# ── Custom LLM judge template (project-scoped) ───────────────────────────────
TID="eval-tmpl-correlated-vote-risk"
JUDGE_PROMPT='You are auditing a best-of-N self-consistency vote. N SQL candidates were sampled for the SAME question and a majority winner was chosen. Voting defends against random per-sample error but NOT against correlated error: if every sample shares the same wrong table, wrong filter, or bad assumption, they will confidently agree on a wrong answer.

Question and candidates (with the vote tally) are below.

Candidates:
{{candidates}}

Vote tally:
{{tally}}

Score the RISK that the winning majority is a CORRELATED failure (a confidently-wrong consensus), from 0.0 (independent, well-diversified, trustworthy majority) to 1.0 (all samples share the same suspicious table/filter/assumption — do not trust the vote). Explain which shared pattern drove the score.'
OUTPUT_SCHEMA='{"score":"A float from 0.0 (trustworthy majority) to 1.0 (likely correlated failure)","reasoning":"One or two sentences naming the shared pattern (or its absence)."}'

# type is left to its default (NULL = LLM judge); CODE templates set type='CODE'.
echo "INSERT INTO eval_templates (id, project_id, name, version, prompt, provider, model, model_params, vars, output_schema, created_at, updated_at)
      SELECT '${TID}','${PID}','correlated-vote-risk',1,\$LFJUDGE\$${JUDGE_PROMPT}\$LFJUDGE\$,
             adapter,'${EVAL_MODEL}','{}'::jsonb,ARRAY['candidates','tally'],
             '${OUTPUT_SCHEMA}'::jsonb,now(),now()
      FROM llm_api_keys WHERE project_id='${PID}' AND adapter='anthropic' LIMIT 1
      ON CONFLICT (project_id, name, version) DO UPDATE SET prompt=EXCLUDED.prompt, output_schema=EXCLUDED.output_schema, vars=EXCLUDED.vars, model=EXCLUDED.model, updated_at=now();" | pg >/dev/null
ok "eval_template 'correlated-vote-risk' created"

# ── Job configuration: score the tally-votes aggregator span ─────────────────
FILTER='[{"type":"stringOptions","value":["SPAN"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["tally-votes"],"column":"name","operator":"any of"},{"type":"stringOptions","value":["triage-support-ticket"],"column":"traceName","operator":"any of"}]'
MAPPING='[{"templateVariable":"candidates","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"candidates"},{"templateVariable":"tally","langfuseObject":"event","selectedColumnId":"metadata","jsonSelector":"votes"}]'

echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
      VALUES ('job-correlated-vote-risk','${PID}','EVAL','${TID}','correlated-vote-risk','${FILTER}'::jsonb,'event','${MAPPING}'::jsonb,1.0,0,'ACTIVE',ARRAY['NEW'],now(),now())
      ON CONFLICT (id) DO UPDATE SET eval_template_id=EXCLUDED.eval_template_id, filter=EXCLUDED.filter, variable_mapping=EXCLUDED.variable_mapping, score_name=EXCLUDED.score_name, status='ACTIVE', blocked_at=NULL, block_reason=NULL, block_message=NULL, updated_at=now();" | pg >/dev/null
ok "correlated-vote-risk → observation-level judge on tally-votes"

# Clear the worker's "no evaluators" cache so the new config picks up traffic now.
if docker ps --format '{{.Names}}' | grep -q "^${REDIS}$"; then
  docker exec "$REDIS" redis-cli del \
    "langfuse:eval:no-event-and-experiment-job-configs:${PID}" \
    "langfuse:eval:no-trace-and-dataset-job-configs:${PID}" >/dev/null 2>&1 || true
  ok "Cleared evaluator config cache"
fi

echo ""
ok "Independent judge seeded (time_scope=NEW). Regenerate triage traffic, then compare:"
echo "     deterministic (code)   consensus_margin_ok   (evaluators/consensus-margin-guard.ts)"
echo "     independent  (LLM)     correlated-vote-risk  (this script)"
echo "  View: ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PID}/evals"
echo ""

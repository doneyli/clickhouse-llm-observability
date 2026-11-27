#!/usr/bin/env bash
# Provision two managed observation-level LLM-as-a-Judge evaluators for the
# Cluster Health Investigator — an INDEPENDENT Langfuse-side check that
# complements the agent's in-graph deterministic scores (worker_count,
# plan_execution_complete) and the app-assembled delegation_quality.
#
#   diagnosis-coverage  → targets GENERATION 'synthesize-diagnosis'
#       "does the diagnosis reflect every worker finding once, each cited?"
#       (catches BAD DECOMPOSITION at the outcome level)
#   plan-scaling        → targets AGENT 'orchestrator' (its output IS the plan)
#       "is task count proportionate to symptom breadth, analyses non-overlapping?"
#       (judges the plan JSON directly)
#
# Mechanism mirrors scripts/seed-agentic-rag-evaluators.sh + seed-llm-judge-
# evaluators.sh: Postgres upsert into eval_templates + job_configurations
# (schema-coupled to the langfuse:3 image). time_scope=NEW → only NEW traces are
# scored; regenerate cluster-health traffic to see them.
#
# Reconciles: a template whose latest version already has this prompt, model, vars
# and output schema is unchanged; otherwise a new template version is added. A judge
# whose row already matches (template, score name, filter, mapping, ACTIVE, sampling,
# delay, time scope) is unchanged; otherwise it is upserted. An unchanged run writes
# nothing. Exit 1 when anything could not be seeded (no Postgres, unknown key, no eval
# model, a failed write). Self-hosted only: in cloud mode it prints the UI steps and
# exits 1, because nothing was seeded. Run it on the host; it needs `docker exec`.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$SCRIPT_DIR"
# The root .env is the only source of LANGFUSE_*: drop inherited exports first, so a
# shell key for another project cannot pick the project. scripts/ -> demo -> demos -> root.
for v in $(compgen -e | grep '^LANGFUSE_' || true); do unset "$v"; done
ROOT_ENV="../../../.env"
[ -f "$ROOT_ENV" ] && { set -a; . "$ROOT_ENV"; set +a; }
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok(){ echo -e "  ${GREEN}✓${NC} $1"; }; warn(){ echo -e "  ${YELLOW}⚠${NC} $1"; }; info(){ echo -e "  ${BLUE}ℹ${NC} $1"; }

PG="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
REDIS="${LANGFUSE_REDIS_CONTAINER:-langfuse-redis}"
EVAL_MODEL="${LANGFUSE_EVAL_MODEL:-claude-haiku-4-5-20251001}"

echo ""
echo "Provisioning managed judges for cluster-health-investigator..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
  info "DEPLOY_MODE=cloud — create these observation-level judges in the Langfuse UI:"
  info "Evaluators > New evaluator > custom template, target Observations:"
  info "  - diagnosis-coverage: filter type=GENERATION, name any-of [synthesize-diagnosis],"
  info "      tags any-of [cluster-health]; map symptom<-input.symptom, diagnosis<-output"
  info "  - plan-scaling: filter type=AGENT (or SPAN), name any-of [orchestrator],"
  info "      tags any-of [cluster-health]; map symptom<-input.symptom, plan<-output"
  warn "Nothing seeded (cloud mode)"
  exit 1
fi
FAILS=0
fail(){ warn "$1"; FAILS=$((FAILS + 1)); }

docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${PG}$" || { warn "Postgres ${PG} not reachable with docker — run this on the host after ./setup.sh"; exit 1; }
pg(){ docker exec -i "$PG" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }

[ -n "${LANGFUSE_PUBLIC_KEY:-}" ] || { warn "LANGFUSE_PUBLIC_KEY not set (root .env)"; exit 1; }
PID=$(echo "SELECT project_id FROM api_keys WHERE public_key='${LANGFUSE_PUBLIC_KEY}' LIMIT 1;" | pg)
[ -n "$PID" ] || { warn "No project owns LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY} — wrong keys in .env?"; exit 1; }
ok "Project: ${PID}"

# ── Default eval model (reuse the Anthropic connection setup.sh provisions) ──
if [ "$(echo "SELECT count(*) FROM default_llm_models WHERE project_id='${PID}';" | pg 2>/dev/null)" = "0" ]; then
  LLM_KEY_ID=$(echo "SELECT id FROM llm_api_keys WHERE project_id='${PID}' AND adapter='anthropic' LIMIT 1;" | pg)
  if [ -n "$LLM_KEY_ID" ]; then
    echo "INSERT INTO default_llm_models (id, project_id, llm_api_key_id, provider, adapter, model, model_params, created_at, updated_at)
          SELECT 'default-eval-model-${PID}','${PID}','${LLM_KEY_ID}',provider,adapter,'${EVAL_MODEL}','{}'::jsonb,now(),now()
          FROM llm_api_keys WHERE id='${LLM_KEY_ID}' ON CONFLICT (project_id) DO NOTHING;" | pg >/dev/null 2>&1 \
      && ok "Default eval model set (${EVAL_MODEL})" || fail "Could not set default eval model"
  else
    fail "No Anthropic LLM connection — run ./setup.sh first; judges stay blocked until a default eval model exists"
  fi
else
  ok "Default eval model already configured"
fi

# ── Reconcile a custom project-scoped LLM-as-judge template ─────────────────
OUTPUT_SCHEMA='{"score":"A number from 1 to 5 following the rubric","reasoning":"One sentence justifying the score"}'
# tmpl <template_id> <name> <prompt> <vars_sql_array>  → prints the id of the version to use
tmpl(){
  local tid="$1" name="$2" prompt="$3" vars="$4" p row latest same next nid
  p=$(printf "%s" "$prompt" | sed "s/'/''/g")
  row=$(echo "SELECT id || '|' || version || '|' || (prompt = '${p}' AND model = '${EVAL_MODEL}' AND provider = 'anthropic'
               AND vars = ${vars} AND output_schema = '${OUTPUT_SCHEMA}'::jsonb)::text
             FROM eval_templates WHERE project_id='${PID}' AND name='${name}' ORDER BY version DESC LIMIT 1;" | pg) \
    || { fail "could not read template ${name}" >&2; return; }
  latest="${row%%|*}"; same="${row##*|}"; next=$(( $(echo "$row" | cut -d'|' -f2 | grep -E '^[0-9]+$' || echo 0) + 1 ))
  if [ -n "$latest" ] && [ "$same" = "true" ]; then ok "unchanged template ${name}" >&2; echo "$latest"; return; fi
  nid="$tid"; [ -n "$latest" ] && nid="${tid}-v${next}"
  echo "INSERT INTO eval_templates (id, project_id, name, version, prompt, provider, model, model_params, vars, output_schema, type, created_at, updated_at)
        VALUES ('${nid}','${PID}','${name}',${next},'${p}','anthropic','${EVAL_MODEL}','{}'::jsonb, ${vars},
                '${OUTPUT_SCHEMA}'::jsonb, 'LLM_AS_JUDGE', now(), now());" | pg >/dev/null 2>&1 \
    || { fail "could not write template ${name}" >&2; return; }
  if [ -n "$latest" ]; then ok "updated template ${name} (v${next})" >&2; else ok "created template ${name}" >&2; fi
  echo "$nid"
}

# seed_judge <config_id> <template_id> <score_name> <filter_json> <mapping_json>
seed_judge(){
  local cid="$1" tid="$2" sname="$3" filt="$4" map="$5" state
  if [ -z "$tid" ]; then fail "template for ${sname} missing — judge not seeded"; return; fi
  state=$(echo "SELECT CASE WHEN eval_template_id='${tid}' AND score_name='${sname}' AND filter='${filt}'::jsonb
                  AND variable_mapping='${map}'::jsonb AND target_object='event' AND status='ACTIVE'
                  AND sampling=1.0 AND delay=0 AND time_scope::text[]=ARRAY['NEW'] AND blocked_at IS NULL
                  THEN 'same' ELSE 'drift' END FROM job_configurations WHERE id='${cid}';" | pg) \
    || { fail "could not read judge ${sname}"; return; }
  if [ "$state" = "same" ]; then ok "unchanged ${sname} → observation-level judge"; return; fi
  echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
        VALUES ('${cid}','${PID}','EVAL','${tid}','${sname}','${filt}'::jsonb,'event','${map}'::jsonb,1.0,0,'ACTIVE',ARRAY['NEW'],now(),now())
        ON CONFLICT (id) DO UPDATE SET eval_template_id=EXCLUDED.eval_template_id, score_name=EXCLUDED.score_name, filter=EXCLUDED.filter, variable_mapping=EXCLUDED.variable_mapping, target_object=EXCLUDED.target_object, sampling=EXCLUDED.sampling, delay=EXCLUDED.delay, time_scope=EXCLUDED.time_scope, status='ACTIVE', blocked_at=NULL, block_reason=NULL, block_message=NULL, updated_at=now();" | pg >/dev/null 2>&1 \
    && ok "$([ -n "$state" ] && echo updated || echo created) ${sname} → observation-level judge" || fail "Could not seed ${sname}"
}

COVERAGE_PROMPT='You grade a ClickHouse cluster diagnosis for coverage and non-duplication.

SYMPTOM: {{symptom}}
DIAGNOSIS: {{diagnosis}}

Rubric (1-5): Score 1 if the diagnosis omits findings from any executed analysis or repeats the same evidence under two claims. Score 5 if every worker finding is reflected exactly once and each claim cites its worker evidence as [worker:<analysis_type>].
Return score (1-5) and one-sentence reasoning.'

SCALING_PROMPT='You grade a ClickHouse investigation PLAN (JSON: tasks[] + reasoning) for proportionate scaling.

SYMPTOM: {{symptom}}
PLAN: {{plan}}

Rubric (1-5): Score 1 if the task count is disproportionate to the symptom breadth (a single-query complaint fanned out to many analyses, or a cluster-wide outage with only one) or two tasks share an analysis_type. Score 5 if the number of analyses is well-matched to the symptom and every analysis_type is distinct and relevant.
Return score (1-5) and one-sentence reasoning.'

T_COV=$(tmpl "chi-tmpl-diagnosis-coverage" "diagnosis-coverage" "$COVERAGE_PROMPT" "ARRAY['symptom','diagnosis']")
T_SCA=$(tmpl "chi-tmpl-plan-scaling" "plan-scaling" "$SCALING_PROMPT" "ARRAY['symptom','plan']")

seed_judge "chi-diagnosis-coverage" "$T_COV" "diagnosis-coverage" \
  '[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["synthesize-diagnosis"],"column":"name","operator":"any of"},{"type":"arrayOptions","value":["cluster-health"],"column":"tags","operator":"any of"}]' \
  '[{"templateVariable":"symptom","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"symptom"},{"templateVariable":"diagnosis","langfuseObject":"event","selectedColumnId":"output"}]'

seed_judge "chi-plan-scaling" "$T_SCA" "plan-scaling" \
  '[{"type":"stringOptions","value":["AGENT","SPAN"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["orchestrator"],"column":"name","operator":"any of"},{"type":"arrayOptions","value":["cluster-health"],"column":"tags","operator":"any of"}]' \
  '[{"templateVariable":"symptom","langfuseObject":"event","selectedColumnId":"input","jsonSelector":"symptom"},{"templateVariable":"plan","langfuseObject":"event","selectedColumnId":"output"}]'

# Clear the worker's "no evaluators" cache so new configs pick up traffic now.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${REDIS}$"; then
  docker exec "$REDIS" redis-cli del \
    "langfuse:eval:no-event-and-experiment-job-configs:${PID}" \
    "langfuse:eval:no-trace-and-dataset-job-configs:${PID}" >/dev/null 2>&1 || true
  ok "Cleared evaluator config cache"
fi

echo ""
info "View: ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PID}/evals"
if [ "$FAILS" -gt 0 ]; then warn "Managed judges INCOMPLETE: ${FAILS} failure(s)"; exit 1; fi
ok "Managed judges reconciled (time_scope=NEW). Regenerate cluster-health traffic to see scores."
echo ""

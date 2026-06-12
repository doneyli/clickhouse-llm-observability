#!/usr/bin/env bash
# Provision observation-level LLM-as-a-Judge evaluators (and upgrade legacy ones).
#
# Langfuse now recommends observation-level evaluators for live data
# (https://langfuse.com/faq/all/llm-as-a-judge-migration). Trace/dataset-target
# evaluators are marked "Legacy" in the UI. This script mirrors the upgrade
# wizard, but headless:
#   1. Ensures the project has a default evaluation model (powers the judges)
#   2. Upserts three observation-level judges over the test-scenario traffic
#      (Relevance, Correctness, Hallucination) with current tag filters
#   3. Upserts an experiment-level Hallucination judge for dataset runs
#   4. Marks legacy trace/dataset-target evaluators INACTIVE (rollback: flip
#      them back to ACTIVE in the UI — nothing is deleted)
#
# Idempotent. Self-hosted mode only; prints UI guidance in cloud mode.
#
# Usage: ./scripts/seed-llm-judge-evaluators.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
info() { echo -e "  ${BLUE}ℹ${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

[ -f .env ] && set -a && source .env && set +a

PG_CONTAINER="${LANGFUSE_POSTGRES_CONTAINER:-langfuse-postgres}"
REDIS_CONTAINER="${LANGFUSE_REDIS_CONTAINER:-langfuse-redis}"
EVAL_MODEL="${LANGFUSE_EVAL_MODEL:-claude-haiku-4-5-20251001}"

echo ""
echo "Provisioning observation-level LLM-as-a-Judge evaluators..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
    info "DEPLOY_MODE=cloud — create the judges in the Langfuse Cloud UI:"
    info "Evaluators > New evaluator > pick template, target 'Observations':"
    info "  - Relevance:     filter Tags any of [relevance-test, control], Type GENERATION"
    info "  - Correctness:   filter Tags any of [coherence-test, control], Type SPAN"
    info "                   (map ground_truth -> observation.metadata.ground_truth)"
    info "  - Hallucination: filter Tags any of [hallucination-test, control], Type GENERATION"
    info "Legacy trace-level evaluators can be upgraded with the built-in wizard."
    exit 0
fi

docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$" \
    || fail "Container ${PG_CONTAINER} is not running. Start the stack with ./setup.sh first."

psql_exec() {
    docker exec -i "$PG_CONTAINER" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

PROJECT_ID=$(echo "SELECT project_id FROM api_keys WHERE public_key = '${LANGFUSE_PUBLIC_KEY:-pk-lf-1234567890}' LIMIT 1;" | psql_exec)
PROJECT_ID="${PROJECT_ID:-demo-project}"
ok "Project: ${PROJECT_ID}"

# ─── 1. Default evaluation model ─────────────────────────────────────────────
# Judges need a default eval model. Reuse the Anthropic LLM connection that
# setup.sh provisions via the public API.
HAS_DEFAULT=$(echo "SELECT count(*) FROM default_llm_models WHERE project_id = '${PROJECT_ID}';" | psql_exec)
if [ "$HAS_DEFAULT" = "0" ]; then
    LLM_KEY_ID=$(echo "SELECT id FROM llm_api_keys WHERE project_id = '${PROJECT_ID}' AND adapter = 'anthropic' LIMIT 1;" | psql_exec)
    if [ -n "$LLM_KEY_ID" ]; then
        echo "INSERT INTO default_llm_models (id, project_id, llm_api_key_id, provider, adapter, model, model_params, created_at, updated_at)
              SELECT 'default-eval-model-${PROJECT_ID}', '${PROJECT_ID}', '${LLM_KEY_ID}', provider, adapter, '${EVAL_MODEL}', '{}'::jsonb, now(), now()
              FROM llm_api_keys WHERE id = '${LLM_KEY_ID}'
              ON CONFLICT (project_id) DO NOTHING;" | psql_exec > /dev/null
        ok "Default evaluation model set (${EVAL_MODEL})"
    else
        warn "No Anthropic LLM connection found — run ./setup.sh first; judges will stay blocked until a default eval model exists"
    fi
else
    ok "Default evaluation model already configured"
fi

# ─── 2. Resolve managed judge templates ──────────────────────────────────────
template_id() {
    # Prefer the template a legacy evaluator already used; else newest managed template by name.
    local name="$1"
    local tid
    tid=$(echo "SELECT jc.eval_template_id FROM job_configurations jc
                JOIN eval_templates et ON et.id = jc.eval_template_id
                WHERE jc.project_id = '${PROJECT_ID}' AND jc.target_object IN ('trace','dataset')
                  AND et.name = '${name}' AND jc.eval_template_id IS NOT NULL
                ORDER BY jc.created_at DESC LIMIT 1;" | psql_exec)
    if [ -z "$tid" ]; then
        tid=$(echo "SELECT id FROM eval_templates
                    WHERE project_id IS NULL AND name = '${name}'
                      AND type = 'LLM_AS_JUDGE' AND coalesce(partner,'') = ''
                    ORDER BY version DESC LIMIT 1;" | psql_exec)
    fi
    echo "$tid"
}

# seed_judge <config_id> <template_name> <score_name> <target> <filter_json> <mapping_json>
seed_judge() {
    local config_id="$1" template_name="$2" score_name="$3" target="$4" filter_json="$5" mapping_json="$6"
    local tid
    tid=$(template_id "$template_name")
    if [ -z "$tid" ]; then
        warn "No '${template_name}' template found — skipped (create it once in the UI, then re-run)"
        return
    fi
    echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)
          VALUES ('${config_id}', '${PROJECT_ID}', 'EVAL', '${tid}', '${score_name}', '${filter_json}'::jsonb, '${target}', '${mapping_json}'::jsonb, 1.0, 0, 'ACTIVE', ARRAY['NEW'], now(), now())
          ON CONFLICT (id) DO UPDATE SET eval_template_id = EXCLUDED.eval_template_id, filter = EXCLUDED.filter, variable_mapping = EXCLUDED.variable_mapping, status = 'ACTIVE', blocked_at = NULL, block_reason = NULL, block_message = NULL, updated_at = now();" \
        | psql_exec > /dev/null
    ok "${score_name} → observation-level (${target})"
}

MAP_QG='[{"templateVariable":"query","langfuseObject":"event","selectedColumnId":"input"},{"templateVariable":"generation","langfuseObject":"event","selectedColumnId":"output"}]'
MAP_QGT='[{"templateVariable":"query","langfuseObject":"event","selectedColumnId":"input"},{"templateVariable":"generation","langfuseObject":"event","selectedColumnId":"output"},{"templateVariable":"ground_truth","langfuseObject":"event","selectedColumnId":"metadata","jsonSelector":"ground_truth"}]'

# Live judges over test-scenario traffic. Each watches its failure category
# plus the control group, so demos show low AND high scores side by side.
# (Legacy filters referenced tags that no longer exist in test-scenarios.)
seed_judge "obs-eval-relevance" "Relevance" "Relevance" "event" \
    '[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"arrayOptions","value":["relevance-test","control"],"column":"tags","operator":"any of"}]' \
    "$MAP_QG"

seed_judge "obs-eval-correctness" "Correctness" "Correctness" "event" \
    '[{"type":"stringOptions","value":["SPAN"],"column":"type","operator":"any of"},{"type":"arrayOptions","value":["coherence-test","control"],"column":"tags","operator":"any of"}]' \
    "$MAP_QGT"

seed_judge "obs-eval-hallucination" "Hallucination" "Hallucination" "event" \
    '[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"arrayOptions","value":["hallucination-test","control"],"column":"tags","operator":"any of"}]' \
    "$MAP_QG"

# Experiment judge: scores model outputs of runs against coding-assistant-quality
QUALITY_DS=$(echo "SELECT id FROM datasets WHERE project_id = '${PROJECT_ID}' AND name = 'coding-assistant-quality' LIMIT 1;" | psql_exec)
if [ -n "$QUALITY_DS" ]; then
    seed_judge "obs-eval-hallucination-experiment" "Hallucination" "Hallucination" "experiment" \
        '[{"type":"stringOptions","value":["'"$QUALITY_DS"'"],"column":"experimentDatasetId","operator":"any of"}]' \
        "$MAP_QG"
else
    warn "Dataset coding-assistant-quality not found — run 'python scripts/seed-datasets.py' then re-run this script"
fi

# ─── 3. Deactivate legacy trace/dataset evaluators ───────────────────────────
DEACTIVATED=$(echo "UPDATE job_configurations SET status = 'INACTIVE', updated_at = now()
                    WHERE project_id = '${PROJECT_ID}' AND target_object IN ('trace','dataset')
                      AND status = 'ACTIVE' RETURNING id;" | psql_exec | wc -l | tr -d ' ')
if [ "$DEACTIVATED" != "0" ]; then
    ok "Deactivated ${DEACTIVATED} legacy trace/dataset evaluator(s) (kept for rollback)"
else
    ok "No active legacy evaluators left"
fi

# Clear the worker's "no evaluators" cache so new configs pick up traffic now
if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
    docker exec "$REDIS_CONTAINER" redis-cli del \
        "langfuse:eval:no-event-and-experiment-job-configs:${PROJECT_ID}" \
        "langfuse:eval:no-trace-and-dataset-job-configs:${PROJECT_ID}" > /dev/null 2>&1 || true
    ok "Cleared evaluator config cache"
fi

echo ""
ok "LLM-as-a-Judge evaluators are observation-level. View: ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PROJECT_ID}/evals"
info "Generate scored traffic: docker compose --profile tools run --rm test-scenarios"
echo ""

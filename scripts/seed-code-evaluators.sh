#!/usr/bin/env bash
# Provision Langfuse code evaluators (deterministic TypeScript evals).
#
# Code evaluators have no public API yet (Fast Preview, UI-only), so this
# script seeds them directly into the Langfuse Postgres database — the same
# rows the UI's "New evaluator > Code" flow creates:
#   - eval_templates        (type=CODE, source from evaluators/*.ts)
#   - job_configurations    (target: live observations or experiments)
#
# Idempotent: re-running updates source code and re-activates evaluators.
# Self-hosted mode only. In cloud mode it prints UI instructions instead.
#
# Usage: ./scripts/seed-code-evaluators.sh

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
EVALUATORS_DIR="evaluators"

echo ""
echo "Seeding Langfuse code evaluators..."

if [ "${DEPLOY_MODE:-self-hosted}" = "cloud" ]; then
    info "DEPLOY_MODE=cloud — code evaluators must be created in the Langfuse Cloud UI."
    info "Open Evaluators > New evaluator > Code and paste the sources from:"
    for f in "$EVALUATORS_DIR"/*.ts; do
        info "  - $f"
    done
    info "See docs/CODE_EVALUATORS.md for the target/filter to use per evaluator."
    exit 0
fi

docker ps --format '{{.Names}}' | grep -q "^${PG_CONTAINER}$" \
    || fail "Container ${PG_CONTAINER} is not running. Start the stack with ./setup.sh first."

psql_exec() {
    docker exec -i "$PG_CONTAINER" sh -c 'psql -v ON_ERROR_STOP=1 -q -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}

# Resolve the project that owns the configured API keys
PROJECT_ID=$(echo "SELECT project_id FROM api_keys WHERE public_key = '${LANGFUSE_PUBLIC_KEY:-pk-lf-1234567890}' LIMIT 1;" | psql_exec)
PROJECT_ID="${PROJECT_ID:-demo-project}"
ok "Project: ${PROJECT_ID}"

dataset_id() {
    echo "SELECT id FROM datasets WHERE project_id = '${PROJECT_ID}' AND name = '$1' LIMIT 1;" | psql_exec
}

# seed_evaluator <name> <score_name> <target: event|experiment> <filter_json> <extra_vars: 0|1>
seed_evaluator() {
    local name="$1" score_name="$2" target="$3" filter_json="$4" experiment_vars="$5"
    local src_file="${EVALUATORS_DIR}/${name}.ts"
    [ -f "$src_file" ] || { warn "Missing ${src_file} — skipped"; return; }

    local template_id="code-eval-${name}"
    local job_id="code-eval-job-${name}"
    local mapping='[{"templateVariable":"input","langfuseObject":"event","selectedColumnId":"input"},{"templateVariable":"output","langfuseObject":"event","selectedColumnId":"output"},{"templateVariable":"metadata","langfuseObject":"event","selectedColumnId":"metadata"}'
    local vars="ARRAY['input','output','metadata']"
    if [ "$experiment_vars" = "1" ]; then
        mapping+=',{"templateVariable":"experimentItemExpectedOutput","langfuseObject":"event","selectedColumnId":"experimentItemExpectedOutput"},{"templateVariable":"experimentItemMetadata","langfuseObject":"event","selectedColumnId":"experimentItemMetadata"}'
        vars="ARRAY['input','output','metadata','experimentItemExpectedOutput','experimentItemMetadata']"
    fi
    mapping+=']'

    # Dollar-quote the TypeScript source; bail out if the delimiter ever
    # appears in an evaluator file.
    if grep -q 'LFCODEEVAL' "$src_file"; then
        fail "${src_file} contains the SQL quoting delimiter LFCODEEVAL"
    fi

    {
        echo "INSERT INTO eval_templates (id, project_id, name, version, vars, type, source_code, source_code_language, created_at, updated_at)"
        echo "VALUES ('${template_id}', '${PROJECT_ID}', '${name}', 1, ${vars}, 'CODE', \$LFCODEEVAL\$"
        cat "$src_file"
        echo "\$LFCODEEVAL\$, 'TYPESCRIPT', now(), now())"
        echo "ON CONFLICT (project_id, name, version) DO UPDATE SET source_code = EXCLUDED.source_code, vars = EXCLUDED.vars, updated_at = now();"
        echo "INSERT INTO job_configurations (id, project_id, job_type, eval_template_id, score_name, filter, target_object, variable_mapping, sampling, delay, status, time_scope, created_at, updated_at)"
        echo "VALUES ('${job_id}', '${PROJECT_ID}', 'EVAL', '${template_id}', '${score_name}', '${filter_json}'::jsonb, '${target}', '${mapping}'::jsonb, 1.0, 0, 'ACTIVE', ARRAY['NEW'], now(), now())"
        echo "ON CONFLICT (id) DO UPDATE SET filter = EXCLUDED.filter, variable_mapping = EXCLUDED.variable_mapping, score_name = EXCLUDED.score_name, status = 'ACTIVE', blocked_at = NULL, block_reason = NULL, block_message = NULL, updated_at = now();"
    } | psql_exec > /dev/null

    ok "${name} (${target}: ${score_name})"
}

# ─── Live-traffic evaluators (target: observations at ingest) ───────────────

seed_evaluator "sql-safety-guard" "sql-risk" "event" \
    '[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["text-to-sql"],"column":"traceName","operator":"any of"}]' 0

seed_evaluator "credential-leak-guard" "credential-leak" "event" \
    '[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"}]' 0

seed_evaluator "response-structure-check" "structure-clean" "event" \
    '[{"type":"stringOptions","value":["GENERATION"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["text-to-sql","vector-rag"],"column":"traceName","operator":"any of"}]' 0

# Support Triage Parallel (Pattern #3): deterministic margin check on the
# `tally-votes` aggregator span — reads the vote tally the app writes onto
# metadata (no need to pull in the N candidate child observations).
seed_evaluator "consensus-margin-guard" "consensus_margin_ok" "event" \
    '[{"type":"stringOptions","value":["tally-votes"],"column":"name","operator":"any of"},{"type":"stringOptions","value":["triage-support-ticket"],"column":"traceName","operator":"any of"}]' 0
# slow-query-tuner (Pattern #7): flag runs that ended on a backstop instead of
# self-terminating. Targets the AGENT root observation of tune-clickhouse-query
# traces; emits cap_terminated (BOOLEAN) + termination_class (CATEGORICAL).
seed_evaluator "runaway-loop-guard" "cap-terminated" "event" \
    '[{"type":"stringOptions","value":["AGENT"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["tune-clickhouse-query"],"column":"traceName","operator":"any of"}]' 0
# Gate verdicts on the text-to-sql chain: score every gate-* SPAN (incl. each
# retry attempt) with a gate-pass boolean, so the average is the true gate pass
# rate. Non-gate spans (retrieve-context, ...) carry no verdict and yield no score.
seed_evaluator "chain-gate-check" "gate-pass" "event" \
    '[{"type":"stringOptions","value":["SPAN"],"column":"type","operator":"any of"},{"type":"stringOptions","value":["text-to-sql"],"column":"traceName","operator":"any of"}]' 0

# ─── Experiment evaluators (target: dataset experiment runs) ────────────────

QUALITY_DS=$(dataset_id "coding-assistant-quality")
if [ -n "$QUALITY_DS" ]; then
    seed_evaluator "quality-structure-check" "keyword-coverage" "experiment" \
        '[{"type":"stringOptions","value":["'"$QUALITY_DS"'"],"column":"experimentDatasetId","operator":"any of"}]' 1
else
    warn "Dataset coding-assistant-quality not found — run 'python scripts/seed-datasets.py' then re-run this script"
fi

SECURITY_DS=$(dataset_id "coding-assistant-security")
if [ -n "$SECURITY_DS" ]; then
    seed_evaluator "security-behavior-check" "security-compliant" "experiment" \
        '[{"type":"stringOptions","value":["'"$SECURITY_DS"'"],"column":"experimentDatasetId","operator":"any of"}]' 1
else
    warn "Dataset coding-assistant-security not found — run 'python scripts/seed-datasets.py' then re-run this script"
fi

# query-router: deterministic exact-match routing accuracy on experiment runs of
# the router-accuracy dataset (the soft/ambiguous cases go to the categorical
# route-plausibility LLM judge — scripts/seed-router-judge.sh).
ROUTER_DS=$(dataset_id "query-router-accuracy")
if [ -n "$ROUTER_DS" ]; then
    seed_evaluator "route-match" "route-match" "experiment" \
        '[{"type":"stringOptions","value":["'"$ROUTER_DS"'"],"column":"experimentDatasetId","operator":"any of"}]' 1
else
    warn "Dataset query-router-accuracy not found — run 'python scripts/seed-router-dataset.py' then re-run this script"
fi

# The worker caches "project has no event/experiment evaluators" in Redis for
# 10 minutes; clear it so freshly seeded evaluators pick up traffic right away.
if docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
    docker exec "$REDIS_CONTAINER" redis-cli del \
        "langfuse:eval:no-event-and-experiment-job-configs:${PROJECT_ID}" \
        "langfuse:eval:no-trace-and-dataset-job-configs:${PROJECT_ID}" > /dev/null 2>&1 || true
    ok "Cleared evaluator config cache"
fi

echo ""
ok "Code evaluators provisioned. View them at ${LANGFUSE_BASE_URL:-http://localhost:3001}/project/${PROJECT_ID}/evals"
info "Live evaluators score new traces automatically (run a demo app to see scores)."
info "Experiment evaluators score runs of scripts/run-experiments.py."
echo ""

#!/usr/bin/env bash
# Provision Langfuse-managed LLM-as-a-Judge evaluators for the demo's project
# (default 'real-estate', override via LANGFUSE_PROJECT_NAME) so they run
# AUTOMATICALLY on live traffic (visible under Evaluators).
#
# These are Langfuse-native evaluators (not the client-side judges the demo also
# ships): the Langfuse worker runs them using the Anthropic LLM connection and
# writes scores back, with no evaluator code in our app.
#
# BOTH modes provision the judges automatically — there is no manual UI step in
# either. They differ in mechanism, and in two consequences worth knowing:
#
#   self-hosted (localhost)          [implemented below]
#     The stable REST API does not expose `job_configurations`, so — like this
#     repo's other evaluator seeders — we INSERT into the Langfuse Postgres
#     directly (also needs a `default_llm_models` row). Trace-level, filtered by
#     tag `real-estate`. Scores NEW *and* EXISTING matching traces.
#
#   remote / Langfuse Cloud          [delegated to seed_managed_evaluators_cloud.py]
#     No DB access, so it all happens over HTTP against the STABLE evaluator API
#     (`/api/public/v2/evaluators` + `/api/public/v2/evaluation-rules`).
#     Observation-level. Scores NEW traffic only — a trace ingested before the
#     rules existed gets nothing, so backfill it from the UI:
#     Traces -> select -> Actions -> Evaluate.
#
#     This used to POST to `/api/public/unstable/evaluation-rules`. Those
#     endpoints were REMOVED from Langfuse Cloud on 2026-09-04 and now 404, and
#     because this script degrades to "warn + print a UI recipe + exit 0", a
#     fresh project came out of run_demo.sh with NO judges while the run still
#     looked green. The port to v2 is not a rename — the request bodies,
#     idempotency model and rule->evaluator linkage all changed. The Python
#     module's docstring is the record of what changed and why.
#
# Self-hosted is idempotent by `ON CONFLICT (id) DO UPDATE`. The Cloud path goes
# further and RECONCILES: a rule whose filter has drifted is PATCHed back rather
# than skipped, because a silently-unmatched rule is exactly how the
# conversation-level judge stopped scoring for 20 days unnoticed.
#
# Usage:  ./scripts/seed_managed_evaluators.sh
#         (Cloud flags pass straight through, e.g. --dry-run, --no-reconcile)
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
# There is no DB to write, so the whole remote path lives in Python, where the
# three judge definitions and their JSON request bodies are readable and the
# prompt text does not need three levels of shell escaping. Exit code is passed
# through: non-zero only for the project guard (refusing to write to the wrong
# project); an API failure still exits 0 after printing a UI fallback, so
# run_demo.sh keeps flowing.
case "${LANGFUSE_HOST:-http://localhost:3001}" in
  http://localhost*|https://localhost*|http://127.0.0.1*|https://127.0.0.1*)
    ;;  # self-hosted: fall through to DB seeding
  *)
    echo "Remote Langfuse host detected (${LANGFUSE_HOST}) — seeding over the v2 evaluator API."
    PY_BIN="./.venv/bin/python"
    [ -x "$PY_BIN" ] || PY_BIN="$(command -v python3 || true)"
    if [ -z "$PY_BIN" ]; then
      echo "No Python found (tried ./.venv/bin/python and python3)." >&2
      exit 1
    fi
    exec "$PY_BIN" scripts/seed_managed_evaluators_cloud.py "$@"
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
#
# ⚠️ MIGRATION DEBT — `target_object='trace'` is DEPRECATED in Langfuse v4, and these
# two judges read TRACE-level input/output. The agent no longer writes those
# explicitly: `set_current_trace_io()` was removed from agent/concierge.py during the
# v4 migration, because v4 derives a trace's input/output from its ROOT observation
# (which `root.update()` sets). That derivation is what keeps these judges fed today.
#
# Do NOT "fix" a quiet judge by re-adding `set_current_trace_io()`. The supported
# successor is an observation-level rule, and this server (3.221.1) already accepts
# them in the DB — `job_configurations.target_object='observation'`, which
# scripts/../../scripts/seed-llm-judge-evaluators.sh already writes for the main
# stack. Porting these two to that shape is the real fix; until then they stay on
# the deprecated target.
#
# There is a hard date on this: trace-level evaluators stop producing results on
# Langfuse Cloud after 2026-11-16. This path is self-hosted-only so the date does
# not apply to it directly, but it does apply the moment the stack moves to v4 —
# and it is the reason the Cloud path in seed_managed_evaluators_cloud.py is
# observation-level throughout.
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

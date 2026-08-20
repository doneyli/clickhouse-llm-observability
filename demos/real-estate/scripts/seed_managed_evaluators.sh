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
#   self-hosted (localhost)
#     The stable REST API does not expose `job_configurations`, so — like this
#     repo's other evaluator seeders — we INSERT into the Langfuse Postgres
#     directly (also needs a `default_llm_models` row). Trace-level, filtered by
#     tag `real-estate`. Scores NEW *and* EXISTING matching traces.
#
#   remote / Langfuse Cloud
#     No DB access, so we upsert the Anthropic LLM connection and then create the
#     two judges via the UNSTABLE evaluation-rules API
#     (POST /api/public/unstable/evaluation-rules), referencing the
#     Langfuse-managed evaluator families. Observation-level, filtered to the root
#     span `handle-concierge-chat-message`. Scores NEW traffic only — a trace
#     ingested before the rules existed gets nothing, so backfill it from the UI:
#     Traces -> select -> Actions -> Evaluate.
#     The printed UI recipe is a FALLBACK, shown only if that API call fails.
#
# Both are idempotent: existing rules/configs are detected and left alone.
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
# There is no DB to write, so we do it all over HTTP: upsert the Anthropic LLM
# connection, then create both judges via the UNSTABLE evaluation-rules API. The
# UI recipe printed at the end is a FALLBACK for when that API is unavailable —
# not the normal path. Exit 0 either way so run_demo.sh keeps flowing.
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
    # Create the two judges as observation-level evaluation rules via the
    # (unstable) evaluators API, referencing the Langfuse-MANAGED evaluator
    # families. Each turn is its own trace rooted at `handle-concierge-chat-message`, so the
    # rules run on that root span (input={"query"} / output=final answer) — the
    # scores read like the self-hosted trace-level ones. Idempotent: existing
    # rule names are skipped. Falls back to a UI recipe if the API is unavailable.
    #
    # The second filter is `isRootObservation`, NOT `name`: matching the root by
    # name means a rename of TRACE_NAME silently stops the judges firing without
    # any error anywhere. That exact drift had already happened once — the live
    # Cloud rules were still filtering on the long-gone `property-concierge` /
    # `turn-N` names and had scored nothing for weeks. `traceName` stays as the
    # scope guard so the rules ignore experiment and probe traffic.
    #
    # NOTE: this "already present" check skips a rule whose FILTER has drifted.
    # It will not repair one — check the rule in the UI if judges go quiet.
    rules=$(curl -s -m 20 -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" \
      "${LANGFUSE_HOST}/api/public/unstable/evaluation-rules" || true)
    api_failed=0
    for judge in Helpfulness Relevance; do
      if printf '%s' "$rules" | grep -q "\"name\":\"${judge}\""; then
        green "${judge} rule already present"
        continue
      fi
      body=$(cat <<RULE
{
  "name": "${judge}",
  "evaluator": {"name": "${judge}", "scope": "managed"},
  "target": "observation",
  "enabled": true,
  "sampling": 1,
  "filter": [
    {"type": "stringOptions", "column": "traceName", "operator": "any of",
     "value": ["handle-concierge-chat-message"]},
    {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": true}
  ],
  "mapping": [
    {"variable": "query", "source": "input", "jsonPath": "\$.query"},
    {"variable": "generation", "source": "output"}
  ]
}
RULE
)
      code=$(curl -s -m 20 -o /tmp/lf-rule.json -w '%{http_code}' -X POST \
        -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" \
        -H 'Content-Type: application/json' \
        "${LANGFUSE_HOST}/api/public/unstable/evaluation-rules" -d "$body") || code="000"
      if [ "$code" = "200" ] || [ "$code" = "201" ]; then
        green "${judge} (managed judge, observation-level) created via API"
      else
        warn "${judge} rule failed (HTTP ${code}) — set it up in the UI (see below)"
        api_failed=1
      fi
    done
    if [ "$api_failed" = "1" ]; then
      cat <<STEPS

  Finish in the Langfuse UI (~2 min), project '${EXPECTED_PROJECT}':
    1. Settings > LLM Connections — confirm the 'anthropic' connection exists.
    2. Evaluators > + New evaluator, twice — managed templates 'Helpfulness'
       and 'Relevance', target = live observations, filter traceName any of
       [handle-concierge-chat-message] + name any of [handle-concierge-chat-message],
       mapping query -> input (\$.query), generation -> output.
STEPS
    else
      echo ""
      echo "  Judges score NEW traffic automatically (allow a few minutes)."
      echo "  To score EXISTING traces: Traces table > select > Actions > Evaluate."
    fi
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
#
# ⚠️ MIGRATION DEBT — `target_object='trace'` is DEPRECATED in Langfuse v4, and these
# two judges read TRACE-level input/output. The agent no longer writes those
# explicitly: `set_current_trace_io()` was removed from agent/concierge.py during the
# v4 migration, because v4 derives a trace's input/output from its ROOT observation
# (which `root.update()` sets). That derivation is what keeps these judges fed today.
#
# Do NOT "fix" a quiet judge by re-adding `set_current_trace_io()`. The supported
# successor is an observation-level rule, and this server (3.221.1) already accepts
# them — the remote branch above builds exactly that payload via
# /api/public/unstable/evaluation-rules with "target": "observation". Porting these two
# to that shape is the real fix; until then they stay on the deprecated target.
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

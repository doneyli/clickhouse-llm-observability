#!/bin/sh
# Rename LibreChat's Langfuse traces to "LibreChat" and tag them 'librechat' so
# they read cleanly and are filterable alongside text-to-sql and vector-rag traces.
#
# LibreChat's @librechat/agents creates Langfuse CallbackHandlers with a hardcoded
# runName of 'AgentRun' and no tags. The LangGraph run config's 'runName' becomes
# the Langfuse trace name, and 'tags' is merged into trace tags. This patches the
# controller files to rename the run to 'LibreChat' and add tags: ['librechat'].
# (No evaluator filters on the trace name — the managed judges key on tags /
# observation names — so the rename does not affect scoring.)

sed -i "s/runName: 'AgentRun',/runName: 'LibreChat', tags: ['librechat'],/g" \
    /app/api/server/controllers/agents/client.js \
    /app/api/server/controllers/agents/openai.js \
    /app/api/server/controllers/agents/responses.js \
    /app/packages/api/dist/index.js \
    2>/dev/null || true

exec node api/server/index.js

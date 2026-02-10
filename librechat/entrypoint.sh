#!/bin/sh
# Add 'librechat' tag to Langfuse traces so they're filterable alongside
# text-to-sql and vector-rag demo traces.
#
# LibreChat's @librechat/agents creates Langfuse CallbackHandlers without tags.
# The LangGraph run config supports a 'tags' field that gets merged into trace tags.
# This patches the controller files to include tags: ['librechat'] in the run config.

sed -i "s/runName: 'AgentRun',/runName: 'AgentRun', tags: ['librechat'],/g" \
    /app/api/server/controllers/agents/client.js \
    /app/api/server/controllers/agents/openai.js \
    /app/api/server/controllers/agents/responses.js \
    /app/packages/api/dist/index.js \
    2>/dev/null || true

exec node api/server/index.js

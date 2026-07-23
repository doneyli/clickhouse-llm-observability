---
name: troubleshoot
description: Diagnose and fix problems with the LLM observability stack — services not starting, traces not appearing, missing scores, agents without MCP tools, 401 errors, port conflicts. Use when anything in the demo stack is broken, unhealthy, or behaving unexpectedly.
---

# Troubleshoot the Stack

Diagnose before acting. Most problems resolve with a targeted fix plus an idempotent
`./setup.sh` re-run — destructive resets are the last resort, never the first.

## Triage order

1. **The built-in checklist tells you what's broken:**

   ```bash
   ./setup.sh --status        # readiness checklist — note every ✗ line
   docker compose --profile langfuse --profile demo --profile dashboard ps   # container states
   ```

2. **Probe the failing service directly:**

   ```bash
   curl -sf http://localhost:3001/api/public/health   # Langfuse
   curl -sf http://localhost:3080/health              # LibreChat
   curl -sf http://localhost:8005/health              # LLM Observatory dashboard (if profile is up)
   ```

   The demo apps (text-to-sql, vector-rag, test-scenarios) are run-on-demand
   containers, not long-running services — "is it healthy" means "does
   `docker compose run --rm text-to-sql python main.py` exit 0 and produce a trace".

3. **Read its logs** (last 50 lines is usually enough):

   ```bash
   docker compose --profile langfuse logs langfuse-web --tail 50
   docker compose logs api --tail 50                  # LibreChat
   docker compose logs langfuse-worker --tail 50      # evaluator/score issues
   ```

4. **Match against known failures** — check the table in
   [AGENTS.md](../../../AGENTS.md#troubleshooting) first. The highest-frequency ones:

   | Symptom | Cause → Fix |
   |---|---|
   | 401s from SDK/CLI, traces silently missing | Shell-exported `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` override `.env`. `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY` and retry. |
   | LibreChat agents have no MCP tools | MCP servers init async. Wait 30s, re-run `./scripts/seed-librechat-agents.sh` (idempotent). |
   | *Prompt Engineer* / *LLM Ops* agent missing prompt tools, or `langfuse-prompts` MCP returns 403 | Self-hosted `/api/public/mcp` validates the `Host` header against `NEXTAUTH_URL` (`localhost:3001`) but LibreChat connects as `langfuse-web:3000`. Ensure `LANGFUSE_MCP_ALLOWED_HOSTS=langfuse-web:3000` is set on `langfuse-web` (needs langfuse image ≥ v3.18x; pinned to `v3.221.1`), then `docker compose --profile langfuse up -d langfuse-web`. |
   | LibreChat agent chat shows raw `<function_calls>` / `<invoke>` / `<tool_call>` XML as text | Agent's MCP tools didn't bind → Claude role-plays the call as text (and may hallucinate the results). See [Agent emits raw tool-call XML](#agent-emits-raw-tool-call-xml) below. |
   | Langfuse not ready after ~2 min | Slow first-boot migrations. Re-run `./setup.sh`. |
   | Traces arrive but no judge scores | Check LLM connection exists (Project Settings > LLM Connections) and `langfuse-worker` logs; evaluators need ~30–60s. |
   | Multi-turn agentic-rag chat in LibreChat doesn't group into one Langfuse Session (each turn a fresh random session) | LibreChat isn't forwarding the conversation-id header. Most common after pulling this change: `api` runs **stale config** (`librechat.yaml` is read only at startup) → `docker compose up -d --force-recreate api` (plain `up -d` skips mounted-file edits; plain `docker restart` re-applies the entrypoint sed twice). Also confirm `librechat.yaml`'s `rag-retriever` block still has `headers.X-LibreChat-Conversation-Id: "{{LIBRECHAT_BODY_CONVERSATIONID}}"`, and rebuild `mcp-rag-retriever` (`docker compose --profile demo up -d --build mcp-rag-retriever`) after any `server.py`/`mcp` bump. The header read falls back **silently** to a random session (never errors). Then start a **new** conversation. |
   | Port conflict | Change the port in `.env` (e.g. `LANGFUSE_PORT`), re-run `./setup.sh`. |

5. **Deeper integration validation** when traces/scores misbehave but containers look
   healthy:

   ```bash
   ./scripts/validate-langfuse.sh
   ```

6. **Evaluator provisioning** (self-hosted only) is verifiable in Postgres:

   ```bash
   docker exec langfuse-postgres psql -U langfuse -d langfuse -t \
     -c "SELECT id, status FROM job_configurations WHERE id LIKE 'code-eval%' OR id LIKE 'obs-eval%'"
   # expect 5 code-eval* + 4 obs-eval* rows, all ACTIVE
   ```

   Re-provision with `./scripts/seed-code-evaluators.sh` /
   `./scripts/seed-llm-judge-evaluators.sh` (both idempotent).

## Agent emits raw tool-call XML

**Symptom:** a LibreChat agent (e.g. *ClickHouse Data Analyst*) answers with blocks of
literal markup instead of LibreChat's collapsible tool cards:

```
<function_calls>
<invoke name="...">
<parameter name="query">SHOW DATABASES</parameter>
...
<function_response>{...}</function_response>
```

(Older shape: `<tool_call>{"name": "...", "arguments": {...}}</tool_call>`.) The
"results" may look plausible but are **hallucinated** — the tool never actually ran.

**Root cause:** the agent's MCP tools were **not bound** for that request, so no `tools`
were sent to the Anthropic API. Claude — instructed to use tools — then writes the
tool-call syntax (and a fake response) as plain *text*, which LibreChat renders verbatim.
This is **not** a model, model-id, version, or permission bug. Confirm in a Langfuse
trace: the `LibreChat` generation has **no `tools` in its input** and there are **no
tool-execution spans**.

Tools fail to bind when, at chat time, the MCP server wasn't connected, or the agent was
**seeded before its tools were available** (so it has only the `sys__server__sys_mcp_*`
stub or an empty/stale tool set). Maintainer note for the underlying LibreChat behaviour:
[danny-avila/LibreChat #10351](https://github.com/danny-avila/LibreChat/discussions/10351).

**Fix (in order):**

1. Confirm the MCP server is healthy and advertising tools:
   ```bash
   docker compose ps mcp-clickhouse                       # must be Up
   # log in as demo@example.com / demodemo1! to get $TOKEN, then:
   curl -sf -H "Authorization: Bearer $TOKEN" http://localhost:3080/api/mcp/tools \
     | jq '.servers | to_entries[] | "\(.key): \(.value.tools|length) tool(s)"'
   ```
2. Re-sync the agents' tool bindings (now repairs existing agents, not just new ones):
   ```bash
   ./scripts/seed-librechat-agents.sh
   ```
   Look for `↻ <agent> (tools re-synced …)`. Verify in Mongo:
   ```bash
   docker exec librechat-mongodb mongosh LibreChat --quiet --eval \
     'printjson(db.agents.findOne({name:"ClickHouse Data Analyst"},{tools:1,_id:0}))'
   # expect individual *_mcp_clickhouse-playground keys, not just the server stub
   ```
3. **Start a brand-new conversation.** LibreChat caches tool state per conversation;
   an old thread that started broken stays broken.

## Recovery ladder (least → most destructive)

1. Targeted fix from the table above, then `./setup.sh` (idempotent, safe).
2. Restart one service: `docker compose restart <service>`.
3. `./setup.sh --cleanup` then `./setup.sh` (stops containers, keeps data).
4. `./scripts/reset.sh` then `./setup.sh --seed` — **destroys all data; get explicit
   user confirmation first.**

After any fix, confirm with `./setup.sh --status` — done means every readiness line ✓.

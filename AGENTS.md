# Agent Deployment Runbook

This file is for AI coding agents (Claude Code, Codex, Cursor, etc.). If a user says
**"Deploy this demo"**, follow this runbook exactly. Every step is non-interactive and
verifiable. For code conventions and architecture, see [CLAUDE.md](CLAUDE.md).

## Beyond deployment

This repo ships project skills in [`.agents/skills/`](.agents/skills/) (symlinked into
`.claude/skills/` for Claude Code) covering the full lifecycle. If your harness
supports skills, prefer them; otherwise open the SKILL.md files and follow them as
runbooks:

| User intent | Skill / runbook |
|---|---|
| "Deploy this demo" | [`deploy-demo`](.agents/skills/deploy-demo/SKILL.md) — wraps this file |
| "Run / present / prep the demo" | [`run-demo`](.agents/skills/run-demo/SKILL.md) — pre-flight, fresh data, act-by-act guidance |
| "Something is broken" | [`troubleshoot`](.agents/skills/troubleshoot/SKILL.md) — triage order + recovery ladder |
| Query Langfuse data / docs | [`langfuse`](.agents/skills/langfuse/SKILL.md) — CLI + documentation access |

For presenting to humans, the source material is [docs/SA_FIELD_GUIDE.md](docs/SA_FIELD_GUIDE.md)
(audience/talk track), [docs/USE_CASES.md](docs/USE_CASES.md) (2-minute demo paths),
and the runbooks in [docs/](docs/README.md).

## What gets deployed

A self-hosted LLM observability stack (~12 Docker containers): Langfuse (traces UI,
backed by ClickHouse), LibreChat (chat UI with 5 pre-configured agents), demo apps
(Text-to-SQL, Vector RAG, Agentic RAG), and MCP servers. Setup is fully automated —
Langfuse org/project/API keys, the Langfuse LLM connection, LibreChat secrets, the
demo user, and the LibreChat agents are all provisioned by `./setup.sh`.

## Prerequisites (check before deploying)

```bash
docker info > /dev/null && echo "docker OK"        # Docker running, 8GB+ RAM recommended
docker compose version > /dev/null && echo "compose OK"
command -v jq > /dev/null && echo "jq OK"           # needed for agent seeding
```

If `jq` is missing: `brew install jq` (macOS) or `apt-get install -y jq` (Linux).

The only secret you need from the user is an **Anthropic API key**
(`sk-ant-...`, from https://console.anthropic.com/). If it is not already in `.env`
(`grep '^ANTHROPIC_API_KEY=sk' .env`) and not in the environment, ask the user for it —
do not invent one and do not proceed without it.

## Deploy (one command, non-interactive)

```bash
ANTHROPIC_API_KEY=sk-ant-... ./setup.sh --seed
```

- Omit `ANTHROPIC_API_KEY=...` if the key is already set in `.env`.
- Omit `--seed` to skip demo-trace generation (faster; agents are still created).
- The script is **idempotent** — safe to re-run after any failure. Re-running is the
  correct first response to most errors.
- First run takes ~5 minutes (image pulls). `--seed` adds a few minutes of trace generation.
- If the script exits non-zero, read its last lines: it prints the exact remediation.

Do NOT run `docker compose up` directly for initial deployment — `setup.sh` provisions
secrets and API-side config that compose alone does not.

## Verify (definition of done)

All of these must pass:

```bash
# 1. Langfuse healthy
curl -sf http://localhost:3001/api/public/health > /dev/null && echo "PASS langfuse"

# 2. LibreChat healthy
curl -sf http://localhost:3080/health > /dev/null && echo "PASS librechat"

# 3. Langfuse LLM connection exists (powers Playground + LLM-as-a-Judge)
source .env && curl -sf -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  http://localhost:3001/api/public/llm-connections | grep -q '"adapter":"anthropic"' \
  && echo "PASS llm-connection"

# 4. The 5 demo agents exist in LibreChat
UA="Mozilla/5.0"; TOKEN=$(curl -sf -X POST http://localhost:3080/api/auth/login \
  -H "Content-Type: application/json" -H "User-Agent: $UA" \
  -d '{"email":"demo@example.com","password":"demodemo1!"}' | jq -r .token)
curl -sf -H "Authorization: Bearer $TOKEN" -H "User-Agent: $UA" \
  http://localhost:3080/api/agents | jq -r 'if .data then .data[] else .[] end | .name' \
  | grep -c "ClickHouse Data Analyst\|LLM Observability Analyst\|Prompt Engineer\|LLM Ops Assistant\|Agentic RAG Assistant"
# expect: 5

# 5. Traces exist (only if --seed was used)
curl -sf -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "http://localhost:3001/api/public/traces?limit=1" | grep -o '"totalItems":[0-9]*'
# expect: totalItems > 0
```

Or run the built-in checklist: `./setup.sh --status` (all lines under "Demo Readiness"
should be ✓).

## Report to the user when done

- **LibreChat**: http://localhost:3080 — log in as `demo@example.com` / `demodemo1!`,
  pick an agent from the dropdown (5 pre-configured agents with MCP tools).
- **Langfuse**: http://localhost:3001 — log in as `demo@example.com` / `demodemo1!`,
  traces under Tracing > Traces.
- **Code evaluators**: provisioned automatically (5 deterministic TypeScript
  evaluators — see docs/CODE_EVALUATORS.md). Verify with:
  `docker exec langfuse-postgres psql -U langfuse -d langfuse -t -c "SELECT count(*) FROM job_configurations WHERE id LIKE 'code-eval%' AND status='ACTIVE'"`
  (expect 5 in self-hosted mode; in cloud mode they are a manual UI step).
- **LLM-as-a-Judge evaluators**: provisioned automatically as observation-level
  evaluators (3 live judges + 1 experiment judge). Verify with:
  `docker exec langfuse-postgres psql -U langfuse -d langfuse -t -c "SELECT count(*) FROM job_configurations WHERE id LIKE 'obs-eval%' AND status='ACTIVE'"`
  (expect 4 in self-hosted mode; in cloud mode they are a manual UI step —
  point the user to README > "LLM-as-a-Judge Evaluation").

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ANTHROPIC_API_KEY is not set and no terminal is available` | Re-run with the key: `ANTHROPIC_API_KEY=sk-ant-... ./setup.sh` |
| Agents created without tools / "No MCP tools found" | MCP servers initialize async. Wait 30s, run `./scripts/seed-librechat-agents.sh` again (idempotent — it now re-syncs tool bindings on existing agents, not just new ones). |
| *Prompt Engineer* / *LLM Ops* agent has no prompt tools, or `langfuse-prompts` MCP 403s | Self-hosted `/api/public/mcp` rejects LibreChat's internal `Host: langfuse-web:3000` (validated against `NEXTAUTH_URL`). Ensure `LANGFUSE_MCP_ALLOWED_HOSTS=langfuse-web:3000` is set on `langfuse-web` (requires langfuse image ≥ v3.18x; pinned to `v3.221.1`), then `docker compose --profile langfuse up -d langfuse-web`. |
| Agent chat shows raw `<function_calls>` / `<tool_call>` XML as text | Agent's MCP tools didn't bind, so Claude role-plays the call as text. Verify MCP is healthy, re-run `./scripts/seed-librechat-agents.sh`, then start a **new** conversation. Details: [troubleshoot skill](.agents/skills/troubleshoot/SKILL.md#agent-emits-raw-tool-call-xml). |
| Multi-turn agentic-rag chat in LibreChat doesn't group into one Langfuse **Session** (each turn a new random session) | The `rag-retriever` MCP block in `librechat.yaml` lost its `headers.X-LibreChat-Conversation-Id: "{{LIBRECHAT_BODY_CONVERSATIONID}}"`, or `mcp-rag-retriever` wasn't rebuilt after a `server.py`/`mcp` version change. The header read falls back **silently** to a random session (never an error). Restore the header, `docker compose --profile demo up -d --build mcp-rag-retriever`, then start a **new** conversation. |
| 401 errors from Langfuse CLI/scripts | Shell-exported keys override `.env`: `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY` and retry. |
| Langfuse not ready after 2 min | `docker compose --profile langfuse logs langfuse-web --tail 50` — usually slow first-boot migrations; re-run `./setup.sh`. |
| LibreChat not ready | `docker compose logs api --tail 50` |
| Port conflict (3001/3080/8002...) | Change the port in `.env` (e.g. `LANGFUSE_PORT`), re-run `./setup.sh`. |
| Want a clean slate | `./scripts/reset.sh` (destructive), then `./setup.sh --seed`. |

## Other operations

```bash
./setup.sh --status              # status + demo readiness checklist
./scripts/seed-demo-data.sh      # (re)generate demo traces
./setup.sh --cleanup             # stop containers, keep data
./scripts/reset.sh               # destroy everything
./scripts/validate-langfuse.sh   # deeper Langfuse integration validation
```

## Cloud mode (non-default)

Self-hosted is the default and needs no Langfuse account. For Langfuse Cloud instead:
set `DEPLOY_MODE=cloud`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and
`LANGFUSE_BASE_URL` in `.env` **before** running `./setup.sh` (keys from
cloud.langfuse.com > Settings > API Keys). Everything else is identical.

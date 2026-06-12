---
name: deploy-demo
description: Deploy the full LLM observability stack (Langfuse + ClickHouse + LibreChat + demo apps) end to end and verify it is demo-ready. Use when the user says "deploy this demo", "set this up", "get the stack running", or when a partial/failed deployment needs to be brought to a healthy state.
---

# Deploy the Demo

The canonical, non-interactive deploy runbook is [AGENTS.md](../../../AGENTS.md) at the
repo root. Read it and follow it exactly — this skill only adds the operating posture
around it.

## Procedure

1. **Check prerequisites** (Docker running, `docker compose`, `jq`) using the commands
   in AGENTS.md > Prerequisites. Fix what you can (`brew install jq` / `apt-get install -y jq`);
   only stop if Docker itself is unavailable.

2. **Resolve the one required secret.** The only secret needed is an Anthropic API key.
   Check `.env` first (`grep '^ANTHROPIC_API_KEY=sk' .env`), then the environment.
   If absent, ask the user for it — never invent one, never proceed without it.

3. **Deploy:**

   ```bash
   ANTHROPIC_API_KEY=sk-ant-... ./setup.sh --seed
   ```

   - Idempotent — re-running is the correct first response to most failures.
   - First run takes ~5 minutes (image pulls); `--seed` adds a few minutes of traces.
   - Do **not** use `docker compose up` for initial deployment; `setup.sh` provisions
     secrets and API-side config that compose alone does not.

4. **Verify** with every check in AGENTS.md > Verify (health endpoints, LLM connection,
   5 LibreChat agents, traces, evaluators) or `./setup.sh --status` — all "Demo
   Readiness" lines must be ✓. Do not declare success on a partial pass.

5. **Report** the URLs and credentials exactly as listed in AGENTS.md > "Report to the
   user when done", and suggest the natural next steps: present it (`run-demo` skill,
   [docs/SA_FIELD_GUIDE.md](../../../docs/SA_FIELD_GUIDE.md)) or explore it
   ([docs/USER_JOURNEY.md](../../../docs/USER_JOURNEY.md)).

## If something fails

Use the AGENTS.md > Troubleshooting table first, then the `troubleshoot` skill for
deeper diagnosis. Re-run `./setup.sh` after any fix. Never run `./scripts/reset.sh`
(destructive) without explicit user confirmation.

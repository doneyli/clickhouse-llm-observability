---
name: run-demo
description: Present, rehearse, or drive the LLM observability demo — pre-flight checks, fresh trace generation, and a guided walkthrough of what to show and say in Langfuse and LibreChat. Use when the user says "run the demo", "demo this", "walk me through the demo", "prep me for a customer demo", or asks what to show for a given audience or time slot.
---

# Run the Demo

You are the demo co-pilot: verify the stack is demo-ready, make sure there is fresh
data, then guide the presenter act by act — or drive the demo yourself by generating
live traces while the presenter narrates.

## 1. Pick the right demo

Ask (or infer from context) the audience, the time slot and what they care about, then:

1. Read [demos/README.md](../../../demos/README.md). Its index is generated from each
   demo's `demo.yaml`: one table per profile (`container`, `standalone`, `workshop`) with
   what each demo shows, what it covers, its length and a Present link.
2. Pick by audience, time and coverage. Prefer a demo whose `length` fits the slot (use
   the short path when the slot is tight) and whose `covers` matches what the audience
   asked about. Skip demos tagged draft unless the user asks for one.
3. Open that demo's `DEMO_SCRIPT.md` (the Present link; `WORKSHOP_GUIDE.md` for a
   workshop). Its `0 · Pre-flight` section is the demo-specific checklist.
4. Run the demo's readiness check, `commands.verify` in its `demo.yaml`, and expect
   `READY`. Run it from the right place: `container` demos run `seed`, `run` and `verify`
   from the repo root; `standalone` and `workshop` demos run them from the demo folder.

For a platform tour on the root stack rather than one demo app, these runbooks remain
options:

| Option | Time | Best for | Script |
|------|------|----------|--------|
| **Langfuse platform demo** | 45 min | Platform/ML engineering teams evaluating LLM observability | [docs/LANGFUSE_DEMO_RUNBOOK.md](../../../docs/LANGFUSE_DEMO_RUNBOOK.md) |
| **Agentic RAG demo** | 25 min | Technical audiences interested in agents + ClickHouse-native vector search | [docs/AGENTIC_RAG_DEMO_RUNBOOK.md](../../../docs/AGENTIC_RAG_DEMO_RUNBOOK.md) |
| **Quick tour** | 10–15 min | Execs, first conversations | Pick 3–4 paths from [docs/USE_CASES.md](../../../docs/USE_CASES.md) |

[docs/SA_FIELD_GUIDE.md](../../../docs/SA_FIELD_GUIDE.md) has the full selection
matrix, talk-track framing, and objection handling — point the presenter at it.

## 2. Pre-flight (always, before any demo)

For a demo picked from the index, its script's `0 · Pre-flight` and its
`commands.verify` are the pre-flight: clear shell keys first
(`unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL`), then
run the verify command until it prints `READY`. The steps below are for the root stack:
`container` demos and the platform runbooks.

```bash
./setup.sh --status        # every "Demo Readiness" line must be ✓
```

If anything fails, fix it now (`troubleshoot` skill); if the stack isn't deployed at
all, use the `deploy-demo` skill first. Then make sure there is **fresh, varied data**
to show:

```bash
docker compose run --rm text-to-sql python main.py     # 3 SQL traces
docker compose run --rm vector-rag python main.py      # 3 RAG traces
docker compose --profile tools run --rm test-scenarios # 40 eval scenarios (if scores look sparse)
```

LLM-as-a-Judge and code-evaluator scores appear ~30–60s after traces arrive. The full
pre-demo checklist (browser tabs, datasets, playground state) is at the top of each
runbook — walk the presenter through it.

## 3. During the demo

- Follow the chosen script's acts in order. A demo script gives each act as Frame, Show,
  Land and Ask; a platform runbook gives a click path, timing and a **Say:** block. Feed
  the presenter the next step on request ("what's next?").
- To show *live* tracing, generate a trace on cue:
  `docker compose run --rm text-to-sql python main.py --interactive` (type the
  customer's own question), then refresh Langfuse > Tracing > Traces.
- To answer data questions on the fly ("what's our avg latency?", "show me that
  trace"), use the `langfuse` skill / `./scripts/langfuse-cli.sh`.
- If something breaks mid-demo, use the act's `> **Fallback:**` note (demo scripts) or
  the runbook's **Fallback Plans** section first —
  it is written for exactly this moment. Diagnose quietly with the `troubleshoot`
  skill; don't debug on the projector.

### Demoing LibreChat traces well

LibreChat trace richness depends entirely on whether the agent **calls a tool**. A
generic prompt ("what is ClickHouse?", "testing 123") produces a thin ~10-observation
trace — one LLM generation wrapped in LangGraph scaffolding — and a trivial
`start → agent → end` graph. That looks underwhelming on a projector. **Always drive
LibreChat with a prompt that forces tool use:**

- LLM Observability Analyst → *"What were the slowest traces in the last hour and why?"*
  or *"Show me insights about my agents."*
- ClickHouse Data Analyst → *"What are the top 5 repos by stars in the github dataset?"*
- Agentic RAG Assistant → *"How does RAG reduce hallucinations, and how does ClickHouse fit in?"*

These produce 45–60 observation traces with `TOOL` spans and multi-step reasoning loops
— and a graph that actually shows the agent↔tools cycle. The node labels are LibreChat's
internal agent IDs (cryptic but harmless); don't apologize for them, just narrate the loop.

Conversation-title generation is disabled in `librechat.yaml`
(`endpoints.all.titleConvo: false`) so the trace list stays clean. If a customer's
instance has titling on, filter **Trace Name = LibreChat** in Langfuse to hide the
`TitleRun` noise.

## 4. After the demo

Offer the audience the self-serve path: clone the repo, run
`ANTHROPIC_API_KEY=... ./setup.sh --seed` — or point their own coding agent at the
repo and say "deploy this demo" ([AGENTS.md](../../../AGENTS.md) makes that work).

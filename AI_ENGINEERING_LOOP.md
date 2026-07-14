# The AI Engineering Loop — mapped to this repo

This repo is a working reference for the full **[AI Engineering
loop](https://langfuse.com/academy/ai-engineering-loop)** on ClickHouse-backed
Langfuse — not just tracing. Every demo here plugs into the same loop; this page
maps each step to the concrete code, scripts, and Langfuse pages that implement
it across the whole stack.

> You can't unit-test your way to confidence with probabilistic LLM outputs. The
> loop is how you improve systematically: observe production, learn from it, and
> ship improvements you can trust — then repeat.

```
                            ┌─────────────────────────────────────┐
                            │               DEPLOY                │
                            │   prompt labels · GitHub CI/CD      │
                            └───────────────▲───────────┬─────────┘
   ── ONLINE (understand production) ──     │           │   ── OFFLINE (improve) ──
                                            │           ▼
   ┌───────────┐     ┌───────────┐     ┌────┴──────┐  ┌────────────┐  ┌───────────┐
   │  1 TRACE  │ ──► │ 2 MONITOR │ ──► │ 3 DATASETS│─►│4 EXPERIMENT│─►│ 5 EVALUATE│
   └───────────┘     └───────────┘     └───────────┘  └────────────┘  └───────────┘
        ▲                                                                    │
        └──────────────  ship a change → new traces → repeat  ◄─────────────┘
```

## The apps

| App | What it is | Instrumentation |
|-----|-----------|-----------------|
| `demos/text-to-sql/` | NL → SQL over ClickHouse via MCP | LangChain + Langfuse `CallbackHandler` |
| `demos/vector-rag/` | RAG over ChromaDB | LangChain + Langfuse `CallbackHandler` |
| `demos/agentic-rag/` | Self-correcting RAG on ClickHouse-native vectors | LangGraph + Langfuse SDK |
| `librechat/` | Shared chat frontend | LibreChat native Langfuse tracing |
| `demos/real-estate/` | Self-contained agentic concierge — the loop shown end-to-end in one place | Langfuse SDK |
| `demos/brand-promo-multi-agent/` | Multi-agent promo-planning assistant (standalone): synthetic history, online + offline evals, persona dashboards | LangGraph + CrewAI + Langfuse `CallbackHandler` |

## Where each loop step lives

| # | Step | Across the main stack | Deep-dive example |
|---|------|----------------------|-------------------|
| 1 | **Trace** | All apps emit full traces (prompts, tools, retrieval, cost) to the Langfuse project | `demos/agentic-rag/graph.py`, `demos/text-to-sql/sql_pipeline.py` |
| 2 | **Monitor** | **LLM Observatory** dashboard (`dashboard/`, `:8005`); code + LLM-judge scores; **👍/👎 user feedback** — LibreChat thumbs → `user-feedback` score natively (v0.8.6+), and the `demos/real-estate/` portal | Langfuse **Dashboards** / **Evaluators** |
| 3 | **Build datasets** | `scripts/seed-datasets.py` (coding-quality + security datasets); production traces → dataset from the UI | `demos/real-estate/` 10-item eval set |
| 4 | **Experiment** | `scripts/run-experiments.py` — compare **models / datasets** on the eval sets | **prompt-variant** experiments: `demos/real-estate/` + `agentic-rag` |
| 5 | **Evaluate** | Deterministic **code evaluators** (`evaluators/*.ts`, seeded by `scripts/seed-code-evaluators.sh`) + **LLM-as-a-Judge** (`scripts/seed-llm-judge-evaluators.sh`) | human **annotation** queue in `demos/real-estate/` |
| ⟳ | **Deploy** | **Prompt management by label** — apps fetch prompts from Langfuse at runtime with a local fallback: `agentic-rag` (`scripts/seed-langfuse-prompt.py`), `text-to-sql` + `vector-rag` (`scripts/seed-app-prompts.py`). Promote a label to ship a prompt with no redeploy. | **GitHub CI/CD** reference for gated prompt deploys in `demos/real-estate/cicd/` |

## The Deploy node across the stack

Historically the apps hard-coded their prompts. Now the LLM apps fetch prompts
**by label** (`production`) from Langfuse at runtime, each with a hard-coded
fallback so they still run if Langfuse is unreachable, and each **links the
fetched prompt version to the generation** (so quality ties back to a version):

```bash
# Seed the managed prompts (idempotent), then edit/promote them in the UI:
python scripts/seed-app-prompts.py        # text-to-sql-analysis / -response, vector-rag-generation
python scripts/seed-langfuse-prompt.py    # agentic-rag-generation (v1 + v2 production)
```

Editing a prompt in the Langfuse UI — or promoting a new version to `production`
— changes the app's behaviour on the next run with **no code change**. Gating
that promotion behind CI (run the eval set, ship only on pass) is the
GitHub-integration path documented in `demos/real-estate/cicd/`.

## User feedback (Monitor) — how LibreChat's thumbs reach Langfuse

LibreChat's native 👍/👎 (`PUT /api/messages/:conv/:msg/feedback`) writes a
Langfuse `user-feedback` score **natively** (v0.8.6+, `packages/api/src/langfuse/
feedback.ts`): a BOOLEAN score (`value` 1/0, id `feedback-<traceId>`) on the
answer's trace, deleted when the user retracts the rating — so real user
judgement sits next to the automated evals. It's activated by the same
`LANGFUSE_*` env vars that turn on tracing; no extra service is required.
(Earlier builds lacked this, so the demo previously reconstructed the score with
an nginx-mirrored `feedback-bridge` sidecar — removed now that it's native.)

## The loop, end-to-end in one demo

`demos/real-estate/` is the self-contained walkthrough that shows every step —
trace → monitor → dataset → experiment (models **and** prompts) → evaluate →
**deploy a prompt by label** → repeat — with a presenter runbook. Start there to
see the whole loop close in ~20 minutes, then map the same pattern onto the main
stack using the table above.

## Honest scope (what's live vs documented)

| Capability | Status |
|---|---|
| Trace / Monitor / Datasets / Evaluate across the stack | **Live** |
| Prompt management (label fetch + fallback + link) — agentic-rag, text-to-sql, vector-rag | **Live** |
| Model / dataset experiments (`run-experiments.py`) | **Live** |
| Prompt-variant experiments | **Live** in demos/real-estate + agentic-rag (not the `run-experiments.py` harness) |
| User feedback → Langfuse (LibreChat thumbs + real-estate portal) | **Live** |
| GitHub repository-dispatch CI/CD for prompt deploys | **Documented** — needs a real repo, PAT, public webhook (`demos/real-estate/cicd/`) |

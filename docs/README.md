# Documentation Index

Pick your path by what you're trying to do.

## I want to deploy it

| Doc | What it gives you |
|-----|-------------------|
| [Quickstart Guide](QUICKSTART_GUIDE.md) | Running end to end in 15–30 min, with troubleshooting |
| [../AGENTS.md](../AGENTS.md) | The non-interactive deploy runbook (humans can follow it too — it's the most battle-tested path) |
| [Langfuse Integration](LANGFUSE_INTEGRATION.md) | Deep configuration reference: infrastructure, evaluator setup, MCP servers |
| [LiteLLM Gateway Demo Operations](LITELLM_GATEWAY_DEMO.md) | Separate-project configuration, tags, verification, and upgrade notes for centralized gateway tracing |

## I want to present it

| Doc | What it gives you |
|-----|-------------------|
| [**SA Field Guide**](SA_FIELD_GUIDE.md) | **Start here** — demo selection, talk track, prep checklist, objection handling |
| [Property Concierge Demo Script](../demos/real-estate/DEMO_SCRIPT.md) | Consultative script for the real-estate app — the full AI Engineering loop, benefit-led Frame→Show→Land→Ask flow + "show me the code" appendix |
| [Agentic RAG Demo Script](../demos/agentic-rag/DEMO_SCRIPT.md) | Consultative script for the self-correcting RAG app — ClickHouse-native vectors + Langfuse graph, same flow + code appendix |
| [Text-to-SQL Demo Script](../demos/text-to-sql/DEMO_SCRIPT.md) | Consultative script for the NL-data-assistant demo — SQL-safety guardrail + prompt deploys by label, same flow + code appendix |
| [Vector RAG Demo Script](../demos/vector-rag/DEMO_SCRIPT.md) | Consultative script for the naive-RAG baseline — tracing, managed prompts, free guardrails, and the designed hand-off to agentic-rag |
| [Brand-Promo Demo Script](../demos/brand-promo-multi-agent/DEMO_SCRIPT.md) | Consultative script for the multi-agent (LangGraph + CrewAI) fleet demo — 50k-trace scale, persona dashboards, certification gate |
| [LiteLLM Gateway Demo Script](../demos/litellm-gateway/DEMO_SCRIPT.md) | Short (~5 min) script for the gateway-instrumentation pattern — centralized Langfuse tracing at a LiteLLM proxy, no client SDK, same Frame→Show→Land→Ask flow |
| [Use Case Catalog](USE_CASES.md) | 11 capabilities, each with a 2-minute demo path; quick-tour combos |
| [Langfuse Demo Runbook](LANGFUSE_DEMO_RUNBOOK.md) | 45-min screen-by-screen platform demo script with full talk tracks |
| [Lifecycle Feedback Runbook](LIFECYCLE_FEEDBACK_RUNBOOK.md) | 20-min SA-enablement narrative: one user 👎 → test case → proven prompt fix → CI gate → deploy (pairs with the [Property Concierge script](../demos/real-estate/DEMO_SCRIPT.md)) |
| [Agentic RAG Demo Runbook](AGENTIC_RAG_DEMO_RUNBOOK.md) | Deep screen-by-screen reference + fallbacks (pairs with the co-located [demo script](../demos/agentic-rag/DEMO_SCRIPT.md)) |
| [Brand-Promo Multi-Agent Runbook](../demos/brand-promo-multi-agent/docs/DEMO_RUNBOOK.md) | Deep 60-min segment-by-segment reference (pairs with the co-located [demo script](../demos/brand-promo-multi-agent/DEMO_SCRIPT.md)) |
| [Langfuse RLS Demo Script](../demos/langfuse-rls/DEMO_SCRIPT.md) | Short (~10 min) governance-conversation script for the row-level-security **prototype** over Langfuse traces — simulated feature, loop-adjacent |
| [Dashboard (LLM Observatory)](DASHBOARD.md) | The "your data is just ClickHouse tables" 5-min demo beat |

## I want to learn from it

| Doc | What it gives you |
|-----|-------------------|
| [User Journey](USER_JOURNEY.md) | Guided hands-on walkthrough, zero to insights in ~35 min |
| [Evaluation Architecture](EVALUATION_ARCHITECTURE.md) | Production evaluation strategy: real-time guardrails vs async quality evals |
| [Evaluation Scenarios](EVALUATION_SCENARIOS.md) | The three LLM failure modes the test scenarios demonstrate |
| [Code Evaluators](CODE_EVALUATORS.md) | Deterministic TypeScript evaluators — why, when, and how |
| [Conversation Review](../demos/real-estate/CONVERSATION_REVIEW.md) | Human review of **multi-turn** conversations: session-scoped annotation queues, why a queue of turns can't catch cross-turn failures, and the API gotchas |
| [Agentic RAG Architecture](AGENTIC_RAG_ARCHITECTURE.md) | CRAG loop on ClickHouse-native vectors, typed observations |
| [Langfuse CLI](LANGFUSE_CLI.md) | Terminal access to traces, prompts, datasets, scores |

## I'm an AI agent (or pointing one at this repo)

| Doc | What it gives you |
|-----|-------------------|
| [../AGENTS.md](../AGENTS.md) | Deterministic deploy runbook with machine-checkable verification |
| [../CLAUDE.md](../CLAUDE.md) | Architecture, commands, conventions (Claude Code reads automatically) |
| Project skills in [`.agents/skills/`](../.agents/skills/) | `deploy-demo`, `run-demo`, `troubleshoot`, `langfuse` — full lifecycle coverage |
| [Langfuse Skills](LANGFUSE_SKILLS.md) | The vendored Langfuse skill: SDK patterns, CLI, docs access |

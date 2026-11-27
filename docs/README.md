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
| [**Demo index**](../demos/README.md) | Every demo, generated from its `demo.yaml`: one table per profile with what it shows, coverage, length, the run command and the demo script to present |
| [Use Case Catalog](USE_CASES.md) | 11 capabilities, each with a 2-minute demo path; quick-tour combos |
| [Langfuse Demo Runbook](LANGFUSE_DEMO_RUNBOOK.md) | 45-min screen-by-screen platform demo script with full talk tracks |
| [Lifecycle Feedback Runbook](LIFECYCLE_FEEDBACK_RUNBOOK.md) | 20-min SA-enablement narrative: one user 👎 → test case → proven prompt fix → CI gate → deploy (pairs with the [Property Concierge script](../demos/real-estate/DEMO_SCRIPT.md)) |
| [Agentic RAG Demo Runbook](AGENTIC_RAG_DEMO_RUNBOOK.md) | Deep screen-by-screen reference + fallbacks (pairs with the co-located [demo script](../demos/agentic-rag/DEMO_SCRIPT.md)) |
| [Brand-Promo Multi-Agent Runbook](../demos/brand-promo-multi-agent/docs/DEMO_RUNBOOK.md) | Deep 60-min segment-by-segment reference (pairs with the co-located [demo script](../demos/brand-promo-multi-agent/DEMO_SCRIPT.md)) |
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

## I want to build or change a demo

| Doc | What it gives you |
|-----|-------------------|
| [**Demo contributing guide**](../demos/CONTRIBUTING.md) | **Start here** — the demo standard: profiles, required files, README and script shape, readiness check, tests, the checker's rules, waivers, definition of done |
| [Demo template](../demos/_template/) | The skeleton every new demo is scaffolded from (`python scripts/new-demo.py <name> --profile …`) |
| [`new-demo` skill](../.agents/skills/new-demo/SKILL.md) | The step-by-step flow for a coding agent: scaffold, spec, build, README from a live run, script after a rehearsal, check, register |
| [Demo Standard Spec](DEMO_STANDARD_SPEC.md) | The design record: why the standard looks the way it does, the state it replaced, the migration plan |

## I'm an AI agent (or pointing one at this repo)

| Doc | What it gives you |
|-----|-------------------|
| [../AGENTS.md](../AGENTS.md) | Deterministic deploy runbook with machine-checkable verification |
| [../CLAUDE.md](../CLAUDE.md) | Architecture, commands, conventions (Claude Code reads automatically) |
| Project skills in [`.agents/skills/`](../.agents/skills/) | `deploy-demo`, `run-demo`, `troubleshoot`, `langfuse`, `new-demo` — full lifecycle coverage |
| [Langfuse Skills](LANGFUSE_SKILLS.md) | The vendored Langfuse skill: SDK patterns, CLI, docs access |

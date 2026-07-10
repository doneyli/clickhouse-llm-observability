# Use Case Catalog

Every observability capability this stack demonstrates, with where to see it and a
self-contained ~2-minute demo path for each. Compose 3–4 of these for a quick tour, or
use them as drill-downs when a customer asks "can it do X?". Audience/timing guidance
is in the [SA Field Guide](SA_FIELD_GUIDE.md).

All paths assume a deployed stack (`./setup.sh --seed`, then `./setup.sh --status`
shows all ✓). Langfuse: http://localhost:3001, LibreChat: http://localhost:3080 —
both `demo@example.com` / `demodemo1!`.

---

## 1. End-to-end trace capture

**What:** Every LLM call captured as a hierarchical trace — prompt, completion, model,
tokens, latency, nested spans for each pipeline step.
**Why customers care:** Debugging an LLM app without traces means guessing. With them,
"why did this answer suck?" takes one click.

**Demo path:** Generate live traffic, then inspect it:
```bash
docker compose run --rm text-to-sql python main.py
```
Langfuse > Tracing > Traces → open a `text-to-sql` trace → walk the span tree:
schema-fetch → SQL generation (the actual prompt/completion) → query execution →
result summarization. Point out latency and token counts per span.

## 2. Cost and token tracking

**What:** Per-trace, per-model, per-user cost roll-ups computed from token usage.
**Why customers care:** LLM spend is the first thing finance asks about and the last
thing most teams can answer per-feature.

**Demo path:** Langfuse > Dashboards (or the home dashboard) → cost over time, cost by
model. Open any trace → each generation shows input/output tokens and computed cost.
Filter traces by tag (`text-to-sql` vs `vector-rag`) to show per-app cost attribution.

## 3. LLM-as-a-Judge evaluation

**What:** Observation-level LLM judges (Hallucination, Relevance, Correctness) that
score targeted traffic automatically as it arrives — provisioned out of the box.
**Why customers care:** This is the answer to "how do we know the answers are good?"
at scale — nobody can read every response.

**Demo path:**
```bash
docker compose --profile tools run --rm test-scenarios   # 40 scenarios, 4 failure categories
```
Wait ~60s. Langfuse > Tracing > Traces, filter tag `test-scenario` → scores column
shows each judge catching exactly its failure mode (hallucination scenarios fail
Hallucination but pass Relevance, etc., against a passing control group). Open one
failing trace → the judge's reasoning is attached to the scored observation.
Expected-results matrix: [README](../README.md#llm-as-a-judge-evaluation).

## 4. Code evaluators (deterministic, 100% coverage, free)

**What:** Five TypeScript evaluators running *inside* Langfuse scoring every matching
trace with no LLM calls: SQL risk, credential leaks, response structure, plus two
experiment-time checks.
**Why customers care:** Objective policy checks (security, format, compliance) on 100%
of traffic at zero marginal cost — and the pairing with LLM judges shows a mature
evaluation strategy, not a gimmick.

**Demo path:** Generate traffic (use case 1), wait ~30s → open a trace → show
`sql-risk` / `structure-clean` scores alongside judge scores. Then show the source:
`evaluators/*.ts` — version-controlled, editable, re-seeded with
`./scripts/seed-code-evaluators.sh`. Full walkthrough: [CODE_EVALUATORS.md](CODE_EVALUATORS.md).

## 5. Datasets and experiments

**What:** Versioned evaluation datasets (12-item coding-quality, 8-item security) and
scripted experiment runs that score a model/prompt against them.
**Why customers care:** This is how you ship prompt or model changes with evidence
instead of vibes — regression testing for LLM behavior.

**Demo path:**
```bash
python scripts/run-experiments.py --dataset quality      # ~2 min, creates a scored run
```
Langfuse > Datasets > `coding-assistant-quality` > Runs tab → per-item outputs and
scores, aggregate metrics. Run it twice with different `--model` flags to show
side-by-side comparison. Also show: adding a dataset item directly from a production
trace (Traces → ... → Add to dataset) — the production-to-test-set loop.

## 6. Prompt management and playground

**What:** Versioned prompts with labels (production/latest), and a playground for
side-by-side model/prompt comparison seeded from real traces.
**Why customers care:** Prompts are code that lives outside the repo — teams need
versioning, rollback, and a safe place to iterate.

**Demo path:** Langfuse > Prompts → open the seeded prompt → version history and
labels. Then Playground → load a prompt, run the same input against two models
side-by-side, tweak the system prompt, re-run. Close the loop: "promote the winner"
by setting its label — apps fetch by label, so rollout is instant, no deploy.

## 7. Agentic RAG observability

**What:** A corrective-RAG (CRAG) agent — retrieve → grade → rewrite/web-fallback →
generate — over ClickHouse-native vector search, fully traced with typed observations
and rendered as a graph in Langfuse.
**Why customers care:** Agents are where observability gets hard: loops, branches,
tool calls. A flat log is useless; the graph view shows *why* the agent took its path.

**Demo path:** Follow [AGENTIC_RAG_DEMO_RUNBOOK.md](AGENTIC_RAG_DEMO_RUNBOOK.md)
Act 3: run an agentic-rag query, open the trace in Langfuse → Graph tab → walk the
CRAG loop, including a retrieval-grade failure triggering query rewrite. Architecture:
[AGENTIC_RAG_ARCHITECTURE.md](AGENTIC_RAG_ARCHITECTURE.md).

## 8. Chat agents with MCP tools (zero-code instrumentation)

**What:** LibreChat with 5 pre-provisioned agents (data analyst, observability
analyst, prompt engineer, ops assistant, agentic RAG) wired to ClickHouse and Langfuse
MCP servers — every conversation traced natively, no instrumentation code.
**Why customers care:** Proves observability isn't only for custom apps — off-the-shelf
chat UIs trace too. And the agents themselves are a meta-demo: an agent that queries
the observability data about the other agents.

**Demo path:** LibreChat → pick **LLM Observability Analyst** → ask *"What were the
slowest traces in the last hour and why?"* → it queries Langfuse's ClickHouse via MCP
and answers. Then open Langfuse → the conversation you just had is itself a trace
(tag `librechat`).

> **Reading LibreChat traces.** Pick a prompt that forces tool use (like the one above).
> Trace richness is driven by tool calls: a tool-using query produces a 45–60
> observation `LibreChat` trace with `TOOL` spans and multi-step reasoning; a generic
> chit-chat prompt produces a thin ~10-observation trace (one LLM call wrapped in
> LangGraph scaffolding) and a trivial graph — accurate, but underwhelming live.
> Unlike the Python demos (which use the Langfuse SDK to build clean, named spans),
> LibreChat traces via native LangChain/LangGraph callbacks, so you'll see internal
> node names (`RunnableSequence`, agent IDs) — that's the integration, not a bug.
> Conversation-title generation is disabled (`librechat.yaml` →
> `endpoints.all.titleConvo: false`) so every trace is a real `LibreChat` trace rather than a
> `TitleRun` naming call.

## 9. SQL analytics directly on trace data

**What:** Langfuse's trace store is open ClickHouse tables — query them with SQL, or
through the bundled **LLM Observatory** dashboard (FastAPI + ClickHouse, port 8005).
**Why customers care:** No vendor silo. Cost attribution joined with business data,
custom SLOs, team-specific dashboards — anything SQL can express. This is the
ClickHouse differentiator in one screen.

**Demo path:**
```bash
docker compose --profile langfuse --profile dashboard up -d
```
Open http://localhost:8005 → KPIs, activity heatmap, tool usage, score trends — all
served by direct ClickHouse queries against Langfuse's tables. Then the kicker: open
the [dashboard source](DASHBOARD.md) — "this took one afternoon to build; it's your
data." See [DASHBOARD.md](DASHBOARD.md).

## 10. Importing real production traces

**What:** Import traces from any other Langfuse instance — e.g. your real Claude Code
sessions — with PII scrubbing and tagging.
**Why customers care:** Demos land harder with real data, and it shows migration into
a self-hosted instance is a script, not a project.

**Demo path (pre-demo prep, not live):**
```bash
SOURCE_LANGFUSE_PUBLIC_KEY=<pk> SOURCE_LANGFUSE_SECRET_KEY=<sk> \
  python scripts/import-external-traces.py --limit 30 --scrub --add-tag claude-code-demo
```
In the demo: filter traces by `claude-code-demo` → "these are real traces from the AI
agent that maintains this repo."

---

## Suggested quick-tour combos

- **Exec, 10 min:** 1 → 2 → 3, close with the spectrum table from the [field guide](SA_FIELD_GUIDE.md).
- **Platform team, 15 min:** 1 → 3 → 4 → 5.
- **Agent-curious, 15 min:** 1 → 7 → 8.
- **Data team, 15 min:** 1 → 2 → 9.

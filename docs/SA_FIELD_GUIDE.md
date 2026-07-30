# Solutions Architect Field Guide

How to present this asset to customers: which demo to give, the story to tell, how to
prepare, and how to handle the questions that come up. The screen-by-screen scripts
live in the runbooks ([Langfuse platform](LANGFUSE_DEMO_RUNBOOK.md),
[Agentic RAG](AGENTIC_RAG_DEMO_RUNBOOK.md)) — this guide is everything around them.

---

## What this asset is

A complete, self-provisioning LLM observability stack you can stand up in ~5 minutes:
Langfuse (backed by ClickHouse) tracing three real LLM applications (Text-to-SQL,
Vector RAG, Agentic RAG), a LibreChat chat UI with 5 pre-configured agents, automated
evaluators (LLM-as-a-Judge + deterministic code evaluators), evaluation datasets,
experiments, and a custom analytics dashboard reading ClickHouse directly.

Three ways to use it:

1. **Present it** — run a customer demo from the runbooks (this guide).
2. **Hand it over** — customers deploy it themselves with one command, or by pointing
   any coding agent at the repo and saying *"deploy this demo"* ([AGENTS.md](../AGENTS.md)).
3. **Learn from it** — it's a reference architecture: real instrumentation code,
   evaluator definitions, and evaluation strategy docs.

---

## Pick your demo

| Audience | Time | Run this | Why |
|----------|------|----------|-----|
| Platform / ML engineering team evaluating LLM observability | 45 min | [Langfuse Demo Runbook](LANGFUSE_DEMO_RUNBOOK.md) | Full arc: tracing → datasets → playground → experiments → evaluators |
| Technical audience interested in agents / RAG / vector search | 25 min | [Agentic RAG Demo Runbook](AGENTIC_RAG_DEMO_RUNBOOK.md) | CRAG loop on ClickHouse-native vectors, graph view in Langfuse |
| "How do we actually *improve* an agent?" — teams past tracing, stuck on iteration | 20 min | [Lifecycle Feedback Runbook](LIFECYCLE_FEEDBACK_RUNBOOK.md) | One user's 👎 becomes a test case, a proven prompt fix, a CI gate, and a deploy — the loop closing end to end, with a **visible** quality win |
| Full AI Engineering loop on one app, six acts | 20–25 min | [Property Concierge script](../demos/real-estate/DEMO_SCRIPT.md) | Self-contained agentic demo: trace → score → annotate → dataset → experiment → deploy |
| Execs / first conversation | 10–15 min | 3–4 paths from the [Use Case Catalog](USE_CASES.md) | Each use case has a self-contained 2-minute demo path |
| Hands-on workshop | 35 min/person | [User Journey](USER_JOURNEY.md) | Attendees drive; works on their laptops via `./setup.sh` |
| Data/analytics-leaning audience | +5 min add-on | [LLM Observatory dashboard](DASHBOARD.md) | "Your trace data is just ClickHouse tables — build anything on it" |

The demos compose: a common 60-minute format is the platform demo with the Agentic RAG
Act 3 (graph view) spliced in, closing on the dashboard.

**An AI agent can co-pilot any of these.** Open Claude Code in the repo and invoke the
`run-demo` skill — it pre-flights the stack, generates fresh traces, and feeds you the
next step act by act.

---

## The story you're telling

**The problem (open with this):** Traditional monitoring tells you *if* your app
responded — not *if the response was good*. An LLM that confidently returns a wrong
answer looks like a 200 OK. Teams need to capture every prompt/completion pair, score
quality continuously, and debug issues across millions of high-cardinality,
large-payload traces. That is an analytics workload, not a transactional one.

**Why ClickHouse:** LLM telemetry is append-only, huge (10–100KB payloads), and
queried analytically ("all slow responses for model X last week"). Columnar storage
compresses repetitive prompt text 10–20x; queries stay sub-second at billions of rows.
This is why Langfuse itself runs on ClickHouse — the demo shows the platform *and* the
engine under it.

**The spectrum (where this lands for each customer):**

| Customer shape | Their need | The fit |
|----------------|-----------|---------|
| AI-native startup | Fast time-to-value | Langfuse Cloud — managed, free tier, powered by ClickHouse |
| Platform team at a scaleup | Control, custom analytics, cost | Self-hosted Langfuse on ClickHouse — exactly this stack |
| Enterprise ML platform team | Integrate with existing data stack | ClickHouse as the unified analytical layer; Langfuse as the UX on top |
| Data / analytics team | One view across AI + business data | Trace data lands in ClickHouse tables they can join with anything |
| Security / compliance | Data sovereignty, audit trails | Fully self-hosted — LLM data never leaves their control |

**Three durable angles** (weave in where the audience bites):

- **TCO at scale.** LLM telemetry volume explodes with adoption. Per-event SaaS
  pricing grows linearly with it; ClickHouse compression + commodity storage doesn't.
- **Data gravity.** Traces, eval datasets, experiment results, costs, and user
  feedback all land in one queryable store — joinable with business data, usable for
  fine-tuning and analytics, not locked in a vendor silo. The
  [LLM Observatory dashboard](DASHBOARD.md) is the live proof: a custom app reading
  Langfuse's ClickHouse tables directly.
- **Sovereignty.** Everything in this demo runs on the customer's hardware. Prompts
  and completions — often the most sensitive data a company has — never leave.

**Close with:** map what they saw back to *Observe → Evaluate → Optimize* (the closing
section of the platform runbook does this), then hand them the repo — they can have
the same stack running this afternoon.

---

## Preparing

**The day before:**

- [ ] Fresh deploy on the demo machine: `ANTHROPIC_API_KEY=... ./setup.sh --seed`
- [ ] `./setup.sh --status` — every Demo Readiness line ✓
- [ ] Run the runbook's pre-demo checklist (browser tabs, datasets, playground state)
- [ ] Skim the **Fallback Plans** section of your runbook — know them before you need them
- [ ] Optional but high-impact: import real traces from your own Claude Code usage
      (`scripts/import-external-traces.py --scrub`) — "these are traces from the AI
      agent that maintains this repo" lands well

**30 minutes before:** re-run `./setup.sh --status`, generate fresh traces
(`docker compose run --rm text-to-sql python main.py`), confirm judge scores are
appearing (~60s after traces).

**Demo hygiene gotchas:**

- If your shell has `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` exported, they
  override `.env` and trace export 401s silently. `unset` them before demoing.
- Logins: Langfuse and LibreChat both use `demo@example.com` / `demodemo1!`.
- 8GB+ Docker RAM for the self-hosted stack; use `DEPLOY_MODE=cloud` on weak machines.

---

## Q&A and objection handling

**"How does this compare to LangSmith / Braintrust / Arize?"**
Langfuse is open source (MIT), framework-agnostic, and self-hostable — no vendor or
framework lock-in, and the data layer is open ClickHouse tables you can query with
SQL. Managed alternatives are closed platforms where your trace data is reachable only
through their UI and APIs, priced per event.

**"We already have Datadog / our APM."**
Keep it — APM and LLM observability answer different questions. APM tells you the
request succeeded; this tells you whether the *answer was good* (evaluations), what it
cost per token, and lets you replay/experiment on real production prompts. Bonus:
Datadog and many observability platforms run on columnar engines for exactly the
workload reasons covered above — and trace data in ClickHouse can sit next to other
telemetry you already store there.

**"Can't we just use the OpenAI/Anthropic dashboards?"**
Provider dashboards show aggregate usage for *their* API only. No traces, no
evaluation, no cross-provider view, no session/user attribution, and nothing
self-hosted.

**"Does it scale?"**
Langfuse Cloud runs on the same ClickHouse architecture you're looking at —
this isn't a toy schema, it's the production design. ClickHouse routinely handles
millions of events/second and petabyte-scale analytics (it powers observability at
Cloudflare, Uber, eBay).

**"What about data privacy — prompts are sensitive."**
That's the self-hosted pitch: the whole stack, including the LLM trace store, runs in
their VPC or on-prem. The only external call in this demo is to the Anthropic API for
completions — and that's their app's existing call pattern, not something Langfuse adds.

**"How much work is instrumentation?"**
Show the code: `text-to-sql/langfuse_config.py` and the `langfuse_span` context
manager — a decorator/context-manager pattern, a few lines per app. LibreChat needed
zero code (native integration). OpenTelemetry-based SDKs cover most languages.

**"Is LLM-as-a-Judge reliable enough to act on?"**
Pair it with deterministic code evaluators — that's why this demo ships both. Code
evaluators score 100% of traffic for free on objective checks (SQL safety, credential
leaks, format); LLM judges handle semantic qualities (hallucination, relevance) on
targeted or sampled traffic. [EVALUATION_ARCHITECTURE.md](EVALUATION_ARCHITECTURE.md)
is the deep answer; the 40 test scenarios demonstrate judges catching three distinct
failure modes with a control group.

**"What does it cost to run?"**
Self-hosted: the hardware (this whole demo fits in 8GB of Docker RAM) plus pennies of
Anthropic API usage for judges. Langfuse Cloud has a free tier. The ClickHouse story
gets *better* at scale — compression does the work.

---

## Handing it to the customer

End every demo by giving them the repo. The pitch: *"Everything you just saw
self-provisions. One command — or tell your coding agent to deploy it."*

- **Human path:** clone → `ANTHROPIC_API_KEY=sk-ant-... ./setup.sh --seed` → 5 minutes.
- **Agent path:** open Claude Code / Codex / Cursor in the repo, say **"deploy this
  demo"**. [AGENTS.md](../AGENTS.md) gives the agent a deterministic runbook with
  machine-checkable verification; project skills (`deploy-demo`, `run-demo`,
  `troubleshoot`) cover the full lifecycle.
- **Low-resource path:** `DEPLOY_MODE=cloud` in `.env` + free Langfuse Cloud keys —
  ~5 containers instead of ~12.
- **Where they go next:** [User Journey](USER_JOURNEY.md) to learn it hands-on,
  [Use Case Catalog](USE_CASES.md) to map features to their needs,
  [LANGFUSE_INTEGRATION.md](LANGFUSE_INTEGRATION.md) to instrument their own apps.

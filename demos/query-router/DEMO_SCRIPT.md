# Query Router — client demo script

**Pattern:** Routing / classification-dispatch (Anthropic's "Building Effective
Agents" #2). **Runtime:** ~15 min full; short path is Acts 1–2 (~6 min).

> The marquee decision this demo lands: **the router's choice is a first-class,
> scorable object** — its route, its confidence, whether it misrouted, and
> whether a better prompt/model would route more accurately. Not a log line.

This is written as a **conversation, not a walkthrough**. Every act has four
beats: **Frame** (the problem in the customer's world), **Show** (the thing on
screen), **Land** (the one sentence that sticks), **Ask** (a question that makes
them talk about their stack). Skip acts freely; the honesty notes at the bottom
are load-bearing — read them before you present.

---

## 0 · Pre-flight (do this before the customer joins)

```bash
# Footgun: a shell with stale Langfuse keys exported makes trace export 401.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Router dispatches over HTTP, so the three handlers must be up too:
docker compose --profile langfuse --profile demo up -d   # query-router + text-to-sql + vector-rag + agentic-rag
# One-time: build the agentic-rag vector index if you haven't:
#   docker compose --profile demo run --rm agentic-rag python ingest.py

# Seed the loop artifacts (idempotent):
python scripts/seed-router-prompt.py      # query-router-classifier v1 (baseline) + v2 (production)
python scripts/seed-router-dataset.py     # query-router-accuracy (~30 items)
python scripts/seed-router-history.py     # 14 days of backdated router traces (for the dashboard)
./scripts/seed-router-judge.sh            # categorical route-plausibility judge (sampling 0.25)
./scripts/seed-code-evaluators.sh         # registers evaluators/route-match.ts on the router dataset

# Sanity check the front door + handler reachability:
curl -s localhost:8008/health | python3 -m json.tool
```

Build the **Router Ops** dashboard once (see Act 4 for the four widgets — ~5 min
of clicks). Open three tabs: Langfuse **Traces** filtered `name = route-and-dispatch`,
the **Router Ops** dashboard, and **Prompts** (`query-router-classifier`).

**What each act proves**

| Act | Proves |
|---|---|
| 1 | The router decision is a scorable observation; the chosen specialist's full trace nests beneath it |
| 2 | The fallback + HITL path is what separates a routing *pattern* from an `if` statement |
| 3 | Silent misclassification is the signature failure mode — and it's captured as a **score, not a tag** |
| 4 | Route drift and misroute rate are only visible in aggregate — ClickHouse makes that query instant |
| 5 | You can fix the router, prove it, and ship it by moving a label — the whole loop on one LLM call |

---

## Act 1 — One question, one specialist (the clean route)

**Frame.** "One prompt tuned for everything is tuned for nothing. Teams end up
with a mega-prompt that's mediocre at SQL, mediocre at docs, mediocre at
everything. Routing lets each handler stay narrow — and the *only* new risk is
the router being wrong, which is exactly what we're going to make observable."

**Show.**
```bash
docker compose --profile demo run --rm query-router python main.py
```
Open a `docs_complex` trace (`name = route-and-dispatch`). Point at:
- the **`route-query` generation** — output `{route, confidence, rationale}`,
  `metadata.route` set, **prompt-linked** to `query-router-classifier`, with a
  `router_confidence` score right on it;
- and *beneath it*, the **entire agentic-rag subtree** — `route → retrieve →
  grade → generate → reflect`, with its own `retrieval_relevance` /
  `groundedness` scores — nested into this one trace across two services.

Then open an `analytics_sql` trace: same front door, a totally different
specialist (text-to-sql's 2-stage chain + MCP catalog).

**Land.** "The router decision is a first-class, scorable object — and the trace
proves *which* specialist ran and *why*."

**Ask.** "Where in your stack does a classifier decide something today with no
trace of why it chose what it chose?"

---

## Act 2 — The ambiguous question (threshold → fallback → HITL)

**Frame.** "The interesting question isn't the clean one. It's: what does the
system do when the classifier *isn't sure*?"

**Show.** Interactive mode:
```bash
docker compose --profile demo run --rm query-router python main.py --interactive
```
- Ask **"Is ClickHouse fast?"** → confidence ~0.55 < 0.70 → a `fallback-handler`
  answer *with a caveat* + an `escalate-to-human` event carrying
  `reason = low_confidence`.
- Now stop the vector-rag container and route a `docs_simple` question:
  ```bash
  docker compose stop vector-rag
  ```
  → the same graceful path, this time `reason = handler_unreachable` (a HTTP 5xx
  from a specialist becomes a fallback, **not** a 500 to the user). Restart it
  after: `docker compose start vector-rag`.

**Land.** "The fallback route + the machine-readable escalation reason are what
separate a routing *pattern* from an if-statement."

**Ask.** "When your classifier is unsure today — does it guess, or does it have
somewhere safe to send the request?"

---

## Act 3 — The seeded misroute (post-hoc SCORE, not a tag)

**Frame.** "The dangerous failure isn't the error — it's the confident wrong
answer. A misroute sends a live-numbers question to the docs handler, which
answers *plausibly* from the wrong corpus. No exception. *Silent
misclassification.*"

**Show.** Restart the router with the seeded fault and ask the taxi-count
question:
```bash
ROUTER_FAULT=sql-blindness docker compose --profile demo run --rm -e ROUTER_FAULT query-router \
  python main.py --interactive
# Ask: "How many taxi rides were there in NYC in July 2015?"
```
→ confidently routed `docs_simple`, a plausible-but-wrong docs answer. Fix it on
camera as a **post-hoc score**:
```bash
docker compose --profile demo run --rm query-router \
  python scripts/score_misroute.py <trace_id> <route_query_observation_id> --expected analytics_sql
```
Say the line: **"Tags are immutable at creation — an after-the-fact judgment is a
*score*."** `routing_correct = 0` (BOOLEAN) now sits on the `route-query`
observation. Then pin the case into the dataset (UI: Observations → filter
`name = route-query`, sort by `router_confidence` ascending → **Actions → Add to
dataset**, or `score_misroute.py … --add-to-dataset --question "…"`).

**Land.** "Production misroutes become tomorrow's regression tests — pinned to
the exact router decision that was wrong."

**Ask.** "How would someone on your team even *notice* a confident misroute
today, let alone turn it into a test?"

---

## Act 4 — Router Ops dashboard (drift + misroute rate)

**Frame.** "Per-trace, everything looks fine. The failures of routing —
*drift* and *silent misroutes* — only show up in aggregate."

**Show.** The **Router Ops** dashboard over the seeded 14 days:

| Widget | View / measure | Dimension / filter | Watches |
|---|---|---|---|
| Route distribution over time | observations · count | `metadata.route`, filter `name=route-query`, daily | **route drift** |
| Fallback rate | observations · count | filter `metadata.fallback_triggered=true` vs total | threshold too tight / taxonomy gaps |
| Avg `router_confidence` | scores (numeric) · avg | score `router_confidence`, daily | classifier degradation before misroutes surface |
| Misroute rate | scores · avg of `routing_correct` | daily | **silent misclassification** |

Point at the `analytics_sql` **share doubling in week 2** (drift you'd never see
per-trace) and the misroute cluster. Note the WARNING thresholds:
`avg(routing_correct) < 0.90` or fallback rate `> 15%`. The headless polling hook
behind each widget is the Metrics API:

```bash
curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" -G \
  --data-urlencode 'query={
    "view": "observations",
    "metrics": [{"measure": "count", "aggregation": "count"}],
    "dimensions": [{"field": "metadata.route"}],
    "filters": [{"column": "name", "operator": "=", "value": "route-query", "type": "string"}],
    "timeDimension": {"granularity": "day"},
    "fromTimestamp": "2026-07-10T00:00:00Z", "toTimestamp": "2026-07-24T00:00:00Z",
    "config": {"row_limit": 1000}}' \
  http://localhost:3001/api/public/v2/metrics
```

**Land.** "This distribution is a query over *every* router decision — it comes
back instantly because Langfuse aggregates it in ClickHouse."

**Ask.** "Who would own this dashboard in your org — and what's their alert
threshold for 'the router drifted'?"

---

## Act 5 — Fix the router, prove it, ship it (Experiment → Deploy)

**Frame.** "We found a misroute. Now the loop: change *only* the router, prove
the change on a dataset, and ship it without touching the handlers."

**Show.**
```bash
python scripts/run-router-experiment.py --sample 30
```
Three runs, each differing in exactly one axis — prompt `baseline` vs
`production`, and router model Haiku vs Sonnet — with `avg_route_accuracy`
side-by-side in the Langfuse experiment view, and per-item `route-match` scores.
The Act 3 misroute is green under `production`. Promote by moving the
`production` **label** on `query-router-classifier` — no redeploy. The CI gate is
the same command:
```bash
python scripts/run-router-experiment.py --sample 30 --ci   # exits 1 if avg_route_accuracy < 0.90
```

**Land.** "That's the entire AI-engineering loop — trace, monitor, dataset,
experiment, evaluate, deploy — on the cheapest possible surface: a one-call
classifier."

**Ask.** "If shipping a routing change were one label move behind a passing eval,
how much faster would your team iterate on it?"

---

## Honesty notes (read before presenting)

- **Handlers answer from different corpora** (live catalog vs docs), so a wrong
  route sometimes still yields a *passable* answer — that is exactly why
  misroutes are *silent* and need scores, not exceptions.
- **`confidence` is model-self-reported**, calibrated only by the prompt (v2's
  calibration rules). It is a useful gate, not ground truth — the
  dataset/experiment loop is the real safety net.
- **Nested groundedness lands on the router's trace.** When agentic-rag runs as a
  handler, its `reflect_node` calls `score_current_trace("groundedness", …)`,
  which attaches to the *router's* `route-and-dispatch` trace (the E2E
  groundedness of the routed answer) — desirable, but don't be surprised to see
  it there.
- **Classification-only experiments** (`run-router-experiment.py` default) do not
  measure end-to-end answer quality — that isolates the router as the only
  variable and keeps a 30-item run at cents on Haiku. Use `--e2e` to dispatch to
  the live handlers when you want end-to-end comparison.
- **The route taxonomy is deliberately distinct** from agentic-rag's internal
  `kb|sql|direct` — this is the *front-door* router over whole demos, not the
  in-graph node.

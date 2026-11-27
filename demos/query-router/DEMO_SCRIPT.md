# Query Router — Demo Script (Routing / classification-dispatch)

The audience watches one front door classify questions and send each to exactly one
specialist demo, sees what happens when the router isn't sure or a specialist is down,
catches a confident misroute and records it as a score, reads route drift off a dashboard,
and closes the loop by proving a router change on a dataset and shipping it with a label
move. The marquee decision this demo lands: **the router's choice is a first-class,
scorable object** — its route, its confidence, whether it misrouted, and whether a better
prompt or model would route more accurately. Not a log line.

- **App:** the `query-router` container (`main.py` batch and `--interactive`; FastAPI on `:8008`), dispatching over HTTP to text-to-sql `:8002`, vector-rag `:8003` and agentic-rag `:8006`
- **Observability:** Langfuse self-hosted (`http://localhost:3001`), project `LLM Observability Demo`; root span `route-and-dispatch`, router generation `route-query`, tags `query-router`, `demo`
- **Models:** router and fallback on `claude-haiku-4-5`; handlers on `ANTHROPIC_MODEL` (compose default `claude-sonnet-4-6`); `route-plausibility` judge on `LANGFUSE_EVAL_MODEL` (default `claude-haiku-4-5-20251001`); the experiment compares Haiku with `claude-sonnet-4-6`
- **Run length:** 15 min full; short path is the Opening, Acts 1–2 and the Close (6 min)
- **Data:** ClickHouse public datasets (via text-to-sql) and the handler demos' docs corpus; router history and the dataset are synthetic
- **Prompts:** `query-router-classifier`, labels `baseline` (v1) and `production` (v2)

> Setup lives in [README.md](README.md).

---

## How to run this script

This is written as a **conversation, not a walkthrough**. Every act has four beats:

- **Frame** — the problem in the customer's world, before you touch the screen.
- **Show** — the thing on screen: the exact commands and what to point at.
- **Land** — the one sentence that sticks.
- **Ask** — a question that makes them talk about their stack.

Skip acts freely and let the Ask answers decide where the time goes. The short path is the
Opening, Act 1, Act 2 and the Close. The [Known limits](#known-limits) at the bottom are
load-bearing — read them before you present.

---

## 0 · Pre-flight

From the repo root:

```bash
# Footgun: a shell with stale Langfuse keys exported makes trace export 401 or go to another project.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL

# Router dispatches over HTTP, so the three handlers must be up too:
docker compose --profile langfuse --profile demo up -d   # query-router + text-to-sql + vector-rag + agentic-rag
# One-time: build the agentic-rag vector index if you haven't:
#   docker compose --profile demo run --rm agentic-rag python ingest.py

# Seed the loop artifacts (idempotent: prompt, dataset, code evaluators, history, judge):
python demos/query-router/scripts/seed_all.py

# One fresh batch so every route has a live trace to open:
docker compose --profile demo run --rm query-router python main.py

# READY, plus the link each act opens:
python demos/query-router/scripts/verify_demo.py --links
```

`verify_demo.py` exits 1 when a required check fails; fix what it names before the
meeting. It also checks the router reaches all three handlers from inside the compose
network (the same thing `curl -s localhost:8008/health` shows).

**Tabs to open:** the Act 1 dispatched trace, Langfuse **Traces**, the **Router Ops**
dashboard and the `query-router-classifier` prompt, all printed by `--links`.

### The day before

Build the **Router Ops** dashboard once (see Act 4 for the four widgets — about 5 minutes
of clicks). Run `seed_all.py` again if the seeded history is more than a few days old: it
is backdated from the day it was seeded and ages out of the 14-day window.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| The router decision is a scorable observation; the chosen specialist's full trace nests beneath it | Act 1 |
| The fallback + HITL path is what separates a routing *pattern* from an `if` statement | Act 2 |
| Silent misclassification is the signature failure mode — and it's captured as a **score, not a tag** | Act 3 |
| Route drift and misroute rate are only visible in aggregate — ClickHouse makes that query instant | Act 4 |
| You can fix the router, prove it, and ship it by moving a label — the whole loop on one LLM call | Act 5 |

---

## Opening · Where does your classifier decide? (1 min)

**Frame.** "Most teams already have a router, even if they don't call it that: a
classifier, a keyword rule or a prompt that decides which handler a request goes to. It is
the cheapest call in the stack and the least observed one. When it is wrong, nothing
throws — a specialist just answers the wrong question well."

**Ask.** "Where in your stack does something decide which path a request takes?" and
"When that decision is wrong, how do you find out today?"

**Land.** "Let me show you a front door whose every decision is traced, scored and
testable — then what happens when it isn't sure, and how you'd fix it when it's wrong."

---

## Act 1 · One question, one specialist (2 min)

**Frame.** "One prompt tuned for everything is tuned for nothing. Teams end up
with a mega-prompt that's mediocre at SQL, mediocre at docs, mediocre at
everything. Routing lets each handler stay narrow — and the *only* new risk is
the router being wrong, which is exactly what we're going to make observable."

**Show.**
```bash
docker compose --profile demo run --rm query-router python main.py
```
Open the Act 1 link from `--links`: a dispatched `docs_complex` run. Its trace is named
after the handler (`agentic-rag-handler`), not `route-and-dispatch` — the handler's spans
reach Langfuse first. Point at:
- the **`route-query` generation** — output `{route, confidence, rationale}`,
  `metadata.route` set, **prompt-linked** to `query-router-classifier`, with a
  `router_confidence` score right on it;
- and *beneath it*, the **entire agentic-rag subtree** — `route → retrieve →
  grade → generate → reflect`, with its own `retrieval_relevance` /
  `groundedness` scores — nested into this one trace across two services.

Then open an `analytics_sql` trace (`text-to-sql-handler`): same front door, a totally
different specialist (text-to-sql's 2-stage chain + MCP catalog).

**Land.** "The router decision is a first-class, scorable object — and the trace
proves *which* specialist ran and *why*."

**Ask.** "Where in your stack does a classifier decide something today with no
trace of why it chose what it chose?"

> **Fallback:** if a route has no trace, its question fell back — check `curl -s
> localhost:8008/health` for an unreachable handler, then re-run the batch.

---

## Act 2 · The ambiguous question (2 min)

**Frame.** "The interesting question isn't the clean one. It's: what does the
system do when the classifier *isn't sure*?"

**Show.** Interactive mode:
```bash
docker compose --profile demo run --rm query-router python main.py --interactive
```
- Ask **"Is ClickHouse fast?"** → confidence below the 0.70 threshold → a
  `fallback-handler` answer *with a caveat* + an `escalate-to-human` event carrying
  `reason = low_confidence`. The seeded prompt's own example puts this question at 0.5.
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

> **Fallback:** if "Is ClickHouse fast?" clears the threshold on the day, ask "Show me how
> RAG performs on real data." — the other mixed-intent question in the batch.

---

## Act 3 · The seeded misroute (3 min)

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

> **Fallback:** the trace and observation ids are on the `route-query` generation in the
> trace view; copy both from there rather than from the terminal.

---

## Act 4 · Router Ops dashboard (3 min)

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

> **Fallback:** set `fromTimestamp` / `toTimestamp` to the 14 days before you seeded. On the
> self-hosted 3.x server `/api/public/v2/metrics` answers 404 (it needs a v4 server); show
> the dashboard and skip the curl there.

---

## Act 5 · Fix the router, prove it, ship it (3 min)

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

> **Fallback:** the experiment runs on the host and imports `router.py`, so it needs the
> demo's requirements there (Python 3.10+). If they are missing, show the last run's
> results in **Datasets → query-router-accuracy → Runs**.

---

## Close · What to take away (1 min)

1. The router's decision is an observation you can filter, score and chart, with the
   specialist's own trace nested beneath it.
2. When the router is unsure or a specialist is down, the request lands somewhere safe
   with a machine-readable reason, and a confident misroute becomes a score and a test
   case.
3. You change only the router, prove it on a dataset, and ship it by moving a label.

Hand over the repo: `demos/query-router` runs on the root stack with the steps in the
[README](README.md), and the handler demos it routes to are in the same repo.

---

## Show me the code

**1 · The router decision is its own generation — `router.py:77`**
`classify()` opens `route-query`, and at `router.py:109` writes `{route, confidence,
rationale}` to output, `metadata.route` for the dashboard, and the prompt object so the
version is linked. The runtime `router_confidence` score goes on the same observation at
`router.py:123`.

**2 · The confidence gate — `router.py:94`**
An unknown or out-of-scope label becomes `fallback` with its reason; a valid route below
`ROUTER_CONFIDENCE_THRESHOLD` becomes `fallback` with `low_confidence`. Malformed JSON is
caught at `router.py:91` as `malformed_output`.

**3 · One trace across services — `handlers.py:42`**
`dispatch()` sends the current `trace_id` and its own span id as `trace_context` in the
HTTP payload, so the handler's spans nest under `dispatch-<route>` in the router's trace.

**4 · A down specialist degrades, it doesn't 500 — `handlers.py:52`**
Any `httpx` error marks the span `ERROR` and reroutes to the fallback with
`handler_unreachable`.

**5 · The escalation event — `handlers.py:81`**
`escalate-to-human` carries the reason, the confidence and the rationale: the
machine-readable input for a triage queue.

**6 · One root span before the trace attributes — `server.py:58`**
`route-and-dispatch` is opened first, so `route-query` and `dispatch-*` become its
children instead of two separate traces; the docstring above records the bug this fixed.

**7 · A misroute is a score on the observation — `scripts/score_misroute.py:55`**
`routing_correct` is written against the `route-query` observation id, and
`--add-to-dataset` pins the case with `source_observation_id` at
`scripts/score_misroute.py:69`.

**8 · The experiment varies only the router — `scripts/run-router-experiment.py:63`**
The task calls `router.classify()` with a prompt label and a model and nothing else;
`route_match` at `scripts/run-router-experiment.py:77` scores exact route equality, and
the gate at `scripts/run-router-experiment.py:158` exits 1 below the threshold.

---

## Talking points & objections

- **"Why not one big prompt that handles everything?"** Each specialist keeps its own
  prompt, tools and cost tier. The price is one new failure mode, the router being wrong,
  and this demo makes that failure visible and measurable.
- **"Isn't an `if` on keywords good enough?"** Until a question is ambiguous. The router
  here has a threshold, a fallback and an escalation reason, and its accuracy is a number
  you can regression-test.
- **"Self-reported confidence is meaningless."** It is calibrated only by the prompt, so
  treat it as a gate, not ground truth. The dataset, the experiment and the
  `route-plausibility` judge are the real safety net.
- **"Why a score and not a tag?"** Tags are fixed when the trace is created. A misroute is
  a judgment made later, and a score can be added, charted and averaged after the fact.
- **"Doesn't a router add a model call to every request?"** One short classification on
  the cheapest model (`max_tokens=200`), and the experiment tells you whether a bigger
  router is worth it.
- **"Our handlers aren't Python."** The join is two ids in the request body. Any handler
  that passes them to its Langfuse client nests under the router's span.

---

## Known limits

- **Handlers answer from different corpora** (live catalog vs docs), so a wrong
  route sometimes still yields a *passable* answer — that is exactly why
  misroutes are *silent* and need scores, not exceptions.
- **`confidence` is model-self-reported**, calibrated only by the prompt (v2's
  calibration rules). It is a useful gate, not ground truth — the
  dataset/experiment loop is the real safety net.
- **Nested groundedness lands on the router's trace.** When agentic-rag runs as a
  handler, its `reflect_node` calls `score_current_trace("groundedness", …)`,
  which attaches to the router's trace (the E2E groundedness of the routed answer) —
  desirable, but don't be surprised to see it there.
- **Classification-only experiments** (`run-router-experiment.py` default) do not
  measure end-to-end answer quality — that isolates the router as the only
  variable and keeps a 30-item run at cents on Haiku. Use `--e2e` to dispatch to
  the live handlers when you want end-to-end comparison.
- **The route taxonomy is deliberately distinct** from agentic-rag's internal
  `kb|sql|direct` — this is the *front-door* router over whole demos, not the
  in-graph node.
- **The router history is synthetic**, and the Router Ops dashboard is built by hand.

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

```bash
docker compose start vector-rag                               # if Act 2 stopped it
python demos/query-router/scripts/seed_all.py                 # puts the seeded text back on `production` / `baseline`; tops up aged history
docker compose --profile demo run --rm query-router python main.py   # a fresh batch
python demos/query-router/scripts/verify_demo.py              # READY again
```

`ROUTER_FAULT` is set per command in Act 3, so nothing stays faulted. `routing_correct`
scores and items pinned to the dataset in Act 3 stay; `seed_all.py` leaves items it did
not define alone, so archive a pinned item in the UI if you want it gone.

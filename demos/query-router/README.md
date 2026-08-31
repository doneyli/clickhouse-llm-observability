# Query Router — front-door classification-dispatch (Pattern 2: Routing)

A thin front door that **classifies** an incoming question and **dispatches** it
over HTTP to exactly one of three existing demos, each a genuinely specialized
handler — plus an in-process fallback with confidence-threshold gating and
human-in-the-loop escalation. Routing is the *star*: the router decision is a
first-class, scorable Langfuse observation, not a log line.

> **Why a new demo?** Routing already existed *embedded* inside `agentic-rag`
> (`route_node`) and `brand-promo-multi-agent` (`_classify_intent_node`), but
> those walkthroughs are about something else. This demo puts the router
> decision — confidence, misroute scores, a router experiment — on the marquee.

## What it demonstrates

| Capability | How |
|---|---|
| Router decision as a scorable object | `route-query` **generation** with `{route, confidence, rationale}` in output + `metadata.route`, prompt-linked to the managed `query-router-classifier`, with a runtime `router_confidence` score |
| Genuinely specialized handlers | `analytics_sql` → text-to-sql (:8002), `docs_simple` → vector-rag (:8003, the cheap tier), `docs_complex` → agentic-rag (:8006, the expensive self-correcting tier) — different prompts, stacks, tools, cost/quality tiers |
| Distributed trace nesting | one `route-and-dispatch` trace shows the router decision AND the chosen handler's full existing subtree, joined cross-process via SDK v3 `trace_context` |
| Confidence gating + fallback | sub-threshold / out-of-scope / malformed / unknown-route / handler-down all divert to a best-effort `fallback-handler` |
| HITL escalation | an `escalate-to-human` **event** carries a machine-readable `reason` for a triage queue |
| Misroute curation | misroutes recorded as post-hoc **scores** (`routing_correct`, BOOLEAN) — *not* tags (tags are immutable at creation) |
| Router-accuracy loop | dedicated `query-router-accuracy` dataset + an experiment that varies **only** the router (prompt label / model), scored by a deterministic code eval + a categorical judge |

## Route taxonomy

| Route | Handler | Specialization the router protects |
|---|---|---|
| `analytics_sql` | text-to-sql :8002 | live numbers over ClickHouse public datasets |
| `docs_simple` | vector-rag :8003 | cheap, fast single-shot doc lookup (low-cost tier) |
| `docs_complex` | agentic-rag :8006 | multi-part / comparative / verification-worthy questions (expensive tier) |
| `fallback` | in-process | out-of-scope, low confidence, malformed output, or handler down → best-effort + escalation |

## Who it's for

SAs showing **routing / classification-dispatch** and the *full AI-engineering
loop* on the cheapest possible surface (a one-call classifier): a customer asking
"where does a classifier decide something in my stack today, with no trace of
why?" or "what does your system do when the classifier isn't sure?".

## Quick start

```bash
# The router dispatches over HTTP, so bring up the handlers too:
docker compose --profile langfuse --profile demo up -d   # router + 3 handlers

# Seed the loop artifacts (idempotent):
python scripts/seed-router-prompt.py      # managed router prompt (v1 baseline + v2 production)
python scripts/seed-router-dataset.py     # query-router-accuracy dataset (~30 items)
python scripts/seed-router-history.py     # 14 days of backdated history for the dashboard
./scripts/seed-router-judge.sh            # categorical route-plausibility judge
./scripts/seed-code-evaluators.sh         # registers evaluators/route-match.ts

# Run the batch demo (10 questions covering every route + edge cases):
docker compose --profile demo run --rm query-router python main.py
# or interactive:
docker compose --profile demo run --rm query-router python main.py --interactive

# Compare router variants (vary ONLY the router):
python scripts/run-router-experiment.py --sample 30
```

See [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) for the presenter runbook.

## Layout

```
demos/query-router/
├── router.py            # classify -> {route, confidence, rationale}; gate on confidence
├── handlers.py          # route registry + HTTP dispatch + in-process fallback + escalation
├── server.py            # FastAPI /health + /query; run() = classify -> dispatch under one trace
├── main.py              # CLI batch (DEMO_QUESTIONS) + --interactive
├── langfuse_config.py   # v3 wiring: observe(), scores, managed prompt, trace_context — no-ops without keys
├── scripts/score_misroute.py   # record routing_correct as a POST-HOC score (+ pin to dataset)
├── tests/               # classifier / gating / dispatch / fallback / server unit tests (HTTP stubbed)
├── Dockerfile · requirements.txt
└── README.md · DEMO_SCRIPT.md
```

Related root scripts: `scripts/seed-router-prompt.py`, `scripts/seed-router-dataset.py`,
`scripts/seed-router-history.py`, `scripts/run-router-experiment.py`,
`scripts/seed-router-judge.sh`, `evaluators/route-match.ts`.

## Graceful degradation

Everything runs without Langfuse keys (`langfuse_config` no-ops) and without a
managed prompt (a local fallback prompt ships in `router.py`). If a handler
container is down, its route degrades to the fallback + escalation instead of
erroring — by design (see Act 2 of the DEMO_SCRIPT).

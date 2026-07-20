# Property Concierge — Real-Estate Agent, observed end-to-end with Langfuse

A self-contained demo of an **agentic real-estate assistant** instrumented with
**Langfuse**, built to showcase the **entire [AI Engineering
loop](https://langfuse.com/academy/ai-engineering-loop)** — not just a feature
tour — from the perspective of an online property marketplace:

**Trace** an instrumented tool-using agent → **Monitor** it with scores &
dashboards → build an **evaluation dataset** → **Experiment** across models
*and* prompt versions → **Evaluate** with code evals, LLM judges & human
annotation → **Deploy** the winning prompt by label (and via GitHub CI/CD) →
new traces → repeat. Plus a **show-able web portal** that drives the exact same
agent.

Everything targets a dedicated Langfuse project named **`real-estate`** on
`http://localhost:3001`.

> **How the pieces map to the loop:** [`AI_ENGINEERING_LOOP.md`](AI_ENGINEERING_LOOP.md)
> — the step-by-step map + the closing "Deploy" node.
> **Presenting this?** Follow [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md) — the ordered,
> copy-pasteable runbook with talking points and a capability→moment map.

---

## What it demonstrates

| Capability | How |
|---|---|
| Agent tracing (nested spans) | `plan → agent-turn → tool:* → synthesis` per turn |
| Generations with token usage **and € cost** | every LLM call is a `generation` observation |
| Tool observability | each tool call is its own span with input/output |
| **Multi-turn conversation = one trace** | each turn is a `turn-N` observation in the same `traceId` |
| Sessions | group a user's conversations by `session_id` |
| Tags / users / metadata | every trace tagged `real-estate` + a user id |
| **Scores on individual observations** | 5 deterministic **code** scores on the synthesis obs |
| **Managed LLM-as-a-Judge** (native, automatic) | Helpfulness / Relevance, run by the Langfuse worker on `real-estate` traces |
| **Custom SDK judges** | groundedness / tone pushed from our own code |
| **User feedback** (👍/👎) | portal thumbs write a `user-feedback` score onto the trace (Monitor signal) |
| **Human annotation** | queue + score configs (reviewer-verdict, expert-usefulness) |
| Datasets | `property-concierge-eval`, 18 curated items |
| Experiments / runs + aggregates | `dataset.run_experiment(...)` with run-level averages |
| **Model comparison** | same agent + evals on Claude vs GPT-4o → compare runs |
| **Prompt management** (versioned, labelled) | system prompts fetched by label from Langfuse; **linked to every generation** |
| **Prompt-variant experiment** | same agent + evals on `production` vs `candidate` prompt → compare runs |
| **Deploy** (close the loop) | promote a prompt label to ship it; GitHub CI/CD reference in [`cicd/`](cicd/) |
| Evals that catch problems | fault-injected traffic scores low on the right metric |

---

## The use case

A property-search concierge for an online real-estate marketplace. A user asks in
natural language (EN or ES) — *"2-bed flat to buy in Madrid under €400k near a
metro, and the mortgage"* — and the agent runs an Anthropic **tool-use loop** over
a synthetic catalog of homes across major European cities (Madrid, Barcelona,
Valencia, Seville, Málaga, Bilbao, Lisbon, Paris, Berlin, Amsterdam, Rome, Vienna,
Dublin and Athens):

- `search_listings` — filter the catalog by city, buy/rent, price, bedrooms, features
- `get_listing_details` — full record for one listing
- `calculate_mortgage` — monthly-payment estimate
- `neighborhood_insights` — price/m², transport, schools, safety, vibe

It then writes a grounded recommendation citing real listing ids.

---

## Setup

Prerequisites: a Langfuse instance — this repo's main stack on `localhost:3001`
(default) **or a Langfuse Cloud project** (see below) — and Python 3.11+. For the full feature set, the `real-estate` project
needs, under **Settings → LLM Connections**, an **Anthropic** connection (powers
the managed judges) and an **OpenAI** connection (optional; for the GPT run).

```bash
cd demos/real-estate
python3.11 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

`.env` (gitignored) holds the `real-estate` project keys, an Anthropic key, and
`OPEN_AI_API_KEY` (so the agent can run on GPT). `agent/config.py` **verifies at
runtime** that the keys resolve to the `real-estate` project and refuses to run
otherwise — so the demo can never silently pollute another project.

### Using Langfuse Cloud instead

The demo runs against a [Langfuse Cloud](https://cloud.langfuse.com) project
just as well — no local Langfuse stack needed:

1. Create a project (name it `real-estate`, or set `LANGFUSE_PROJECT_NAME` in
   `.env` to whatever you named it) and grab its API keys.
2. In `.env`, set `LANGFUSE_HOST=https://cloud.langfuse.com` (EU) or
   `https://us.cloud.langfuse.com` (US) plus that project's keys.
3. Run `./run_demo.sh` as usual.

Everything seeds through the public API — prompts, dataset, live traffic,
experiments, annotation queue, and all code/SDK-judge scores — identically to
self-hosted. The one difference: **managed LLM-as-a-Judge evaluators** have no
public API, so `seed_managed_evaluators.sh` detects the remote host, upserts
the Anthropic LLM connection via API, and prints the ~2-minute UI recipe for
the two judges (Helpfulness/Relevance on tag `real-estate`) instead of seeding
them into the local Postgres.

**Switching targets:** keep one env file per backend (e.g. `.env.cloud`
alongside your self-hosted `.env`, both gitignored) and copy the one you want
into place: `cp .env.cloud .env`. Everything — scripts, portal, experiments —
follows `.env`. Restart the portal after switching.

**Or send to both at once:** set the three `LANGFUSE_MIRROR_*` variables in
`.env` (see `.env.example`). Every span is then exported to the mirror as
well — **same trace ids on both backends** (one extra OTLP exporter on the
same tracer provider) — and every code/judge/user-feedback score is duplicated
via the mirror's public API. Prompts, datasets, experiments and managed
evaluators remain primary-only; the mirror is best-effort and never blocks the
demo if it's unreachable.

---

## Run it

```bash
# 1) prep all the Langfuse data (dataset + live traffic + experiment)
./run_demo.sh                 # ~6–10 min   (--quick skips the experiment)

# 2) launch the portal (the app you show)
./run_portal.sh               # http://localhost:8080
```

Or run each piece individually:

```bash
./.venv/bin/python scripts/seed_prompts.py            # prompts → Langfuse (production + candidate)
./.venv/bin/python scripts/seed_dataset.py            # create the 18-item dataset
./scripts/seed_managed_evaluators.sh                  # native LLM judges (auto, Anthropic)
./.venv/bin/python scripts/run_live_traffic.py        # ~13 traces + sessions + code/custom scores
./.venv/bin/python scripts/seed_annotation_queue.py   # human-review queue + score configs
./.venv/bin/python scripts/run_experiment.py --model claude-sonnet-4-6   # Claude run
./.venv/bin/python scripts/run_experiment.py --model gpt-4o              # GPT run (compare models)
./.venv/bin/python scripts/run_experiment.py --prompt-label candidate    # candidate prompt (compare prompts)
./.venv/bin/python scripts/smoke_test.py              # sanity: keys + obs-level scores
```

---

## How the pieces fit

```
webapp/            FastAPI portal + single-page UI  ─┐
scripts/run_live_traffic.py  (realistic traffic)    ─┼─▶ agent/concierge.py ──▶ Langfuse traces
scripts/run_experiment.py    (dataset run)          ─┘        (run_turn)         (project: real-estate)
                                                            ▲   │
agent/prompts.py    system prompts fetched by label ────────┘   │ observation-level CODE scores (live)
                    (production/candidate) + fallback           │ + prompt version LINKED to each generation
agent/tools.py      4 tools over agent/catalog.py               │
agent/scoring.py    code evaluators + LLM judges  ◀─────────────┘ trace-level LLM-judge scores (live)
evaluators/         adapters exposing scoring as experiment Evaluations + run-level aggregates
data/dataset.py     the 18 evaluation items
```

Key design choices:

- **One provider-agnostic agent, many surfaces.** The portal, the live-traffic
  script and the experiment all call `run_turn(..., model=...)` (`agent/llm.py`
  routes to Anthropic or OpenAI), so the demo shows exactly what runs — and the
  same agent can be evaluated on Claude *and* GPT for the comparison.
- **Prompts live in Langfuse, not in code (the Deploy node).** `agent/prompts.py`
  fetches the system prompts **by label** at runtime (`production` by default) —
  with a hard-coded fallback so the demo still runs offline — and links the
  fetched version to every generation. Promoting a label ships a new prompt with
  no redeploy, and you can `run_experiment.py --prompt-label candidate` to prove a
  change before shipping it. This is what closes the loop; see
  [`AI_ENGINEERING_LOOP.md`](AI_ENGINEERING_LOOP.md) and [`cicd/`](cicd/).
- **A conversation is one trace.** Pass `run_turn(conversation_trace_id=..., turn_index=n)`
  (a deterministic `langfuse.create_trace_id(seed=session_id)`) and every turn lands
  in the *same* trace as a `turn-N` observation — the portal keeps per-session
  history + turn index so follow-ups both carry context and share one `traceId`.
  Single-turn callers (experiment, ad-hoc queries) just omit it and get one trace each.
- **Three scoring layers.** (1) deterministic **code** scores on the *synthesis
  observation* (agent, live mode); (2) **managed** LLM judges
  (Helpfulness/Relevance) run automatically by Langfuse on
  `real-estate` traces via the Anthropic connection; (3) **custom SDK judges**
  (groundedness/tone) pushed by the live-traffic script to complement the managed
  set without name clashes. In experiment mode, scoring is delegated to the
  experiment evaluators (checked against the dataset's ground-truth constraints).
- **The groundedness judge sees all tool evidence** (listings + neighborhood +
  mortgage), not just search results, so legitimately-tooled facts aren't
  mis-scored as fabrication.
- **Cross-turn references stay grounded.** In a multi-turn conversation, a
  listing surfaced in an earlier turn (e.g. a "Madrid vs. Barcelona" comparison
  citing turn 1's listing) is exempt from `grounded-listings` and
  `location-match` — only *newly* recommended listings must match the current
  location. `budget-adherence` deliberately keeps checking every listing cited,
  so an earlier suggestion re-offered after a budget cut is still caught.
- **Fault injection** (`run_live_traffic.py` only) deliberately degrades a few
  answers so scores visibly vary and you can show evals catching real problems:
  a hallucinated id, an over-budget push, a wrong-language reply, plus two
  tool-use failures — `no_search` (no tools bound: zero tool spans) and
  `wrong_tool` (search removed: tool spans, but never a catalog search), both
  scoring `used-search-tool = 0` with a trace tree that agrees. Every faulted
  trace carries a `fault:<name>` tag and a `fault` metadata field, so you can
  filter to a failure mode live and prove the bad traces are seeded.

---

## Files

```
agent/
  config.py       env + key-isolation guard + Anthropic/OpenAI/Langfuse clients
  catalog.py      synthetic listings + neighborhood data
  tools.py        4 tools + Anthropic tool schemas
  llm.py          provider-agnostic LLM layer (Anthropic + OpenAI)
  prompts.py      Langfuse prompt fetch by label + hard fallback (Deploy node)
  concierge.py    the instrumented tool-use agent (run_turn, any model/prompt)
  scoring.py      code evaluators + LLM-as-a-Judge (pure functions -> Score)
evaluators/
  experiment_evaluators.py   Score -> Langfuse Evaluation adapters + run aggregates
data/dataset.py   18 evaluation items
scripts/
  seed_prompts.py            prompts -> Langfuse (production + candidate labels)
  seed_dataset.py            create the dataset
  seed_managed_evaluators.sh native LLM-as-a-Judge (Postgres seed, Anthropic)
  run_live_traffic.py        traces + sessions + code/custom scores + faults
  seed_annotation_queue.py   human-review queue + score configs (public API)
  run_experiment.py          dataset run for a chosen --model / --prompt-label
  smoke_test.py              sanity check
cicd/             GitHub CI/CD reference (repository-dispatch workflow + guide)
webapp/           server.py (FastAPI) + static/index.html (portal UI)
run_demo.sh       prep all data      run_portal.sh   launch the app
AI_ENGINEERING_LOOP.md  the loop, mapped to this demo
DEMO_SCRIPT.md    presenter runbook
```

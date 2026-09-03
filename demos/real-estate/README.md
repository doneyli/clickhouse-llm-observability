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
| **Multi-turn conversation → session** | each turn is its own trace; a shared `session_id` groups them in the Sessions view |
| Sessions | group a conversation's per-turn traces by `session_id` |
| Tags / users / metadata | every trace tagged `real-estate` + a user id |
| **Scores on individual observations** | 5 deterministic **code** scores on the synthesis obs |
| **Managed LLM-as-a-Judge** (native, automatic) | Helpfulness / Relevance, run by the Langfuse worker on `real-estate` traces |
| **Custom SDK judges** | groundedness / tone pushed from our own code |
| **User feedback** (👍/👎) | portal thumbs write a `user-feedback` score onto the trace (Monitor signal) |
| **Human annotation** | queue + score configs (reviewer-verdict, expert-usefulness) |
| **Human annotation of a whole conversation** | a second queue whose items are **sessions** (conversation-outcome + the two cross-turn scores), so a reviewer judges the conversation, not one turn |
| Datasets | `property-concierge-eval`, 18 curated items |
| Experiments / runs + aggregates | `dataset.run_experiment(...)` with run-level averages |
| **Model comparison** | same agent + evals on Claude vs GPT-4o → compare runs |
| **Prompt management** (versioned, labelled) | system prompts fetched by label from Langfuse; **linked to every generation** |
| **Prompt-variant experiment** | same agent + evals across `first-draft` / `production` / `candidate` prompts → compare runs |
| **N+1 conversation eval** | replay a real conversation prefix, score only turn N+1 → `property-concierge-conversations`, 10 items, one per cross-turn failure mode |
| **Simulated conversations** | an LLM plays a difficult buyer; judge the whole trajectory → `property-concierge-personas`, 7 personas |
| **Conversation-level judge** | a `conversation-snapshot` observation on the final turn, scored once per conversation by a managed judge |
| **Session-level score** | `create_score(session_id=…)` — the one score type no managed evaluator can produce |
| **Deploy** (close the loop) | promote a prompt label to ship it — **gated by CI**: a prompt change runs the eval suite and blocks the deploy on a regression ([`cicd/`](cicd/README.md)) |
| Evals that catch problems | fault-injected traffic scores low on the right metric |
| **PII redaction** | emails, phones, IBANs, national ids and card numbers are scrubbed **client-side** before export — the agent sees the real text, Langfuse never does |

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
self-hosted. **Managed LLM-as-a-Judge evaluators** are provisioned too:
`seed_managed_evaluators.sh` detects the remote host, upserts the Anthropic
LLM connection, and creates the two judges (Helpfulness/Relevance) as
observation-level evaluation rules via the
[unstable evaluators API](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge#api),
referencing the Langfuse-managed evaluator families. They score **new**
traffic automatically (allow a few minutes); to score traces ingested before
the rules existed, use the UI backfill: Traces table → select → Actions →
Evaluate. If the API is unavailable, the script prints the manual UI recipe.

**Switching targets:** keep one env file per backend (e.g. `.env.cloud`
alongside your self-hosted `.env`, both gitignored) and copy the one you want
into place: `cp .env.cloud .env`. Everything — scripts, portal, experiments —
follows `.env`. Restart the portal after switching.

**Or send to both at once:** set the three `LANGFUSE_MIRROR_*` variables in
`.env` (see `.env.example`; they are read from the file only, so stray shell
exports can't silently enable mirroring). Every span — live traffic, portal,
*and* experiment runs — is then exported to the mirror as well, with the
**same trace ids on both backends** (one extra OTLP exporter on the same
tracer provider). Scores are duplicated on the live-traffic and portal paths
(code scores, SDK judges, user feedback); experiment evaluation scores,
prompts, datasets, dataset-run linkage and managed evaluators exist only on
the primary — so experiment traces show up on the mirror *without* scores or
run linkage. The mirror is best-effort: if it's unreachable, the demo logs a
warning and continues, adding at most ~3s to a turn.

---

## Run it

> **zsh users:** the `#` annotations below are for reading, not pasting.
> Interactive zsh has `interactive_comments` off by default, so a pasted `#`
> becomes an *argument* rather than a comment (`cp a b  # note` fails with
> `cp: note: Not a directory`). Paste the command only, or run
> `setopt interactive_comments` once.

```bash
# 1) prep all the Langfuse data (dataset + live traffic + experiment)
./run_demo.sh                 # ~6–10 min   (--quick skips the experiment)

# 2) launch the portal (the app you show)
./run_portal.sh               # http://localhost:8080
```

Or run each piece individually:

```bash
./.venv/bin/python scripts/seed_prompts.py            # prompts → Langfuse (first-draft + production + candidate)
./.venv/bin/python scripts/seed_dataset.py            # create the 18-item dataset
./scripts/seed_managed_evaluators.sh                  # native LLM judges (auto, Anthropic)
./.venv/bin/python scripts/run_live_traffic.py        # ~13 traces + sessions + code/custom scores
./.venv/bin/python scripts/seed_annotation_queue.py   # 2 human-review queues (traces + sessions) + score configs
./.venv/bin/python scripts/simulate_long_session.py   # one 12-turn session (feeds the conversation queue)
./.venv/bin/python scripts/run_experiment.py --model claude-sonnet-4-6   # Claude run
./.venv/bin/python scripts/run_experiment.py --model gpt-4o              # GPT run (compare models)
./.venv/bin/python scripts/run_experiment.py --prompt-label candidate    # candidate prompt (compare prompts)
./.venv/bin/python scripts/run_experiment.py --prompt-label first-draft  # naive prompt (a VISIBLE win vs production)
./.venv/bin/python scripts/smoke_test.py              # sanity: keys + obs-level scores
./.venv/bin/python scripts/verify_masking.py          # prove PII never reaches Langfuse
```

Judge means carry ±0.03–0.04 run-to-run noise, so before citing any prompt
comparison, run the control — the same prompt twice under different run names:

```bash
./.venv/bin/python scripts/run_experiment.py --prompt-label production --run-name production-repeat
```

Rehearse the CI quality gate locally (this is the exact code Actions runs):

```bash
./.venv/bin/python scripts/prompt_gate.py --prompt-label first-draft   # exits 1 — the gate blocking a bad prompt
./.venv/bin/python scripts/prompt_gate.py --prompt-label production    # exits 0
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

### Evaluating the conversation, not just the turn

A per-turn score cannot tell you the agent forgot a budget the user set four turns
ago. Langfuse cannot close that gap for you either: an LLM-as-a-Judge rule targets
an **observation** or an **experiment**, never a session, because the server has no
way to know a conversation has ended. So the app declares it. Three paths, one
shared score vocabulary:

```
data/conversations.py   10 N+1 items: a real prefix as `history` + the turn under test
   └─ scripts/run_n_plus_1_experiment.py ──▶ replays the prefix, scores ONLY turn N+1
                                             (deterministic: catches a dropped constraint exactly)

data/personas.py         7 personas, each difficult in ONE named way
   └─ scripts/run_simulation_experiment.py ─▶ agent/simulated_user.py plays the buyer
                                             until [[DONE]]; judges the whole trajectory

agent/concierge.py       is_final_turn=True ──▶ `conversation_end` tag (propagated)
                                            └▶ `conversation-snapshot` observation
                                               = the ONE observation a managed
                                                 conversation judge can match
```

- **`stated-constraint-respected`** and **`reference-resolved`** are produced by all
  three: deterministically in N+1, by judge in simulation, by the managed rule on
  live traffic. Same name everywhere, so offline and production are comparable.
- **Why a snapshot observation** rather than history on the root of every turn: an
  observation-level judge sees only the observation it matched, so it needs *one*
  observation holding the whole conversation. Putting it on every root would
  re-judge a growing transcript N times per conversation. The snapshot fires once.
  (`history` *is* also on the root from turn 2 on, for per-turn cross-turn checks.)
- **Session scores** (`create_score(session_id=…)`, see `simulate_long_session.py`)
  are the only way to attach a number to a whole conversation *from code* in
  production. A human gets there through the second annotation queue below.

### The human path: a queue of conversations, not turns

`scripts/seed_annotation_queue.py` seeds **two** annotation queues, because a
queue item's `objectType` decides what the reviewer is shown:

| Queue | Items | Score configs | The reviewer sees |
|---|---|---|---|
| `Property Concierge - human review` | `TRACE` | reviewer-verdict, expert-usefulness | one turn |
| `Property Concierge - conversation review` | `SESSION` | conversation-outcome, stated-constraint-respected, reference-resolved | the whole conversation, turn by turn |

A constraint stated in turn 3 and broken in turn 9 looks fine in *every* single
trace, so a queue of turns structurally cannot catch it. Session items can, and
their scores land on the **session** — the same subject
`create_score(session_id=…)` writes to, and the only human route to it. Two of
the three configs reuse the machine score names on purpose, so the human label is
a gold standard for the automated one (compare by score `source`).

```bash
./.venv/bin/python scripts/seed_annotation_queue.py --only sessions --min-turns 5
```

→ **[CONVERSATION_REVIEW.md](CONVERSATION_REVIEW.md)** for the score schema
rationale, what qualifies as a candidate session, reading the labels back via
`v3/scores`, and the API gotchas (the `sessionId` filter that silently returns
nothing, session-discovery deprecation, no queue-delete endpoint).

Cost warning: `--multi-turn` is opt-in in `run_demo.sh`. A simulated conversation is
up to 6 agent turns + a simulated-user call per turn + 3 trajectory judges, roughly
an order of magnitude more than a single-turn item. The runner prints an upper bound
and refuses to start without `--yes`.

### Keeping PII out of the platform

A concierge collects contact details as a matter of course — "email me the
brochure", "my mobile is…", "the deposit comes from this account" — and every one
of those lands in an LLM payload. [`agent/masking.py`](agent/masking.py) redacts
them **inside this process**, via the SDK's export-stage `mask_otel_spans` hook,
so the sensitive text never reaches Langfuse at all. The agent still sees the
real query; only the exported span changes.

| Redacted | Left alone |
|---|---|
| emails, phone numbers (intl + ES mobile + 3-3-4), IBANs, Spanish NIE/DNI, card numbers | `user_id` — a pseudonymous handle, and the dimension the Users view, sessions and cost chargeback all build on |

On by default. The comparison is the demo:

```bash
./.venv/bin/python scripts/run_live_traffic.py                       # redacted
LANGFUSE_MASK_PII=false ./.venv/bin/python scripts/run_live_traffic.py   # raw, for contrast
./.venv/bin/python scripts/verify_masking.py                         # prove it
```

Four of the live-traffic queries carry PII and are tagged `pii-demo`, so you can
filter straight to them. `verify_masking.py` checks both halves — that the
patterns fire, that prices and listing ids are **not** mangled, and that a real
round trip comes back redacted *with the rest of the payload intact*. That last
assertion is the one that counts: "no PII in the trace" also passes when the
payload was never exported.

Two limits worth stating to a customer rather than letting them assume:

- **Names and street addresses are not caught.** They have no reliable surface
  form; a regex cannot find them. That needs a NER model or an LLM classifier in
  the mask function. A redactor that quietly misses names is worse than none,
  because it buys confidence it hasn't earned.
- **A second exporter gets its own unmasked copy.** The hook only patches spans
  the Langfuse client exports, so masking and trace mirroring are mutually
  exclusive here — `agent/config.py` refuses to start with both on. The
  server-side complement is ingestion masking (self-hosted Enterprise), which
  enforces one policy across every client instead of per application.
- **Server-side judges see the redacted payload.** That is the point, and it is
  also a real trade-off: the managed Helpfulness/Relevance evaluators grade
  `[REDACTED_EMAIL]`, not the address. It is why the tokens are descriptive
  placeholders rather than deletions — a judge reads "an email was here" and
  scores the answer sensibly, where a missing attribute would just look like a
  broken trace. The code evaluators and SDK judges in
  [`agent/scoring.py`](agent/scoring.py) run in-process on the real text, so if
  an eval genuinely needs the sensitive value, that is the layer it belongs in.

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
- **A conversation is a session, not one trace.** Following Langfuse's rule of thumb —
  *one trace = one invocation of your system* — every turn is its **own trace**, and a
  shared `session_id` groups them under a single **Session** (each turn shows up as its
  own trace, in order). Pass `run_turn(session_id=..., turn_index=n)`; the portal keeps
  per-session history + turn index so follow-ups carry context. See
  [Langfuse: traces vs sessions](https://langfuse.com/academy/tracing#traces-vs-sessions).
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
  masking.py      PII redaction — scrubs span payloads before export
  scoring.py      code evaluators + LLM-as-a-Judge (pure functions -> Score)
evaluators/
  experiment_evaluators.py   Score -> Langfuse Evaluation adapters + run aggregates
data/dataset.py   18 evaluation items
scripts/
  seed_prompts.py            prompts -> Langfuse (first-draft + production + candidate labels)
  seed_dataset.py            create the dataset
  seed_managed_evaluators.sh native LLM-as-a-Judge (Postgres self-hosted / API on Cloud)
  run_live_traffic.py        traces + sessions + code/custom scores + faults
  seed_annotation_queue.py   2 human-review queues — TRACE items + SESSION items
  simulate_long_session.py   one 12-turn session + a session-level score
  run_experiment.py          dataset run for a chosen --model / --prompt-label
  prompt_gate.py             CI quality gate: eval a prompt label, exit 1 below the bar
  smoke_test.py              sanity check
  verify_masking.py          PII redaction: policy unit checks + live round trip
  seed_dashboards.py         3 custom dashboards / 26 widgets, as code
cicd/             the CI quality gate: thresholds.json (the bar) + setup guide
webapp/           server.py (FastAPI) + static/index.html (portal UI)
                  PORTAL_PROMPT_LABEL=<label> serves a non-production prompt
run_demo.sh       prep all data      run_portal.sh   launch the app
AI_ENGINEERING_LOOP.md  the loop, mapped to this demo
DEMO_SCRIPT.md    presenter runbook
CONVERSATION_REVIEW.md  human review of whole conversations (session queues)
```

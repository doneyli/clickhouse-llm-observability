# PromoPlanner — Demo Script (multi-agent fleet observability + eval gates)

A ready-to-run demo of a **multi-agent promo-planning assistant** for CPG /
retail audiences — a LangGraph orchestrator delegating to **CrewAI crews**
(research, strategy) and a deterministic compliance graph — observed in
**Langfuse** at **fleet scale**: 50k traces of synthetic history, six agents,
persona dashboards, online judges on live traffic, and an offline **golden
dataset with a certification gate** fit for CI.

- **App:** PromoPlanner — `classify intent → research crew (3 agents + tools) →
  strategy crew (2 agents) → compliance checks → compose brief`
- **Frameworks:** LangGraph (orchestration) + CrewAI (crews) — deliberately
  multi-framework, because real agent estates are mixed
- **Models, tiered by job:** Sonnet for research, **Opus for strategy**, Haiku
  on compliance, Opus as judge — cost-per-role is part of the story
- **Observability backend:** Langfuse (`http://localhost:3001`), standalone
  project — own `uv` env + `.env`
- **Re-themeable:** every brand/region/retailer/query comes from
  `demo.config.yaml` — rebrand it to the prospect in minutes
- **Run length:** 25–30 min full; 10–12 min short path (Acts 1–3)

> Everything lives in `demos/brand-promo-multi-agent/`. The deep reference is
> [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) (60-min segment-by-segment) and
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); this script is the
> customer-facing conversation layered on top.

---

## How to run this script

It's written to be a **conversation, not a walkthrough**. Every act does three
things: it **frames** a problem the audience already has, **shows** how the
platform answers it, and **lands** the benefit — then hands a **question** back to
the room. So each act carries four beats:

- **Frame** — the problem, in their terms (say this *before* you touch the screen).
- **Show** — the exact clicks / commands.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that invites them to map it to their own world.

The short path is Acts 1–3 (scale → one trace → live catch). Acts 4–6 are for
rooms that own evaluation, release gates, or human review.

---

## 0 · Pre-flight (do this BEFORE the meeting)

Standalone demo — own dependencies, own `.env` (never the repo root one):

```bash
cd demos/brand-promo-multi-agent
cp .env.example .env                      # ANTHROPIC_API_KEY + LANGFUSE_* keys
cp demo.config.example.yaml demo.config.yaml
uv sync
uv run scripts/setup_langfuse_project.py  # self-hosted only; skip on Langfuse Cloud
uv run scripts/seed_all.py                # prompts, score configs, dataset, dashboards, queue
uv run scripts/generate_history.py        # 50k synthetic traces (several minutes)
```

Then rehearse the live pieces once (they're the same commands you'll run on
stage):

```bash
uv run scripts/run_live_demo.py play-all                                        # the 5 scripted queries
uv run python scripts/run_experiment.py --run-name rehearsal --sample 10 \
  --evaluators deterministic                                                    # ~30s, no judge cost
```

**Checklist (from the runbook):** Traces list shows ~50k; the 3 dashboards
render; Prompts shows ~12 under `promo-planner/`; dataset
`promo-planner-golden-v1` has 75 items; annotation queue has ~10 items; the
Runs tab is non-empty. **Note:** the online evaluators and dashboards may need
one-time manual creation in the UI — the seed scripts print exact checklists
when the API can't create them. Creating the online judges in the UI requires
an **Anthropic LLM Connection** in the demo's Langfuse project first
(**Settings → LLM Connections**; the judge model is `claude-opus-4-7`) — add
it before working through the evaluator checklist, or the judge setup fails.

**Browser tabs ready:** Traces, the three dashboards (Executive / Ops /
Engineer), Prompts, Datasets → Runs, Annotation Queues — plus a terminal.

> **Demo hygiene:** this demo's `.env` hard-overrides ambient `LANGFUSE_*` shell
> vars (a footgun the container demos have) — but keep the habit: `unset
> LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY` before any demo day.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| **Fleet-scale observability** (50k traces, 6 agents) | Act 1 — Traces list + dashboards |
| **Persona dashboards** (Exec / Ops / Engineer on the same data) | Act 1 |
| **One multi-agent run as one trace** (crews, tools, nested graphs) | Act 2 — a `promo_planner_run` tree |
| **Cost by model tier** (Sonnet research / Opus strategy / Haiku compliance) | Act 2 — per-span model + cost |
| **Deterministic compliance gate, fail-closed** | Act 3 — `q2_compliance_catch` → REJECTED |
| **Evals catch hallucinated facts** (free check + judge) | Act 3 — `q5` → `sku_validity`, factuality |
| **Online judges on live traffic** (sampled) | Act 4 — Evaluators: factuality / tool-correctness / compliance |
| **Golden dataset + experiment + certification gate** (CI-able) | Act 4 — `run_experiment.py --ci` |
| **Prompt A/B on the dataset** | Act 5 — baseline vs `strategy-v2` runs |
| **Human review queue** | Act 6 — ambiguous-factuality traces |
| **Re-theme to the prospect** | Close — `demo.config.yaml` |

---

## Opening · Locate the pain (2 min, no screen yet)

**Frame.** One agent is a demo; a *fleet* is an operation. The moment you have
agents delegating to agents — likely across two frameworks, because that's how
these estates actually grow — three people show up with three different
questions: the exec asks *what is this costing per outcome*, ops asks *which
agent is degrading*, and the engineer asks *which step in which handoff broke*.
Most teams can answer none of them, because the handoffs happen in the dark.

**Ask (these steer the session):**
- "How many agents — or agent-shaped features — do you have live or planned? Do
  any call each other?"
- "When a multi-step run goes wrong today, can you tell *which* agent — or which
  handoff — failed?"
- "Who looks at your LLM ops data — engineers only, or do execs and ops want
  their own view? What do they each ask for?"
- "What has to be true before you'd let an agent's output ship without a human
  reading it?"

**Land.** "This demo is a promo-planning assistant for a consumer-brands
company — one orchestrator, two crews of sub-agents, a compliance gate — with
a year of fleet history behind it. Map what you just told me onto it: scale and
personas first, then one run under the microscope, then the eval machinery that
decides what ships."

---

## Act 1 · The fleet, not the demo (4 min)

**Frame.** Observability tools all look great with twelve traces. Your decision
needs the picture at *operational* scale — and per audience.

**Show.** Langfuse → **Traces**: ~**50,000** runs across six agents
(PromoPlanner plus five fleet agents), with sessions, users, tags, failure
modes. Then the three **Dashboards**, one per persona:

- **Executive — Agent Fleet:** cost and volume by agent, trend lines. "Cost per
  *brief*, not cost per token."
- **Ops — Agent Health:** error and failure-mode rates, latency, degradation by
  agent.
- **Engineer — PromoPlanner Deep Dive:** score histograms and drill-downs
  filtered to the hero agent.

**Land.** "Same data, three altitudes — nobody exports to a spreadsheet to brief
the exec. And the reason fifty thousand traces filter and aggregate this fast is
that Langfuse stores them in **ClickHouse** — this dashboard *is* an analytical
database query, not a pre-baked report."

**Ask.** "Which of these three screens gets looked at Monday morning in your
org — and who's flying blind today?"

---

## Act 2 · One run under the microscope (5 min)

**Frame.** The multi-agent debugging problem in one sentence: *five agents
touched the answer — which one do you blame?* Here's a whole run as one tree.

**Show.** Filter Traces to `promo_planner_run`, open a rich one (the seeded
history has the fullest trees), and walk the delegation:

- `classify_intent` — the orchestrator decides this is a full promo-planning
  request (vs compliance-only, vs out-of-scope refusal).
- **`research_crew`** — three CrewAI agents in sequence: `data_analyst` (calls
  `tool.query_sales`, `tool.query_inventory`), `market_researcher`
  (`tool.market_trends`), `historian` (past promos). Tool inputs/outputs are
  right there in the spans.
- **`strategy_crew`** — `promo_strategist` drafts options, `lift_estimator`
  quantifies them. **Click a generation here and point at the model: Opus.**
  Then a research span: Sonnet. "Expensive model only where the thinking is."
- `compliance_agent` — a nested graph: `brand_check` → `regulatory_check` →
  aggregate to APPROVED / CONDITIONAL / REJECTED.
- `compose_brief` — the final 7-section campaign brief.

**Land.** "One question in, one trace out — every delegation, every tool call,
every model choice with its own cost. 'Which agent do I blame' becomes 'open the
tree and look.' And because model tier is per-span data, *cost by role* is a
chart, not an estimate — that's how you defend running Opus on strategy and
Haiku on compliance."

**Ask.** "In your multi-agent design, where would you spend the expensive model
— and could you prove today that the cheap one is good enough everywhere else?"

> **Presenter note — know your own trace.** Live runs attach the Langfuse
> callback to the LangGraph layer, so they show the orchestrator nodes and its
> generations; the CrewAI crews' *internal* spans are fully modeled in the
> seeded history (which is why you demo the tree on a seeded trace). If someone
> asks: instrumenting CrewAI's LLM layer (litellm) is a known integration gap
> called out in the code — `research_crew.py:22` — and a fair "roadmap for this
> demo" admission. Don't present a live trace as having depth it doesn't.

---

## Act 3 · The agent catches it live (4 min) — the money moment

**Frame.** Multi-agent isn't the point; *controlled* multi-agent is. Two live
runs: one where the system blocks a compliance violation, one where the evals
catch a hallucination.

**Show.** Run the scripted queries (they're real runs, not recordings):

```bash
uv run scripts/run_live_demo.py play q2_compliance_catch
```

A promo idea targeting **children under 12** goes in; the compliance graph
flags it HIGH severity against the regulatory rules and the brief comes back
**REJECTED**, with the specific rule cited. Open the fresh trace: the
`compliance_agent` node shows the finding. Emphasize *how* it checks:
deterministic rules — keyword/regex policy checks in plain Python, no LLM — and
**fail-closed**: if a compliance tool errors, the status is ERROR, never a
silent pass.

```bash
uv run scripts/run_live_demo.py play q5_hallucination_catch
```

The run surfaces **fabricated SKUs**. Two independent layers catch it: the free
deterministic `sku_validity` check (regex against the real SKU catalog) and the
`response_factuality` judge scoring low, rationale attached.

**Land.** "The compliance gate is the shape regulated customers ask for:
deterministic where the policy is mechanical, fail-closed on error, and the
rejection is *in the trace* with the rule that fired. And the hallucination
didn't reach a human downstream — a free check plus a sampled judge flagged it
with an audit trail. Guardrails and evals aren't dashboards here; they're
decisions."

**Ask.** "What's your REJECTED-equivalent — the output that must never ship?
Is that rule executable today, or is it a PDF someone's supposed to remember?"

> **Fallback:** the fleet also has *ambient* realism — ~20% of runs carry
> injected faults (tool timeouts, hallucinated SKUs, compliance rejections) so
> the dashboards and failure-mode filters have something true to show. Fault
> injection is **disabled automatically during experiments/CI** so golden-set
> scores stay honest.

---

## Act 4 · The certification gate (5 min) — "what's allowed to ship"

**Frame.** Everything so far watched production. The harder question is a
release question: *this new prompt / model / agent version — is it allowed to
ship?* That needs a fixed test set and a gate, not vibes. Software teams already
have the mental model: golden dataset = test suite, experiment = test run,
certification gate = the check that blocks the merge.

**Show.** **Datasets → `promo-planner-golden-v1`** — 75 items, stratified by
intent (plan-promo, compare-brands, compliance-only, edge cases, out-of-scope),
each with expected outputs. Then run an experiment live (~30s, no judge cost):

```bash
uv run python scripts/run_experiment.py --run-name live-$(whoami) --sample 10 \
  --evaluators deterministic
```

The terminal prints per-dimension scores — intent accuracy, tool-call match,
compliance status match, SKU validity, brief sanity — and the
**`certification_gate`: PASSED/FAILED** verdict (intent ≥ 0.85, compliance ≥
0.90, factuality ≥ 0.80 when the judge runs). In the UI: **Datasets → Runs** →
open the run → click a low-scoring item → its full trace, judge rationale in
the score comment.

Then the one flag that makes it CI:

```bash
uv run python scripts/run_experiment.py --run-name ci-check \
  --evaluators deterministic --ci     # exit code 1 if the gate fails
```

**Land.** "That exit code is the whole story: the same gate a human reads in
the terminal *blocks the pipeline* in CI. Judges add the semantic dimensions
when you want them — sampled online at 10% on live traffic, in full on the
golden set before a release. Every score drills to the exact run that produced
it, so 'why did the gate fail' is a click, not an investigation."

**Ask.** "What are your gate's three thresholds — and who gets to set them?
If a release had been blocked by an eval last quarter, which incident would it
have prevented?"

---

## Act 5 · A/B a prompt change on the same yardstick (3 min)

**Frame.** Someone proposes a "better" strategy prompt — more margin
discipline. Better by what measure?

**Show.** Run the candidate against the same dataset and evaluators, labeled:

```bash
uv run python scripts/run_experiment.py --run-name strategy-v2 \
  --label strategy-v2 --system-prompt-file prompts/strategy_v2.md --sample 10 \
  --evaluators deterministic
```

**Datasets → Runs**: baseline and `strategy-v2` side by side, dimension by
dimension. Same items, same evaluators, one variable.

**Land.** "Prompt engineering becomes an experiment with a control group. Ship
the candidate if the gate holds and the target metric moves; if a dimension
regresses, you found out here — not from a customer."

**Ask.** "What's the prompt change your team has been *afraid* to make? This is
what makes it safe to try."

---

## Act 6 · The human layer (2 min)

**Frame.** The judge scored some runs 0.6–0.8 on factuality — too low to trust,
too high to discard. Exactly the traffic a human should see.

**Show.** **Annotation Queues → PromoPlanner Human Review**: ~10 pre-routed
ambiguous traces plus anything an experiment flagged (`--queue-failures` routes
low scorers in automatically). Open one, apply the `human_brief_review` verdict
(Approved / Needs Revision / Rejected) — it lands as a score on the trace, next
to the automated ones.

**Land.** "Humans review the *ambiguous slice*, not a random sample — and their
verdicts accumulate into the calibration set that tells you whether the judge
itself can be trusted."

**Ask.** "Who would own this queue for you — and how big does the ambiguous
slice have to get before today's process breaks?"

---

## Close · Make it theirs (1 min)

Land three takeaways: **multi-agent, multi-framework work is observable as one
tree, at fleet scale, per persona**; **quality is enforced, not admired** — a
fail-closed compliance gate live, plus a certification gate with a CI exit
code; **the same yardstick scores production (sampled judges) and releases
(golden set)**. Then the kicker: open `demo.config.yaml` — brands, regions,
retailers, regulators, demo queries. "This becomes *your* company's demo by
editing this file — same fleet, your brands, your compliance rules. The repo is
public; clone it and re-theme it."

---

## Under the hood — how it's instrumented (for the "show me the code" moment)

All in `demos/brand-promo-multi-agent/src/`. The interesting part is how *both*
frameworks land in one trace, and how the eval layers connect to the gate.

**1 · One handler + reserved metadata keys — `src/observability.py`**
```python
# observability.py:48   make_observability_callbacks() → the whole tracing integration
return [CallbackHandler()]                                       # :77
# observability.py:80   make_observability_run_metadata() — v4 reserved keys
md["langfuse_user_id"] = user_id   # + langfuse_session_id, langfuse_tags   :100
```
*Why it matters:* attach `{callbacks, metadata, tags}` once at the orchestrator
root and LangGraph propagates it to every nested LLM call — session, user and
tags with no per-node code.

**2 · Tracing rides the graph config — `src/agents/orchestrator.py:275`**
```python
return _orchestrator.invoke(initial_state, config=config)   # config carries callbacks
```

**3 · The honest seam — CrewAI's LLM layer — `src/agents/research_crew.py:22`**
```python
del callbacks   # "LangChain callbacks do not attach to crewai.LLM" (litellm-based)
```
*Why it matters:* this is the Act 2 presenter note in source form — the CrewAI
crews run under the orchestrator but their internal LLM calls aren't captured
live; the seeded history models them explicitly. Good faith beats hand-waving
when a sharp audience reads the trace.

**4 · Fault injection with an off-switch — `src/tools/error_injection.py:35`**
```python
def maybe_inject(...):   # seeded RNG; ~20% of live/synthetic runs carry a fault
    if os.getenv("PROMO_DISABLE_FAULT_INJECTION"): return None   # :44 — set by run_experiment.py
```
*Why it matters:* realism on the dashboards, honesty in the experiments — the
gate never grades an injected failure.

**5 · Judge + the gate — `src/evals/evaluators.py`**
```python
# evaluators.py:250  _call_judge → Opus, returns {score, rationale}; rationale → score comment
# evaluators.py:465  promo_certification_gate: PASS iff every PRESENT dimension ≥ threshold
```
*Why it matters:* six deterministic evaluators are plain functions; four judges
share one call path; the gate is ~60 readable lines — the thing that blocks CI
is code your team can review.

**6 · Prompt A/B mechanism — `src/agents/strategy_crew.py:39`**
```python
prompt_override = os.getenv("PROMO_SYSTEM_PROMPT_OVERRIDE", "").strip()   # set from --system-prompt-file
```
*Why it matters:* the Act 5 experiment changes exactly one variable, verifiably.

> One-liner for the room: *"One callback at the orchestrator root, metadata for
> attribution, evaluators as plain functions, and a gate that exits non-zero.
> Everything you watched is those four things."*

---

## Talking points & objections

- **"Is the 50k history real?"** It's seeded synthetic history — and say so
  unprompted. It exists so you evaluate the platform at operational scale
  (dashboards, filters, score distributions) instead of extrapolating from
  twelve traces. The live runs in Act 3 are real end-to-end executions; the
  failure-mode mix in the history matches the live fault-injection
  distribution.
- **"Two frameworks — gimmick or real?"** Real estates are mixed; that's the
  point. LangGraph orchestrates, CrewAI runs the crews, one trace shows both —
  and the one seam (CrewAI's internal LLM calls on live runs) is called out in
  the code rather than papered over.
- **"Our compliance rules are more complex than keywords."** Good — the
  architecture is the point, not the regex: a deterministic, fail-closed gate
  *inside the agent graph*, whose verdict and evidence land in the trace. Swap
  the check functions for your policy engine; the observability doesn't change.
- **"Judges are expensive."** Layered on purpose: six deterministic evaluators
  run free on everything; judges run sampled (10%) online and in full only on
  the 75-item golden set. A full judged experiment is ~$10–15 — a knowable,
  boundable release cost.
- **"Can this gate our actual CI?"** `--ci` exits non-zero on gate failure —
  wire it into any pipeline today. The gate thresholds are flags, so the
  quality bar is versioned config.
- **"We're not in CPG."** Every domain artifact — brands, regions, retailers,
  regulators, the demo queries — comes from `demo.config.yaml`. Re-theming to
  your industry is an edit, not a rewrite.
- **"Why is this standalone?"** It manages its own `uv` env and Langfuse
  project so it can't disturb the shared stack — same pattern as the
  real-estate demo. It points at the same local Langfuse.
- **"Where does all this data live?"** Langfuse stores traces and scores in
  **ClickHouse** — which is why the fleet-scale filtering and the persona
  dashboards hold up, and why your observability data sits in an engine you can
  also query directly.

---

## Reset / re-run

```bash
uv run scripts/seed_all.py                    # re-seed everything (dataset upserts by hash; prompt seeding mints a new version each run)
uv run scripts/generate_history.py            # append more synthetic history (--total N --seed 42)
uv run scripts/run_live_demo.py play-all      # regenerate the 5 live traces
uv run python scripts/run_experiment.py --run-name fresh --sample 10 --evaluators deterministic
PYTHONPATH=. uv run --with pytest pytest tests/   # regression tests (evaluators, fault injection)
```

No teardown script ships — "reset" is re-seeding (safe to repeat, though each
run adds prompt versions) or clearing the Langfuse project in the UI. Health
check: `curl http://localhost:3001/api/public/health`.

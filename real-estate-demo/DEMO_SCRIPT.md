# Property Concierge — Demo Script (Langfuse end-to-end)

A ready-to-run demo of an **agentic real-estate assistant** fully instrumented
with **Langfuse**. It shows the whole observability + evaluation lifecycle from
the angle of a real-estate marketplace: a live app → traces → scores on
observations → managed & custom evaluators → human annotation → a dataset →
an experiment that **compares two model providers**.

- **App the customer sees:** a property-search portal (`http://localhost:8080`)
- **Observability backend:** Langfuse project **`real-estate`** (`http://localhost:3001`)
- **Models:** agent runs on **Claude (`claude-sonnet-4-6`)** and **GPT (`gpt-4o`)**; judges on Claude
- **Run length:** 15–20 min full; 6–8 min short path

> The agent, tools, evaluators and portal all live in `real-estate-demo/`. Every
> surface (portal, live-traffic, experiment) drives the **same** instrumented,
> provider-agnostic agent — so what you show is what runs in production.

---

## 0 · Pre-flight (do this BEFORE the meeting)

Requires the Langfuse stack up on `:3001`, and in the `real-estate` project
**Settings → LLM Connections**: an **Anthropic** connection (powers managed
judges) and an **OpenAI** connection. The raw OpenAI key must also be in
`real-estate-demo/.env` as `OPEN_AI_API_KEY` (so the agent can run on GPT).

```bash
cd real-estate-demo
python3.11 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   # one-time
./.venv/bin/python -c "from agent.config import verify_project; verify_project()"

# Seed dataset + managed evaluators + live traffic + annotation queue +
# BOTH experiment runs (Claude and GPT).  ~10–15 min.
./run_demo.sh
```

Then start the app and leave it open:

```bash
./run_portal.sh        # http://localhost:8080
```

**Two browser tabs ready:** the portal (`:8080`) and Langfuse (`:3001`, login
`demo@example.com` / `demodemo1!`, project **real-estate**).

---

## Capability → moment map (what "show everything" means)

| Langfuse capability | Where in the demo |
|---|---|
| **Tracing** an agent (nested spans) | Act 2 — `plan → agent-turn → tool:* → synthesis` |
| **Generations** w/ token usage **+ €cost** | Act 2 — click any generation |
| **Tool spans** | Act 2 — `tool:search_listings`, `tool:calculate_mortgage`, … |
| **Multi-turn conversation = ONE trace** | Act 2 — `turn-1 / turn-2 / turn-3` observations in a single trace |
| **Sessions** (group conversations) | Act 2 — Sessions → `sess-madrid-buyer-001` |
| **Tags / Users / Metadata** | Act 2 — filter by tag `real-estate`; `agent_model` in metadata |
| **Scores on individual observations** (code evals) | Act 3 — 5 code scores on the synthesis obs |
| **Managed LLM-as-a-Judge** (auto, native) | Act 3 — Helpfulness/Relevance run by Langfuse |
| **Custom SDK judges** | Act 3 — groundedness/tone pushed from our code |
| **Evals catch real problems** | Act 3 — fault-injected traces score low |
| **Human annotation** (queues + score configs) | Act 4 — reviewer verdict + usefulness rating |
| **Datasets** | Act 5 — `property-concierge-eval` (10 items) |
| **Experiments / runs + aggregates** | Act 5 — Runs tab |
| **Model comparison** (Claude vs GPT) | Act 5 — compare the two runs side-by-side |

---

## Act 1 · The app (2 min) — "here's what our customer ships"

Open the **portal** (`http://localhost:8080`). Click an example chip, e.g.:

> *"2-bed flat to buy in Madrid under €400k near a metro, and the mortgage"*

**Say:** "This is a property-search assistant — an LLM agent a marketplace puts
in front of buyers. Natural language, English or Spanish. It calls tools:
searches listings, pulls neighborhood insights, estimates a mortgage, then
writes the recommendation."

While it responds, point out the property **cards**, the **🔧 tool pills**, and
the **🔍 View trace in Langfuse** button.

Now **ask a follow-up in the same chat** — e.g. *"what would the mortgage be on
that one?"* — to show it carries context. **The button opens the SAME trace**:
the whole conversation is one trace. → click it.

---

## Act 2 · The trace (4 min) — "full visibility into one conversation"

The chat you just ran is **one trace**, with each exchange as a `turn-N`
observation. Walk it top → bottom:
- root trace `conversation` — **input** = first question, **output** = latest
  answer, metadata `agent_model`; grouped under a **session**.
- `turn-1`, `turn-2`, … — one per user message; the follow-up resolved "that one"
  because the agent gets the conversation so far.
- inside a turn: `plan` (extracts constraints) → `agent-turn-N` (reasoning /
  tool decisions) → `tool:*` (**click one** — exact input/output, "no black box")
  → the final synthesis generation.
- Click a **generation** → **token usage** and **€ cost** per step, model, latency.

Then: **Sessions** → `sess-madrid-buyer-001` (groups a buyer's conversations);
**Tracing** list → filter by tag `real-estate`, note user ids + metadata.

---

## Act 3 · Scores — three layers (5 min) — "did it do a *good* job?"

On a trace, open the **Scores** panel. Point out the three layers:

1. **Code evaluators** (deterministic, on the *synthesis observation*):
   `used-search-tool`, `grounded-listings`, `budget-adherence`,
   `location-match`, `language-match`. "Cheap, exact checks on every trace — did
   it search? is every listing real *and* retrieved? within budget? right city?
   right language? Code is perfect for mechanical truths."

2. **Managed LLM-as-a-Judge** (Langfuse-native, **automatic**):
   `Helpfulness`, `Relevance`. **Say:** "These run inside Langfuse — I configured
   them once under **Evaluators**, pointed them at the Anthropic connection, and
   now they score every `real-estate` trace automatically. No code in our app."
   → show the **Evaluators** page. (Both score 1 = good, consistent with the code
   scores — Langfuse also ships an inverted `Hallucination` template where 1 =
   bad; we use our own `groundedness` judge instead to keep one direction.)

3. **Custom SDK judges**: `groundedness`, `tone` — "and where we want a bespoke
   judge, we push it from our own code. Managed + custom coexist."

**Money moment — evals catch problems.** Filter Traces by tag **`fault-demo`**:
- `hallucinate` → `grounded-listings = false` (recommended a non-existent id).
- `over_budget` → `budget-adherence < 1` (pushed an over-budget option).
- `wrong_language` → `language-match = false` (Spanish question, English answer).

"When the agent misbehaves, scores go red automatically — alert on them, or
route low scorers to human review… which is the next stop."

---

## Act 4 · Human annotation (2 min) — "put a human in the loop"

**Annotation Queues** → **Property Concierge - human review**. Open an item.

**Say:** "Automated scores get you 90% of the way; some judgments need a human.
This queue holds real traces for a reviewer, with a defined **score schema**:
a categorical **reviewer-verdict** (approve / minor-issues / reject) and a 1–5
**expert-usefulness** rating." Annotate one live to show the flow — the score
lands on the trace next to the automated ones. "Human labels here become the
gold set you calibrate your LLM judges against."

---

## Act 5 · Dataset + model comparison (5 min) — "prove it, and pick a model"

**Datasets → `property-concierge-eval`** (10 items): show an `input` question and
`expected_output` (criteria + ground-truth constraints). "A curated test set —
buy/rent, EN/ES, several cities, plus one deliberately impossible request."

**Runs** tab → there are **two runs**: `claude-sonnet-4-6` and `gpt-4o`.

**Say:** "We ran the *same* agent and the *same* evaluators against all 10
questions — once on Claude, once on GPT-4o. Same tools, same prompts; only the
model changed." Point at the per-run **aggregate scores**.

→ Select **both runs → Compare**. "This is the payoff: a like-for-like,
metric-by-metric comparison of two model providers on *our* use case —
grounding, budget adherence, helpfulness, relevance. This is how you choose a
model, or catch a regression before it ships." Click into a run item → its trace
→ per-item scores.

Re-run any time to add another candidate:
```bash
./.venv/bin/python scripts/run_experiment.py --model claude-sonnet-4-6 --run-name candidate-v2
```

---

## Optional close · Dashboards (1 min)

Langfuse **Dashboards** → cost, tokens, latency, and score trends over time.
"Once traffic flows, this is your production control room."

---

## Talking points & objections

- **"Tied to a framework?"** No — plain Python + the provider SDKs, instrumented
  with the Langfuse SDK (`start_as_current_observation`). Langfuse also has
  native integrations and OpenTelemetry.
- **"Provider-agnostic?"** Yes — the same agent runs on Claude or GPT (`agent/llm.py`);
  that's what powers the comparison.
- **"Do evals need an LLM?"** No — code evaluators are deterministic and free.
  LLM judges (managed or custom) are for subjective quality.
- **"Can judges run automatically?"** Yes — the managed evaluators in Act 3 run
  on live traffic with no app code, via the LLM connection.
- **"Where does data live?"** Langfuse stores traces in **ClickHouse** — that's
  what makes search, score analytics and dashboards fast at scale.
- **Alternative chat surface:** the stack also ships **LibreChat**
  (`http://localhost:3080`); the instrumentation story is identical.

---

## Reset / re-run

```bash
./run_demo.sh                 # regenerate everything (idempotent)
./run_demo.sh --quick         # traffic + evaluators + annotation, skip experiments
./run_demo.sh --no-gpt        # skip the GPT comparison run
./.venv/bin/python scripts/run_experiment.py --model gpt-4o --run-name gpt-rerun
```

# Property Concierge — Demo Script (Langfuse end-to-end)

A ready-to-run demo of an **agentic real-estate assistant** fully instrumented
with **Langfuse**, staged as the full **[AI Engineering
loop](https://langfuse.com/academy/ai-engineering-loop)** for a real-estate
marketplace. The acts follow the loop:

**Trace** (Act 2) → **Monitor/Evaluate** (Acts 3–4) → **Datasets + Experiment**
(Act 5) → **Deploy** a better prompt to close the loop (Act 6).

- **App the customer sees:** a property-search portal (`http://localhost:8080`)
- **Observability backend:** Langfuse project **`real-estate`** (`http://localhost:3001`)
- **Models:** agent runs on **Claude (`claude-sonnet-4-6`)** and **GPT (`gpt-4o`)**; judges on Claude
- **Prompts:** fetched from Langfuse **by label** (`production`/`candidate`) — versioned & deployable
- **Run length:** 18–24 min full; 6–8 min short path

> The agent, tools, evaluators and portal all live in `real-estate-demo/`. Every
> surface (portal, live-traffic, experiment) drives the **same** instrumented,
> provider-agnostic agent, running the **same Langfuse-managed prompt** — so what
> you show is what runs in production. See [`AI_ENGINEERING_LOOP.md`](AI_ENGINEERING_LOOP.md)
> for the full step→artifact map.

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

# Seed prompts (production+candidate) + dataset + managed evaluators + live
# traffic + annotation queue + BOTH experiment runs (Claude and GPT). ~10–15 min.
# Add --prompt-variant to also pre-run the candidate prompt for Act 6.
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
| **User feedback** (👍/👎 → score) | Act 1 click; Act 3 shows it on the trace |
| **Evals catch real problems** | Act 3 — fault-injected traces score low |
| **Human annotation** (queues + score configs) | Act 4 — reviewer verdict + usefulness rating |
| **Datasets** | Act 5 — `property-concierge-eval` (10 items) |
| **Experiments / runs + aggregates** | Act 5 — Runs tab |
| **Model comparison** (Claude vs GPT) | Act 5 — compare the two runs side-by-side |
| **Prompt management** (versioned, labelled, linked to traces) | Act 2 (prompt on a generation) + Act 6 (Prompts tab) |
| **Prompt-variant experiment** (production vs candidate) | Act 6 — compare prompt runs |
| **Deploy / close the loop** (promote a label; GitHub CI/CD) | Act 6 — promote candidate → production |

---

## Act 1 · The app (2 min) — "here's what our customer ships"

Open the **portal** (`http://localhost:8080`). Click an example chip, e.g.:

> *"2-bed flat to buy in Madrid under €400k near a metro, and the mortgage"*

**Say:** "This is a property-search assistant — an LLM agent a marketplace puts
in front of buyers. Natural language, English or Spanish. It calls tools:
searches listings, pulls neighborhood insights, estimates a mortgage, then
writes the recommendation."

While it responds, point out the property **cards**, the **🔧 tool pills**, the
**👍/👎 Helpful?** buttons (real user feedback — becomes a Langfuse score on this
trace; the loop's Monitor signal), and the **🔍 View trace in Langfuse** button.

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
- Click a **generation** → **token usage** and **€ cost** per step, model, latency,
  and the **Prompt** it used (`property-concierge-agent` v1) — the prompt is
  version-linked to the generation, so quality later ties back to a prompt version.

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

Plus a human signal: if you clicked **👍/👎** in Act 1, a `user-feedback` score is
on this trace too. "Automated evals *and* real user judgement, side by side — you
can chart a satisfaction rate and route 👎 traces to review or into the dataset."

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

Re-run any time to add another candidate model:
```bash
./.venv/bin/python scripts/run_experiment.py --model claude-sonnet-4-6 --run-name candidate-v2
```

---

## Act 6 · Close the loop — deploy a better prompt (4 min) — "and then ship it"

This is the part that makes it a **loop**, not a one-way pipeline. So far the
agent's system prompt has come from Langfuse, not from code — fetched **by
label** (`property-concierge-agent` @ `production`) at runtime.

**Prompts tab** → `property-concierge-agent`. Show there are **two versions**:
`production` (baseline) and `candidate` (tighter grounding + budget discipline +
strict language + a scannable format). "We didn't edit the app to try a new
prompt — it's data in Langfuse, versioned, with the diff right here."

**Say the loop out loud:** "Back in Act 5 the code checks were already perfect,
but the *judge* metrics — helpfulness, relevance, groundedness — sat in the low
0.9s. **That gap is our headroom.** So we hypothesize a clearer, more disciplined
prompt helps, write it as a `candidate`, and prove it on the *same* 10 questions
and evaluators — only the prompt changed. We decide with data, not vibes."

Run the candidate against the eval set (≈2–3 min live, or pre-seed with
`./run_demo.sh --prompt-variant`):
```bash
./.venv/bin/python scripts/run_experiment.py --prompt-label candidate
```

**Datasets → Runs** → select the `production` (`claude-sonnet-4-6`) and
`candidate` (`claude-sonnet-4-6-candidate`) runs → **Compare**. This is the honest
money moment — **a real trade-off, not a clean win:**
- **Up:** the candidate lifted **groundedness 0.93 → 0.96, helpfulness 0.90 →
  0.92, relevance 0.93 → 0.93**, and **held every code metric at 1.00**.
- **Down:** the stricter format **cost some warmth** — the categorical `tone`
  judge went from *good ×8 / excellent ×2* to *good ×9 / **poor ×1***.

**Say it:** "This is the loop earning its keep. We aimed at grounding and got it —
without breaking the deterministic checks — but the eval set *caught a
side-effect*: the rigid format reads a little colder. That's a decision to make
with data, not a thing we'd have noticed by eyeballing one answer." *(`tone` is
categorical, so it shows as a label distribution here, not a mean.)*

**Deploy — or iterate.** The point is you now decide with evidence:
- **Ship it** if grounding wins for a factual concierge: in the **Prompts** tab,
  set the `production` label on the candidate version (promote it). "That's the
  deploy — the app fetches `production`, so it serves the new prompt with **no
  redeploy**. In a real pipeline this promotion is gated by CI: the [GitHub
  integration](https://langfuse.com/docs/prompt-management/features/github-integration)
  runs this exact eval as a check and only ships on pass — see [`cicd/`](cicd/)."
- **Or iterate** to a `candidate-v2` that keeps the grounding discipline but
  restores warmth, and re-run the experiment. "The first fix is rarely the last —
  that's why it's a loop."

Either way, the next portal question produces new traces under the shipped
version → **back to Act 2**. The loop is closed.

> **Presenter note:** after promoting, the app may serve the previous version for
> up to ~60s (the SDK's prompt cache TTL) — ask the follow-up question a moment
> later so the new version is the one that runs.

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
- **"How do prompt changes ship — is this real CI/CD?"** Yes. The app reads the
  prompt labelled `production` at runtime, so promoting a version *is* the deploy.
  Langfuse's GitHub integration (`repository_dispatch` / webhook sync) turns that
  promotion into a gated pipeline — run the eval set as a check, ship only on
  pass + `production` label. Reference workflow in [`cicd/`](cicd/).
- **"Where does data live?"** Langfuse stores traces in **ClickHouse** — that's
  what makes search, score analytics and dashboards fast at scale.
- **Alternative chat surface:** the stack also ships **LibreChat**
  (`http://localhost:3080`); the instrumentation story is identical.

---

## Reset / re-run

```bash
./run_demo.sh                  # regenerate everything (idempotent)
./run_demo.sh --quick          # traffic + evaluators + annotation, skip experiments
./run_demo.sh --no-gpt         # skip the GPT comparison run
./run_demo.sh --prompt-variant # ALSO pre-run the candidate prompt (for Act 6 compare)
./.venv/bin/python scripts/run_experiment.py --model gpt-4o --run-name gpt-rerun
./.venv/bin/python scripts/run_experiment.py --prompt-label candidate  # candidate prompt run
```

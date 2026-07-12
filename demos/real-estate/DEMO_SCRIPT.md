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

> The agent, tools, evaluators and portal all live in `demos/real-estate/`. Every
> surface (portal, live-traffic, experiment) drives the **same** instrumented,
> provider-agnostic agent, running the **same Langfuse-managed prompt** — so what
> you show is what runs in production. See [`AI_ENGINEERING_LOOP.md`](AI_ENGINEERING_LOOP.md)
> for the full step→artifact map.

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

Don't rush the **Ask** — the answers tell you which acts to go deep on and which
to skim, and they surface the specifics you'll need if this turns into a
proof-of-concept. Skip acts freely; the arc holds even if you only run three.
Pair with the `run-demo` skill (open Claude Code in the repo) to pre-flight the
stack and get fed the next act live.

---

## 0 · Pre-flight (do this BEFORE the meeting)

Requires the Langfuse stack up on `:3001`, and in the `real-estate` project
**Settings → LLM Connections**: an **Anthropic** connection (powers managed
judges) and an **OpenAI** connection. The raw OpenAI key must also be in
`demos/real-estate/.env` as `OPEN_AI_API_KEY` (so the agent can run on GPT).

```bash
cd demos/real-estate
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

> **Demo hygiene:** if your shell exports `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`,
> they override `.env` and traces 401 silently — `unset` them first.

---

## What each act proves

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

## Opening · Locate the pain (2 min, no screen yet)

**Frame.** Traditional monitoring tells you whether your app *responded* — a
200 OK — not whether the answer was any *good*. An LLM that confidently invents a
listing that doesn't exist looks identical, to your infra, to one that nailed it.
That gap is the whole reason this category exists.

**Ask (and actually wait — these steer the session):**
- "Do you have LLM features in production today, or on the way?"
- "When one gives a bad answer right now, how do you find out — who notices, and
  how long does it take?"
- "How do you measure whether an answer was *good* today — anything automated, or
  is it someone eyeballing screenshots?"
- "Is cost-per-feature something you can see, or is it one big provider bill?"

**Land.** Map what they say to an act: *no visibility* → lean on Acts 2–3;
*can't prove quality / picking a model* → Acts 3–5; *changes are scary to ship*
→ Act 6. Tell them you'll show the whole loop on a working app, then let their
answers decide where you slow down.

---

## Act 1 · The app (2 min) — "here's what our customer ships"

**Frame.** Start where they live: a real product. This is the kind of assistant a
marketplace puts in front of buyers — the thing whose bad answers reach a customer.

**Show.** Open the **portal** (`http://localhost:8080`). Click an example chip, e.g.:

> *"2-bed flat to buy in Madrid under €400k near a metro, and the mortgage"*

While it responds, point out the property **cards**, the **🔧 tool pills** (it
searched listings, pulled neighborhood insights, estimated a mortgage), the
**👍/👎 Helpful?** buttons, and **🔍 View trace in Langfuse**. Then ask a
**follow-up in the same chat** — *"what would the mortgage be on that one?"* — to
show it carries context, and click **View trace** (opens the SAME trace).

**Land.** "Natural language, English or Spanish, calling real tools — and every
single one of those turns is already captured, with a one-click path from the
answer a user saw to the full reasoning behind it. That 👍/👎 is a real user
signal that lands next to our automated scores. Nothing here is a mock."

**Ask.** "Where would an assistant like this live for you — support, search,
internal tooling? And when it says something wrong to a customer, who owns that,
and how do they even hear about it today?"

---

## Act 2 · The trace (4 min) — "full visibility into one conversation"

**Frame.** The number-one operational question with LLM apps: *when it's wrong,
was it the retrieval, the tool, the prompt, or the model?* Without step-level
visibility you're guessing. Here's what "you're not guessing" looks like.

**Show.** The chat you just ran is **one trace**, each exchange a `turn-N`
observation. Walk it top → bottom:
- root trace `conversation` — **input** = first question, **output** = latest
  answer, metadata `agent_model`; grouped under a **session**.
- `turn-1`, `turn-2`, … — one per user message; the follow-up resolved "that one"
  because the agent gets the conversation so far.
- inside a turn: `plan` (extracts constraints) → `agent-turn-N` (tool decisions) →
  `tool:*` (**click one** — exact input/output, "no black box") → final synthesis.
- Click a **generation** → **token usage** and **€ cost** per step, model, latency,
  and the **Prompt** it used (`property-concierge-agent` v1), version-linked to the
  generation.

Then: **Sessions** → `sess-madrid-buyer-001`; **Tracing** list → filter by tag
`real-estate`, note user ids + metadata.

**Land.** "Root-cause goes from 'read the logs and guess' to 'open the trace and
see the step that broke' — with the exact tool input, the cost of every call, and
the prompt version that produced it. Multi-turn folds into one trace and
conversations into a session, so you're debugging an *interaction*, not a
disconnected call."

**Ask.** "When an LLM answer is bad today, what can you actually see — the final
output only, or the steps? Roughly how long does root-cause take, and who gets
pulled in?"

---

## Act 3 · Scores — three layers (5 min) — "did it do a *good* job?"

**Frame.** Tracing tells you *what happened*; it doesn't tell you if it was any
good. Quality is not one thing — some checks are mechanical (was every listing
real?), some are subjective (was it helpful?). You need both, and you can't afford
an LLM call on 100% of traffic just to check formatting. So Langfuse layers them.

**Show.** On a trace, open the **Scores** panel — three layers:

1. **Code evaluators** (deterministic, on the *synthesis observation*):
   `used-search-tool`, `grounded-listings`, `budget-adherence`, `location-match`,
   `language-match`. Cheap, exact checks on every trace — did it search? is every
   listing real *and* retrieved? within budget? right city? right language?
2. **Managed LLM-as-a-Judge** (Langfuse-native, **automatic**): `Helpfulness`,
   `Relevance`. Show the **Evaluators** page — "configured once, pointed at the
   Anthropic connection, now scores every `real-estate` trace. No code in our app."
3. **Custom SDK judges**: `groundedness`, `tone` — pushed from our own code where
   we want a bespoke judge. Managed + custom coexist.

Plus the human signal: if you clicked **👍/👎** in Act 1, a `user-feedback` score
is on this trace too.

**Money moment — evals catch problems.** Filter Traces by tag **`fault-demo`**:
- `hallucinate` → `grounded-listings = false` (recommended a non-existent id).
- `over_budget` → `budget-adherence < 1` (pushed an over-budget option).
- `wrong_language` → `language-match = false` (Spanish question, English answer).

**Land.** "Deterministic checks run free on 100% of traffic; LLM judges cover the
subjective stuff; real user feedback sits right beside both. When the agent
misbehaves, the score goes red *automatically* — so you can alert on it, chart a
satisfaction rate, and route the bad ones to a human. You're not sampling
screenshots; you're measuring every conversation."

**Ask.** "What does 'good' actually mean for your use case — accuracy, tone,
format, safety? Who decides that, and is it written down anywhere yet? And is
anyone reviewing outputs today — what's that costing you in time?"

---

## Act 4 · Human annotation (2 min) — "put a human in the loop"

**Frame.** Automated scores get you ~90% of the way. The last mile — the judgment
that becomes your gold standard — needs a domain expert. The trap is building a
labeling tool to capture it. You don't have to.

**Show.** **Annotation Queues** → **Property Concierge - human review**. Open an
item. Point at the **score schema**: a categorical **reviewer-verdict**
(approve / minor-issues / reject) and a 1–5 **expert-usefulness** rating. Annotate
one live — the score lands on the trace next to the automated ones.

**Land.** "Your experts review real production traces in a structured queue, and
their labels land as scores on the very same trace. That human-labeled set is what
you calibrate your LLM judges *against* — so 'is the judge trustworthy?' becomes a
measurable question, not a leap of faith."

**Ask.** "Who are your domain experts, and how would you get their judgment into
the loop today — spreadsheets, Slack threads, nothing? What would it take to make
that repeatable?"

---

## Act 5 · Dataset + model comparison (5 min) — "prove it, and pick a model"

**Frame.** Everything so far scored *production* traffic. But the questions that
stall teams are decisions: *should we switch models? did this change make things
better or worse?* You answer those on a fixed test set, not on live traffic.

**Show.** **Datasets → `property-concierge-eval`** (10 items): show an `input`
question and `expected_output` (criteria + ground-truth constraints) — buy/rent,
EN/ES, several cities, plus one deliberately impossible request.

**Runs** tab → two runs: `claude-sonnet-4-6` and `gpt-4o`. "Same agent, same
evaluators, same tools and prompts — only the model changed." Point at per-run
**aggregate scores**, then select **both runs → Compare**: a like-for-like,
metric-by-metric comparison — grounding, budget adherence, helpfulness, relevance.
Click a run item → its trace → per-item scores.

Add another candidate any time:
```bash
./.venv/bin/python scripts/run_experiment.py --model claude-sonnet-4-6 --run-name candidate-v2
```

**Land.** "This is how you choose a model — or catch a regression *before* it
ships — with a number, not a hunch. Because cost is captured per run too, 'is the
cheaper model good enough for this feature?' becomes a chart you can defend to
whoever signs off."

**Ask.** "How do you decide which model to use, or whether to upgrade? Have you
ever been blocked from switching because you couldn't *prove* the new one was as
good — or as safe?"

---

## Act 6 · Close the loop — deploy a better prompt (4 min) — "and then ship it"

**Frame.** This is what makes it a *loop*, not a dashboard. For most teams a
prompt change means a code change, a review, and a deploy — so prompts get
"fixed" by whoever's brave enough. Here the prompt is data in Langfuse, fetched by
**label** (`property-concierge-agent` @ `production`) at runtime.

**Show.** **Prompts tab** → `property-concierge-agent` → two versions:
`production` (baseline) and `candidate` (tighter grounding + budget discipline +
strict language + scannable format), with the diff right there.

Say the loop out loud: "Back in Act 5 the code checks were perfect but the *judge*
metrics sat in the low 0.9s — **that gap is our headroom.** So we hypothesize a
tighter prompt, write it as a `candidate`, and prove it on the *same* 10 questions
and evaluators. Decide with data, not vibes."

Run the candidate (≈2–3 min live, or pre-seed with `./run_demo.sh --prompt-variant`):
```bash
./.venv/bin/python scripts/run_experiment.py --prompt-label candidate
```

**Datasets → Runs** → compare `production` (`claude-sonnet-4-6`) vs `candidate`
(`claude-sonnet-4-6-candidate`). The **honest money moment — a real trade-off, not
a clean win:**
- **Up:** groundedness **0.93 → 0.96**, helpfulness **0.90 → 0.92**, relevance
  **0.93 → 0.93**, and **every code metric held at 1.00**.
- **Down:** the stricter format **cost some warmth** — the categorical `tone` judge
  went from *good ×8 / excellent ×2* to *good ×9 / **poor ×1***.

*(`tone` is categorical, so it shows as a label distribution here, not a mean.)*

**Land.** "This is the loop earning its keep. We aimed at grounding and got it —
without breaking the deterministic checks — but the eval set *caught a
side-effect*: the rigid format reads a little colder. That's a call you make with
evidence, not something you'd notice by eyeballing one answer."

**Deploy — or iterate.** In the **Prompts** tab, set the `production` label on the
candidate to promote it. "The app fetches `production`, so it serves the new prompt
with **no redeploy**. In a real pipeline that promotion is gated by CI — the
[GitHub integration](https://langfuse.com/docs/prompt-management/features/github-integration)
runs this exact eval and only ships on pass; reference in [`cicd/`](cicd/)." Or
iterate to a `candidate-v2` that keeps the grounding but restores warmth.

Either way, the next portal question produces new traces under the shipped version
→ **back to Act 2**. The loop is closed.

**Ask.** "How does a prompt change reach production for you today — code deploy?
Who's allowed to make one, and how nervous is that change when it goes out?"

> **Presenter note:** after promoting, the app may serve the previous version for
> up to ~60s (the SDK's prompt-cache TTL) — ask the follow-up a moment later so the
> new version is the one that runs.

---

## Optional close · Dashboards (1 min)

**Show.** Langfuse **Dashboards** → cost, tokens, latency, and score trends over
time. **Land.** "Once traffic flows, this is your production control room — and
because it's all in ClickHouse underneath, you can build any view you want on the
same data." **Ask.** "Who'd want this view — the eng team, or someone in the
business watching cost and quality?"

---

## Under the hood — how it's instrumented (for the "show me the code" moment)

When a technical audience asks *"okay, but how much of my code does this take?"*,
open these files. **All runtime tracing is the Langfuse SDK, concentrated in
`demos/real-estate/agent/`** — `webapp/server.py` is only the caller that feeds in
session context. There's no framework lock-in and no magic. Show the ones that map
to what they cared about in the **Ask** beats.

**1 · One client, pinned to the right project — `agent/config.py`**
```python
# agent/config.py:54  get_langfuse() — a singleton bound to EXPLICIT keys
_langfuse = Langfuse(public_key=..., secret_key=..., host=LANGFUSE_HOST)   # :60
```
`config.py:33-36` hard-overrides the `LANGFUSE_*` env vars and `verify_project()`
(`config.py:119`) fails fast if the keys resolve to the wrong project. *Why it
matters:* a stray shell-exported key can't silently ship a customer's traces to the
wrong place — a real operational footgun, handled in ~3 lines.

**2 · The whole trace tree is one function — `agent/concierge.py` (`run_turn`)**
```python
# every step is one context-manager call; the nesting IS the trace tree
with lf.start_as_current_observation(as_type="generation", name="plan", ...):   # :177
    ...
for i in range(MAX_ITERS):
    with lf.start_as_current_observation(as_type="generation", name=f"agent-turn-{i+1}"):  # :211
        ...
    for call in res["tool_calls"]:
        with lf.start_as_current_observation(as_type="span", name=f"tool:{call['name']}"):  # :234
            ...
```
*Why it matters:* the trace you walked in Act 2 is just these nested `with`
blocks — `as_type` picks generation vs span, `name` is the label. That's the entire
instrumentation surface for a whole agent.

**3 · Session wrapping (Act 2 / the Sessions story) — two lines, two files**
```python
# webapp/server.py:95   one conversation = one trace (deterministic id from the session)
conv_trace_id = get_langfuse().create_trace_id(seed=sid)
# agent/concierge.py:141 each turn attaches to that shared trace
root_cm = lf.start_as_current_observation(..., trace_context={"trace_id": conversation_trace_id})
# agent/concierge.py:148 THIS is what groups traces into a Langfuse Session
ctx = propagate_attributes(session_id=session_id, user_id=user_id, tags=..., trace_name=...)
```
*Why it matters:* `propagate_attributes(session_id=...)` is the one call that powers
the entire Sessions view — grouping is a property you *set*, not a pipeline you build.

**4 · Token usage + € cost (Act 2 cost story) — `agent/llm.py` → folded into the generation**
```python
# agent/llm.py:59  provider-agnostic call returns usage + cost_details (:41 publishes GPT prices)
res = call_llm(model, system, messages, tools=tools, max_tokens=1500)
# agent/concierge.py:217  attach them to the generation
gen.update(output=..., usage_details=res["usage"], cost_details=res["cost_details"])
```
*Why it matters:* cost per step is captured at source. Claude is auto-priced by
Langfuse; GPT gets explicit prices — which is exactly why the Act 5 cost comparison
works across providers.

**5 · Prompt management = the Deploy node (Act 6) — `agent/prompts.py`**
```python
# agent/prompts.py:100  fetch the system prompt BY LABEL, with a hard-coded fallback
return get_langfuse().get_prompt(AGENT_PROMPT_NAME, label=label, fallback=AGENT_FALLBACK)
# agent/prompts.py:112  link the fetched version to the generation (skips when fallback)
def link_kwargs(prompt): return {} if getattr(prompt, "is_fallback", False) else {"prompt": prompt}
```
Used at `concierge.py:178,199` as `**link_kwargs(...)`. *Why it matters:* the prompt
is data in Langfuse, not a string in the app — so promoting a label *is* the deploy
(Act 6), and the fallback means the app still runs on a fresh clone with nothing seeded.

**6 · Scores on an observation (Act 3) — `agent/scoring.py` → attached in `concierge.py`**
```python
# agent/concierge.py:300  attach each deterministic code score to the SYNTHESIS observation
for s in run_code_evaluators(result):          # agent/scoring.py:150
    lf.create_score(trace_id=trace_id, observation_id=final_gen_id,
                    name=s.name, value=s.value, data_type=s.data_type, comment=s.comment)
```
*Why it matters:* the code evaluators are plain Python functions (`scoring.py`) — no
LLM, deterministic, free — and a score is just `create_score(...)` pointed at an
observation. Managed + custom LLM judges are configured in the Langfuse UI, not here.

> One-liner for the room: *"The whole agent's tracing is nested `with`-blocks in one
> file, cost and prompt-version ride along on each step, and a score is a single
> call. That's the integration cost."*

---

## Talking points & objections

- **"Tied to a framework?"** No — plain Python + the provider SDKs, instrumented
  with the Langfuse SDK (`start_as_current_observation`). Native integrations and
  OpenTelemetry also exist.
- **"Provider-agnostic?"** Yes — the same agent runs on Claude or GPT
  (`agent/llm.py`); that's what powers the comparison.
- **"Do evals need an LLM?"** No — code evaluators are deterministic and free. LLM
  judges (managed or custom) are for subjective quality.
- **"Can judges run automatically?"** Yes — the managed evaluators in Act 3 run on
  live traffic with no app code, via the LLM connection.
- **"Is LLM-as-a-Judge reliable enough to act on?"** Pair it with deterministic
  code evaluators (that's why the demo ships both) and calibrate judges against the
  human-annotated set from Act 4.
- **"How do prompt changes ship — is this real CI/CD?"** Yes. The app reads the
  `production`-labelled prompt at runtime, so promoting a version *is* the deploy;
  Langfuse's GitHub integration turns it into a gated pipeline. See [`cicd/`](cicd/).
- **"Where does data live?"** Langfuse stores traces in **ClickHouse** — that's
  what makes search, score analytics and dashboards fast at scale, and keeps the
  data in a store you can query with SQL rather than a vendor silo.
- **"What about our existing APM (Datadog etc.)?"** Keep it — APM says the request
  succeeded; this says whether the *answer* was good, what it cost per token, and
  lets you replay and experiment on real prompts.
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

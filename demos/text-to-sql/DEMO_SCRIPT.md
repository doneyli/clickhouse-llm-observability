# Text-to-SQL — Demo Script (prompt chaining with gate checks on a data assistant)

A ready-to-run demo of a **natural-language data assistant** over ClickHouse's
public datasets (via MCP), built on LangChain and fully traced to **Langfuse**.
This is the repo's **Pattern 1 (prompt chaining) headline**: a fixed
`analyze → retrieve → respond` chain with two **gate checks** wired *inside* it —
each with bounded retry and a distinct fail routing (abort vs escalate). Its
signature beats: **gates that enforce policy between chain steps** (not just
observe it after), the same **deterministic SQL-safety guardrail** still scoring
every response at ingest, and **prompt management** — every prompt (including the
gate's own rubric) lives in Langfuse and ships by label, no redeploy.

- **App:** a gated LangChain chain (`analyze → [gate 1] → retrieve context →
  respond → [gate 2]`), CLI batch + interactive modes (container in the root
  `docker-compose.yaml`)
- **Gates:** Gate 1 (deterministic, catalog membership) after analysis; Gate 2
  (hybrid: deterministic SQL policy + Haiku grounding grader) after the response.
  Bounded retry (`GATE_MAX_ATTEMPTS=2`); Gate 1 exhausted → **abort**, Gate 2
  exhausted → **escalate** (flagged answer + `gate:escalated` tag).
- **Data context:** the ClickHouse public playground (`sql.clickhouse.com`) —
  a 24-dataset catalog the analysis stage reasons over, plus live context
  fetched through the **`mcp-clickhouse`** server
- **Observability backend:** Langfuse (`http://localhost:3001`), trace name `text-to-sql`
- **Model:** `claude-sonnet-4-6` (both chain steps); `claude-haiku-4-5` (gate grader)
- **Run length:** 14–18 min full; ~6 min short path (Acts 1–2)

> The pipeline, config, and instrumentation live in `demos/text-to-sql/`; the
> guardrail is `evaluators/sql-safety-guard.ts`, seeded into Langfuse by
> `scripts/seed-code-evaluators.sh`. For the loop framing shared by all the
> demos, see [`../../AI_ENGINEERING_LOOP.md`](../../AI_ENGINEERING_LOOP.md).

> **Honesty note (know this before you present):** the pipeline *reasons over*
> the dataset catalog and often drafts SQL in its answers, but it does **not
> execute** queries against ClickHouse — the MCP step retrieves the database
> catalog as context. There is also **no HTTP endpoint**; port 8002 is mapped but
> nothing listens. Demo it as what it is: a traced NL-analysis assistant with a
> SQL-policy guardrail. If asked "does it run the SQL?" — "not in this demo; the
> gate is exactly the layer you'd want *before* you let it — **and as of this
> version, the gate is actually in the pipeline, not just observing it.**"

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

Don't rush the **Ask** — the answers tell you which acts to go deep on. The short
path is Acts 1–2 (trace + the gate money moment); add Acts 3–5 when there's appetite.

---

## 0 · Pre-flight (do this BEFORE the meeting)

```bash
# CRITICAL: clear leaked Langfuse keys — exported shell vars override .env and 401 silently.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Stack up (Langfuse + mcp-clickhouse), .env needs ANTHROPIC_API_KEY + Langfuse keys
docker compose --profile langfuse up -d
docker compose --profile demo build text-to-sql

# Seed the managed prompts (NOT covered by setup.sh — the Deploy act needs this).
# Now also seeds the gate's rubric (text-to-sql-gate-grounding, Haiku/temp-0) and
# a `candidate` label on text-to-sql-analysis for the experiment. Idempotent.
python scripts/seed-app-prompts.py

# Seed the code evaluators (setup.sh does this; run it if scores are missing).
# Now also provisions `chain-gate-check` → the gate-pass boolean on every gate span.
./scripts/seed-code-evaluators.sh

# Seed the per-step analysis dataset (for the Act 4/5 experiment)
python demos/text-to-sql/scripts/seed_step_dataset.py

# Generate fresh traces (10 questions, ~2 min; scores land ~30s after)
docker compose run --rm text-to-sql python main.py
```

**Create the gate-fail-rate Monitor once** (Monitors → New; boolean-average as a
pass-rate — fires when <90% of gate checks pass in a rolling hour):

```json
{
  "dataSource": "scores-boolean",
  "metric": {"measure": "value", "aggregation": "avg"},
  "filters": [{"column": "name", "operator": "=", "value": "gate-pass"}],
  "operator": "<", "alertThreshold": 0.9, "warningThreshold": 0.95, "window": "1h"
}
```

(For a per-gate view, add a Custom Dashboard widget: average `gate-pass` grouped
by `observationName` — `gate-database-selection` vs `gate-response-quality`.
Monitors have no stable public API in the pinned Langfuse version, so this is a
one-time UI step.)

**Browser tabs ready:** Langfuse Traces filtered to name `text-to-sql`
(`:3001`, `demo@example.com` / `demodemo1!`), the **Prompts** tab, the **Monitors**
/ dashboard tab, and a terminal for the interactive gate + guardrail moments.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| **Tracing a multi-stage LangChain pipeline** (2 generations + a manual span) | Act 1 — `query_analysis` → `retrieve-context` → `response_generation` |
| **Token usage + cost per stage** | Act 1 — click either generation |
| **MCP tool step traced next to LLM steps** | Act 1 — the `retrieve-context` span |
| **Gate checks between chain steps** (pass/fail verdict as span output) | Act 2 — `gate-database-selection`, `gate-response-quality` |
| **Bounded retry with stable span names + attempt metadata** | Act 2 — retried step appears twice, `metadata.attempt` 1→2 |
| **Two fail routings: abort (Gate 1) vs escalate (Gate 2)** | Act 2 — `gate:aborted` / `gate:escalated` trace tags |
| **Gate-fail rate monitored via a boolean score** | Act 2 — `gate-pass` score + the Monitor/dashboard widget |
| **Deterministic guardrail scores on 100% of traffic** | Act 3 — `sql-risk`, `sql-read-only`, `credential-leak` |
| **Evals catch a policy violation live** | Act 3 — ask for a DELETE, watch `sql-risk = destructive` |
| **Prompt management** (versioned, fetched by label, linked to generations) | Act 4 — `text-to-sql-analysis` / `-response` / `-gate-grounding` |
| **Ship a prompt change with no redeploy** | Act 4 — edit → re-run → new version on the trace |
| **Experiment where the gate is the metric** | Act 4/5 — analysis prompt `production` vs `candidate`, gates fixed |
| **LLM-as-a-Judge at stack level** (test scenarios) | Act 5 — 40 tagged scenarios scored by managed judges |

---

## Opening · Locate the pain (2 min, no screen yet)

**Frame.** Every data team is being asked for the same feature right now: *"let
people ask questions in English."* The two things that keep it from shipping are
trust problems, not model problems. One: **what SQL is the model writing against
your warehouse** — is anything checking it's read-only, bounded, sane? Two: the
behavior lives in prompts, and **every prompt tweak is a code deploy**, so the
thing you tune most ships the slowest.

**Ask (these steer the session):**
- "Is anyone here building — or being asked for — a natural-language interface
  over your data? What's blocking it from production?"
- "If an LLM writes SQL in your environment today, what stands between it and a
  `DROP TABLE`?"
- "When you tweak a prompt, what does shipping that look like — PR, review,
  deploy? How long?"

**Land.** "Small demo, two sharp answers: a **policy check that scores every
response the moment it's ingested** — for free, no LLM judge needed — and
**prompts that ship by label** instead of by deploy. Both on a pipeline you can
read in one file."

---

## Act 1 · One question, whole pipeline (4 min)

**Frame.** An NL-over-data answer is never one LLM call — it's *understand the
question → figure out what data applies → compose the answer*. When it's wrong,
you need to know which stage lied.

**Show.** Run the batch (or one question in `--interactive`):

```bash
docker compose run --rm text-to-sql python main.py
```

Questions like *"What are the most expensive areas for property in London?"*
stream by. In Langfuse → **Traces**, open the newest `text-to-sql` trace
(tags `text-to-sql`, `demo`) and walk it top → bottom:

- **Generation 1** — metadata `purpose: query_analysis`: the model decides which
  of the 24 public datasets answer the question (`uk`, `nyc_taxi`,
  `stackoverflow`, …).
- **`gate-database-selection`** — a span sitting *between* the generations, with
  `output.verdict = "pass"` (deterministic catalog check). Point at it: "the
  chain now checks itself between steps; the verdict is right there in the span
  output" (that's Act 2 — flag it now, cash it in there).
- **`retrieve-context`** — a plain span around the **MCP call** to
  `mcp-clickhouse`: the live database catalog fetched as context. Non-LLM steps
  sit in the same tree as LLM steps.
- **Generation 2** — `purpose: response_generation`: the final answer, composed
  from question + analysis + context.
- **`gate-response-quality`** — the second gate span, `output.verdict = "pass"`,
  with a nested **Haiku generation** (the grounding grader, prompt
  `text-to-sql-gate-grounding` linked).
- Click either generation → **token usage, cost, latency, model** — and the
  **Prompt** panel showing which prompt version produced it (that's Act 4's
  setup — point at it now, cash it in later).

**Land.** "Three steps, one trace, each with its own cost and its own
input/output. When an answer is off, you can see *which stage* drifted — the
dataset choice or the composition — instead of rereading one blob of logs. And
the MCP step proves this isn't LLM-only tracing: tool calls land in the same
tree."

**Ask.** "How many stages would your version of this have — schema lookup,
generation, execution, formatting? Can you see them separately today?"

> **Fallback:** if `retrieve-context` shows `[MCP unavailable: …]`, the pipeline
> still answered — graceful degradation is itself worth ten seconds of stage
> time. Check `mcp-clickhouse` is up afterwards.

---

## Act 2 · The gate: from observing to enforcing (~4 min) — the new money moment

**Frame.** "Last time, the SQL guard scored violations *after* the answer already
shipped. Policy that only observes isn't policy. Now the check runs *inside* the
chain — bad output never reaches the next step, and every gate verdict is still
scored at ingest so you can Monitor the fail rate."

**Show (gate pass).** Run one normal question (batch from Act 1 is fine). Open the
trace and point at **`gate-database-selection`** → `output.verdict = "pass"`, and
~30s later the **`gate-pass = true`** score landing on that span (Scores tab).

**Show (force a fail + retry).** Inject a fault so the analysis names no database:

```bash
docker compose run --rm -e DEMO_FAULT=vague-analysis text-to-sql python main.py --interactive
# then ask any question, e.g.:  What are property prices in London?
```

In the trace (tags now include `fault:vague-analysis`):
- analysis **attempt 1** → `gate-database-selection` verdict **`fail`** (reason
  quoted: "…names no database from the catalog…");
- analysis **attempt 2** — *same span name*, `metadata.attempt = 2`, and the gate
  failure reason in both `metadata.gate_failure_reason` **and** the retried
  prompt input;
- gate verdict again. **If it fails twice: the trace is tagged `gate:aborted`,
  and there is no `retrieve-context` and no response generation at all** — "we
  didn't pay for the rest of a doomed chain." The terminal narrates the same
  story: `[gate-database-selection] fail (attempt 1) — …`.

**Show (score + Monitor).** Scores tab → `gate-pass` on *every* gate span,
including the failed attempt. Open the Monitor / dashboard widget → gate-fail rate
by gate name. Filter Traces by `gate-pass = false` — your standing "gates that
fired" view.

**Land.** "Verdict in the span, boolean at ingest, alert on the rate. Retry is
bounded and every attempt is auditable — same span name, attempt in metadata.
The catch-early routing is deliberate: Gate 1 *aborts* (don't spend the MCP call
+ response tokens on a broken analysis); Gate 2 *escalates* (an answer exists but
is unverified — flag it, don't hide it)."

**Ask.** "Between which two steps of *your* pipeline would a gate have caught your
last incident?"

---

## Act 3 · The SQL safety net (4 min)

**Frame.** You cannot put an LLM near a warehouse on vibes. But you also can't
afford an LLM judge on 100% of traffic just to check a policy that's mechanical:
*read-only, bounded, no secrets*. Mechanical policies deserve mechanical
enforcement — deterministic code, every trace, zero marginal cost. **And the same
destructive-SQL policy now *also* runs in-pipeline as Gate 2's fail-closed
branch** — the evaluator observes at ingest; the gate enforces before return.

**Show.** On the trace from Act 1, open **Scores**. Every generation carries:

- `sql-present` / `sql-read-only` / **`sql-risk`** (categorical:
  `safe` / `missing-limit` / `destructive` / `no-sql`) — from the
  `sql-safety-guard` code evaluator
- `credential-leak` / `leak-type` — from `credential-leak-guard`, scanning for
  key-shaped strings (`sk-…`, `AKIA…`, connection strings) on **every** app in
  the stack
- `output-present` / `structure-clean` / `response-length` — structural checks
  (truncation, leaked `{placeholders}`, broken code fences)

Now trip the guardrail live:

```bash
docker compose run --rm text-to-sql python main.py --interactive
# then ask:  Write a query to delete all old taxi trips
```

~30 seconds later, refresh the trace: **`sql-risk = destructive`**,
`sql-read-only = false`, with a comment quoting the offending statement. Filter
the Traces list by that score — that's your standing "SQL policy violations"
view, ready to alert on.

Now show the *gate* side of the same policy — the pipeline refusing to ship it:

```bash
docker compose run --rm -e DEMO_FAULT=destructive-sql text-to-sql python main.py --interactive
# then ask:  How do I clean up old taxi trips?
```

Gate 2 fails **closed** on the deterministic SQL-policy branch, retries once,
then **escalates**: the returned answer is prefixed `[Unverified — routed for
review: …]`, the trace gets a **`gate-escalation`** span and a **`gate:escalated`**
tag. "The evaluator *told you* it was destructive; the gate *stopped you from
shipping it unflagged*."

**Land.** "That check is a small TypeScript function running inside Langfuse at
ingest — deterministic, free, on 100% of traffic, typically scored within 30
seconds. Nobody eyeballs screenshots; the policy is *enforced as data* you can
filter, chart, and page on. And because it's code, your security team can read
exactly what it checks."

**Ask.** "What's your SQL policy in one sentence — read-only? row limits?
schema allowlist? Who owns it, and where is it written down today?"

---

## Act 4 · Ship a prompt without a deploy — and let the gate be the metric (4 min)

**Frame.** Both steps of this chain are driven by prompts — and prompts are
the highest-churn artifact in any LLM app. If changing one means a code deploy,
iteration speed is capped by your release train.

**Show (prompt ships by label).** **Prompts** tab → `text-to-sql-analysis`,
`text-to-sql-response`, and — new this version — **`text-to-sql-gate-grounding`**
(the gate's own rubric, Haiku/temp-0): "even the gate's rubric ships by label."
Each carries a `production` label; the app fetches them **by label at startup**,
with a hard-coded fallback so a fresh clone still runs.

Edit `text-to-sql-response` in the UI — something visible, e.g. *"End every
answer with one suggested follow-up question."* — save as a new version, move
the `production` label to it. Re-run:

```bash
docker compose run --rm text-to-sql python main.py
```

The behavior changes, and on the new trace the generation links to **v2** of the
prompt — quality, cost, and version travel together.

**Show (the gate is the experiment's metric).** Run the experiment: two variants
of the *analysis* prompt over the per-step dataset, with the response prompt and
both gates byte-identical across runs:

```bash
python demos/text-to-sql/scripts/run_experiment.py   # production vs candidate
```

In Langfuse → **Datasets → `text-to-sql-analysis-step` → Runs**, compare the two
runs on **`gate_pass_analysis`** — the same catalog check Gate 1 enforces
in-pipeline, now the experiment's success metric.

**Land.** "The prompt is data, not code — promote a label and the next run serves
it, no redeploy, version-stamped on every generation. And the gate is the
*contract*: prompts iterate against the exact check production enforces. Gate the
promotion behind that experiment in CI and you've got a release process for
prompts; the reference pipeline lives in this repo's real-estate demo
(`demos/real-estate/cicd/`)."

**Ask.** "Who should be *allowed* to change a prompt in your org — only
engineers? Would a PM ship prompt changes if it didn't need a deploy?"

---

## Act 5 · Optional — judges at stack level (3 min)

**Frame.** Regex catches policy violations; it can't tell you an answer was
*irrelevant* or *hallucinated*. That's the LLM-as-a-Judge layer — shown here on
the stack's evaluation harness rather than live traffic.

**Show.** Run the 40 synthetic test scenarios and let the managed judges score
them:

```bash
docker compose --profile tools run --rm test-scenarios
```

In Langfuse, filter Traces by tag `test-scenario`: scenarios tagged
`relevance-test` / `coherence-test` / `hallucination-test` / `control` get scored by the managed
**Relevance / Correctness / Hallucination** judges (provisioned once — in this
self-hosted stack by `scripts/seed-llm-judge-evaluators.sh`, or in the
Evaluators UI on Langfuse Cloud — no app code either way). The
deliberately-bad scenarios score low — the judges *catch* them.

**Land.** "Two layers, deliberately: deterministic code for the mechanical
policies at 100% coverage, LLM judges for the semantic questions on a sample.
The judges here are scoped to the test harness; pointing one at live
`text-to-sql` traffic is a two-minute change in the Evaluators UI."

**Ask.** "For your data assistant, what would 'wrong' mean — wrong table, stale
number, made-up column? Which of those are mechanical checks, and which need a
judge?"

---

## Close (1 min)

Four takeaways: **every step of the chain is visible** (LLM and tool steps alike,
with cost); **gates enforce policy between steps** — bad output never reaches the
next step, bounded retry, abort vs escalate, every verdict a boolean score you can
Monitor; **policy is also enforced as free deterministic scores** on all traffic;
**prompts ship by label** (including the gate's rubric), not by deploy. Then hand
them the asset: the repo is public — the chain + gates are `demos/text-to-sql/`
(a handful of small files; the gates are `gates.py`), the guardrail is ~100 lines
of TypeScript in `evaluators/`. "Clone it, swap the catalog for your schema, the
guardrail for your policy, and put the gate between the two steps that hurt you."

---

## Under the hood — how it's instrumented (for the "show me the code" moment)

Everything lives in `demos/text-to-sql/`; the guardrail in `evaluators/`. The
integration is deliberately the *low-touch* end of the spectrum — LangChain's
callback does the heavy lifting (contrast `demos/real-estate/`, which hand-builds
its trace tree).

**1 · Zero-config client, graceful when keys are absent — `langfuse_config.py`**
```python
# langfuse_config.py:16   tracing turns on only when both keys exist
LANGFUSE_ENABLED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
# langfuse_config.py:38   v3 client straight from env
from langfuse import get_client
client = get_client()
```
*Why it matters:* no keys → the app still runs, just untraced. Every wrapper in
this file degrades to a no-op on error — tracing can never take the app down.

**2 · One callback instruments both chains — `langfuse_config.py:163`**
```python
from langfuse.langchain import CallbackHandler
handler = CallbackHandler()          # passed as config={"callbacks": [handler]}
```
*Why it matters:* the two generations in Act 1 cost zero instrumentation code —
the LangChain integration emits them, with model, usage, and cost attached.

**3 · Trace name + tags by context propagation — `langfuse_config.py:120`**
```python
with propagate_attributes(trace_name="text-to-sql", tags=["text-to-sql", "demo"]):
    ...   # everything invoked inside lands on one named, tagged trace
```

**4 · A manual span for the non-LangChain step — `sql_pipeline.py:118`**
```python
with langfuse_span("retrieve-context"):          # langfuse_config.py:143
    self._context = mcp.get_context_for_question(question, analysis)
```
*Why it matters:* the MCP call isn't a LangChain runnable, so it gets a plain
SDK span — auto and manual instrumentation compose in one tree.

**5 · Prompt fetched by label, linked to the generation — `sql_pipeline.py:21`**
```python
lf_prompt = get_managed_prompt(name)              # get_prompt(name, label="production")  :62
tmpl = ChatPromptTemplate.from_template(lf_prompt.get_langchain_prompt())
tmpl.metadata = {"langfuse_prompt": lf_prompt}    # THIS line links version → generation
```
*Why it matters:* that one metadata assignment is the whole Act 4 story — the
callback sees it and stamps the prompt version on every generation.

**7 · The gates — `gates.py` + `sql_pipeline.py:query()`**
```python
# gates.py — pure functions, no Langfuse: Gate 1 deterministic, Gate 2 hybrid.
with langfuse_gate("gate-database-selection") as span:      # langfuse_config.py
    gate1 = gate_database_selection(analysis)               # stable span name
    span.update(output=gate1.as_output(),                   # {"verdict": ...} → gate-pass score
                metadata={"attempt": attempt, ...})         # dynamic values in metadata
if not gate1.passed:                                        # exhausted → route
    tag_current_trace(["gate:aborted"]); return "..."       # (Gate 2 → gate:escalated)
```
*Why it matters:* the gate *enforces* between steps — same span name across
retries, attempt in metadata, the reason fed back into the retried prompt; the
`chain-gate-check.ts` evaluator turns each `output.verdict` into a `gate-pass`
boolean at ingest. Fail-closed on SQL policy, fail-open only on grader parse error.

**6 · The guardrail itself — `evaluators/sql-safety-guard.ts`**
```ts
// :55  destructive statements → sql-risk = "destructive"
/\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|GRANT|REVOKE|...)\b/i
// :82  SELECT without LIMIT → "missing-limit"
```
*Why it matters:* the Act 3 SQL-safety moment is ~100 lines of reviewable
TypeScript running inside Langfuse at ingest — no service to run, no LLM to pay.
The same policy also runs in-pipeline as Gate 2's fail-closed branch (point 7).

> One-liner for the room: *"One callback handler, one manual span, one metadata
> line for prompt-linking, two gate spans that write a verdict — and the guardrail
> is a TypeScript function inside Langfuse. That's the whole integration."*

---

## Talking points & objections

- **"Does it actually execute the SQL?"** Not in this demo — it reasons over the
  live catalog (via MCP) and drafts SQL in its answers. That's deliberate for a
  public-playground demo; the guardrail is the layer you'd require *before*
  execution, and it's already scoring every response.
- **"Regex for SQL safety — really?"** For the mechanical policy, yes — it's
  deterministic, auditable, free, and runs on everything. It's a *layer*, not
  the whole answer: semantic quality is the judges' job (Act 5), and real
  execution would add a parser-based check. Defense in depth, cheapest layer
  first.
- **"Won't the gate retries blow up latency/cost?"** Retry is bounded
  (`GATE_MAX_ATTEMPTS=2` — one retry per gated step) and Gate 1 *aborts* before
  the MCP + response step on a doomed analysis, so the worst case is cheaper than
  today's un-gated chain that always runs all three steps. Gate 2's grounding
  grader is Haiku at temp 0; the deterministic checks are free.
- **"What if the grounding grader itself flakes?"** The deterministic SQL-policy
  half of Gate 2 fails **closed** (policy is policy); the LLM-graded half fails
  **open** on a parse error only (records `verdict_source: parse-error`) — gate
  infrastructure never takes the app down.
- **"Why is there no `user_id`/session on these traces?"** The batch demo
  doesn't set them; the SDK plumbing is present (`langfuse_config.py`). The
  real-estate and agentic-rag demos show sessions/users fully wired.
- **"Can the judges score live traffic, not just test scenarios?"** Yes — the
  managed judges are scoped by tag/trace-name filters; pointing one at
  `text-to-sql` is a UI change. They're scoped to the test harness here to keep
  the demo's costs deterministic.
- **"What's MCP buying us?"** A standard way to hand tools (here: the ClickHouse
  catalog) to any client — the same `mcp-clickhouse` server also powers the
  LibreChat agents in this stack. And the span proves tool calls trace like
  everything else.
- **"Where does the trace data live?"** Langfuse stores it in **ClickHouse** —
  which is why score filters and dashboards stay fast, and why your traces sit
  in an engine you can also query directly with SQL.

---

## Reset / re-run

```bash
docker compose run --rm text-to-sql python main.py                             # fresh traces (scores ~30s later)
docker compose run --rm text-to-sql python main.py --interactive               # interactive gate + guardrail moment
docker compose run --rm -e DEMO_FAULT=vague-analysis text-to-sql python main.py --interactive   # force Gate 1 fail → retry → abort
docker compose run --rm -e DEMO_FAULT=destructive-sql text-to-sql python main.py --interactive  # force Gate 2 fail → retry → escalate
python scripts/seed-app-prompts.py                                             # re-seed prompts incl. gate rubric + candidate (idempotent)
./scripts/seed-code-evaluators.sh                                              # re-seed evaluators incl. chain-gate-check
python demos/text-to-sql/scripts/seed_step_dataset.py                          # per-step analysis dataset (add --link-latest N to link recent traces)
python demos/text-to-sql/scripts/run_experiment.py                             # production vs candidate analysis prompt, gates fixed
./scripts/seed-demo-data.sh                                                    # full seed (text-to-sql + vector-rag + scenarios)
```

> **Tip (synthetic burst for the Monitor):** run a few `--fault vague-analysis`
> and `--fault destructive-sql` questions back-to-back to push the gate-fail rate
> below 90% and watch the Monitor fire.

# Grocery Assistant — Demo Script

The audience sees the same shopping assistant traced two ways: one run they can measure and
one they can't, with the same answers. Then they see which evaluator to build first: four
deterministic checks on the cart and the order history before any judge. It's for an
engineering team that has an AI assistant traced in Langfuse and is now asking "is this set
up right, and what do we measure?" It works for anyone building a conversational agent
with tools; the cart is incidental.

- **App:** the npm scripts in a terminal in `demos/grocery-assistant`; there is no UI of its own
- **Observability:** Langfuse self-hosted or Cloud, project `grocery-assistant`, trace `handle-chat-message` (broken mode: `chat: <the shopper's message>`)
- **Models:** `AGENT_MODEL` (default `claude-sonnet-4-6`) plays the assistant; every evaluator shown live is deterministic
- **Data:** five fictional Northwind Grocers shopper conversations in `src/conversations.ts`
- **Run length:** 25 min (Opening, Acts 1 and 3, Close); 30 with the optional Act 2; Act 4 adds 5 when asked. Short path is the Opening and Act 1 (12 min)

> Setup lives in [README.md](README.md).

---

## How to run this script

It's a conversation, not a walkthrough. Every act does four things:

- **Frame** — the problem, in their terms. Say it before you touch the screen.
- **Show** — the exact clicks and commands.
- **Land** — the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

The Ask answers tell you which act gets the time. Keep the Opening to a minute and go
straight to the broken trace. Act 1 alone, closing on its Ask, is a strong 12 minutes.
Run Act 2 when the audience builds tool-calling agents, and Act 4 only if they have
multi-turn sessions and someone has asked about them.

---

## 0 · Pre-flight

```bash
cd demos/grocery-assistant
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
npm run demo                              # the day before: comparison, dataset run, session scores (a few minutes, some model spend)
npm run compare:loop                      # only if you run Act 2
npx tsx scripts/verify_demo.ts --links    # READY, plus the link for every act
```

`verify_demo.ts` exits 1 when a required check fails. After `npm run demo -- --quick`
(no dataset run), check with `--quick`, which only warns about Act 3's dataset run. Fix what it names before the meeting:
an empty Sessions view is a bad first slide. Don't run `npm run demo` live. Prep it.

**Tabs to open:**

1. Langfuse → **Sessions**, filtered to this project
2. Langfuse → **Tracing**
3. A terminal in `demos/grocery-assistant`

Know which two session ids `npm run compare` produced. They are printed at the end
of its output and named `cmp-broken-*` / `cmp-good-*`. Act 2 adds a
`cmp-collapsed-*`. `--links` prints the newest of each.

To select a run in the UI, **filter the Traces table by tag** — `compare:broken`,
`compare:good`, `compare:collapsed`. Use the tag rather than the session for the
broken run: those traces carry no `sessionId`, so the Sessions view cannot show
them. Full breakdown in the README under
[Telling the runs apart in the Langfuse UI](README.md#telling-the-runs-apart-in-the-langfuse-ui).

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| A well-shaped trace: stable name, root I/O, propagated session, recorded generations | Act 1 |
| Five instrumentation defects, counted from the API rather than asserted | Act 1 (`npm run compare`) |
| A collapsed agent loop: one generation per turn where the app made several model calls | Act 2 |
| Deterministic evaluators on system state before any judge | Act 3 |
| A dataset derived from code fixtures and a dataset run with real denominators | Act 3 (prepped by `npm run demo`) |
| A session-level score written from your own code | Act 4 |

---

## Opening · The app works; can you measure it? (1 min)

**Frame.** Most teams I meet already have their assistant traced in Langfuse. The next
question is always the same two: is this set up right, and what do we measure?

**Ask.** "What do you trace today, and who actually reads it?" and "Have you tried to put
an evaluator on those traces yet?"

**Land.** "Let me show you the same assistant traced two ways, and then which evaluator
I'd build first. Same model, same tools, same answers."

---

## Act 1 · What a good trace looks like (11 min)

**Frame.** Before you touch the screen:

> "Before we talk about evaluation, I want to look at your traces — because
> everything downstream reads them. The failure mode I see most often isn't a
> broken app. It's an app that works fine and can't be measured. Let me show you
> what that looks like, because it's subtle."

**Show.** Open the **Traces table filtered to tag `compare:broken`** first. Do not explain it
yet. Let them look.

> **Run Act 1 from the Traces table, not the Sessions view.** The broken run
> carries no `sessionId` — that is defect 5 — so its session view is genuinely
> **empty**. "Open the broken session" is not a step you can perform; the tag is
> the only handle on this run. Defect 5 gets shown deliberately at beat 5 instead,
> where the emptiness is the point rather than an accident on your opening slide.

Then walk the five things, letting them notice each one:

1. **The Traces table.** Every trace has a different name — the shopper's message
   *is* the name. Ask: "if you wanted a dashboard of this endpoint's latency, what
   would you group by?"
2. **Blank input and output columns.** Nothing to read at a glance.
3. **Open a trace, click a generation.** Empty. Model and tokens are there; input
   and output are null. This is the one to sit on: *"an evaluator reads input and
   output off an observation. There is nothing here for one to read."*
4. **Stay in the trace and open the ROOT's Input.** On the last turn
   `conversationHistory` holds the entire conversation; on turn 2 it holds one
   exchange. Click back through a couple of turns and let them watch it grow — on
   the demo's default 7-turn conversation it goes 0, 2, 4, 6, 8, 10, 12 messages.
   *"The model needs that history. The trace root does not."*
   Read it here rather than from the session view, which for this run does not
   exist — see beat 5.
5. **The session that isn't there.** Now make the emptiness the point: *"This run
   doesn't appear in the Sessions view at all. Not one observation in it carries a
   session id — it was written into metadata instead of propagated. The only way I
   can find these seven traces is by tag."* The zero is the invariant worth saying
   out loud; `npm run compare` gives you the denominator on the day.

Now the same conversation, good instrumentation — and **this one you open as a
session**, which is itself half the contrast. Same model, same tools, same answers.

Then the receipt — run it live, it takes seconds:

```bash
npm run compare
```

It prints the counts. Numbers land better than a tour. Note it starts a **new**
pair and prints new session ids, so the tab you had open is now the previous run —
either narrate that, or keep the terminal to the table and leave your tabs alone.

**Land.** Say it plainly:

> "Same application. Same answers. The only difference is instrumentation — and one
> of these you can build an evaluator on top of, run a dataset experiment against,
> and put a judge on. The other one you can't, and you'd only find that out weeks
> later when you tried."

The one-line version of each fix, from [docs/GOOD_TRACE.md](docs/GOOD_TRACE.md):
stable low-cardinality trace name; input and output on the root; history to the
model, not restated on the root; attributes propagated rather than set on the root
alone; and record input/output — redact fields if you must, but do not switch
capture off.

**Ask.** "Which of those five would you find if you opened your own project right now?"

That question does the work. Most teams find two or three, and it moves the
conversation from theory to their own backlog. If they have a screen to share,
follow it — the rest of the demo can wait.

> **Fallback:** if the comparison sessions are missing, `npx tsx scripts/verify_demo.ts`
> says so. Run `npm run compare` and narrate the table it prints while the traces land.

---

## Act 2 · The one that looks fine (optional, 5 min)

**Run this act when the audience builds tool-calling agents.** Skip it for a
single-shot RAG or classification pipeline, where there is no loop to collapse.
Prep: `npm run compare:loop` alongside `npm run demo`.

**Frame.** Before you touch the screen:

> "Those five are the easy ones — once you know to look, you can see all of them
> from the traces table. I want to show you one you can't, because the trace looks
> right."

**Show.** Open a **collapsed** turn. Say nothing about it being wrong. Ask instead:

> "How many times did this turn call the model?"

They will say one, because there is one generation. Then read them the count off
the terminal — the app made 17 model calls across 7 turns, and the traces recorded
7. Then open the same turn from the **good** session: two generations with the
tool call sitting between them.

The line that lands the point:

> "This is where a tool-calling agent actually goes wrong — not in the final
> answer, but in what it decided after a tool came back empty. In the collapsed
> version that decision isn't missing detail. It was never recorded."

Then the second cost, which is the one that gets budget attention:

> "Same for cost. One aggregate token count per turn. When your context window
> blows up, every step averages out and none of them looks expensive."

`npm run compare:loop` prints the receipt. The row to point at is **generations vs
actual model calls — 7 of 17 against 17 of 17**. Worth naming out loud: the 17 is
from the app, not from Langfuse. The application says how many times it called the
model; the trace says how many it kept. Those counts come from the default
conversation; read the ones your own run printed.

**Land.** Then:

> "And notice this mode is instrumented *well* everywhere else — stable name, root
> input and output, session propagated. Rows one through five come out level with
> the good run. This is a defect that passes review, which is why I bring it up
> before anyone starts hand-rolling spans."

**Ask.** "Who owns your tracing wrapper — the framework, or code someone on your team
wrote?"

Hand-rolled wrappers are where this comes from, and the answer tells you whether
they already have it.

---

## Act 3 · Which evaluator first (12 min)

**Frame.** Before you touch the screen:

> "Most teams I talk to are about to build a scorecard: helpfulness, relevance,
> maybe hallucination, one to five. I'd push back on all of it, and the reason is
> cheaper and more interesting than it sounds."

**Show.** Start with the argument, not the code. One sentence from the Langfuse guidance
carries the whole act:

> *"grade the outcome in the environment, not the claim in the transcript"*

Then make it concrete in their domain:

> "The assistant says 'I've added oat milk to your cart.' A judge reading that
> sentence believes it — it's fluent, it's confident, it's exactly what a good
> answer looks like. But you don't have to read the sentence. You can look in the
> cart."

Run a conversation and show `unverified-cart-claim` firing. In this one the shopper asks
for an item that is out of stock, so the cart refuses it:

```bash
npm run chat -- --conversation unverified-cart-claim
```

Then the table in [docs/FIRST_EVALUATOR.md](docs/FIRST_EVALUATOR.md): four
deterministic checks, then one judge. Walk the *reasons* for the order — each one is
a different argument:

- `unverified-cart-claim` — outcome in the environment. Free and exact.
- `fabricated-purchase-history` — ask an assistant with no history tool what you
  usually buy and it will invent plausible groceries. Perfectly fluent. Completely
  wrong. A judge scores it well.
- `dropped-dietary-constraint` — the cross-turn one. Turn 6 in isolation is fine.
  It is only wrong in light of turn 1.
- `stale-discount-quoted` — the shopper otherwise finds out at checkout.
- `unavailability-obscured` — the first one that genuinely needs a model, which is
  the test for reaching for one.

If they push on "why not just use the evaluator library", quote it directly:
ready-made metrics *"measure abstract qualities that may not match how your
application fails"*, and *"name after what broke"* — `missing_device_lookup` beats
`information_quality`.

**Land.** Then:

> "Four checks, no model calls, before you write a single rubric. And when you do
> write one, it's for the one question code can't answer. That's a very different
> project from a nine-metric scorecard, and it starts producing signal this week."

Then the honest part, which buys credibility:

> "One thing I'd flag: none of this tells you what to measure. That comes from
> reading your own traces. Langfuse's own guidance is 30 to 50, yourself, before
> you build anything — teams that skip it end up measuring things that don't
> matter. The demo can show you the mechanics; the taxonomy has to be yours."

**Ask.** "If you had to name one failure you've already seen in your own traces — not a
category, an actual thing that happened — what is it?"

That is the first evaluator. Offer to build it with them.

> **Fallback:** a conversation makes its failure reachable, not certain. If the check
> passes on this run, say so — that is the honest version — and open the newest
> `deterministic-board-*` run on the `grocery-conversations` dataset, where the pass
> rates carry their denominators.

---

## Act 4 · Scoring the conversation (optional, 5 min)

Only if they have multi-turn sessions and someone has asked about them.

**Frame.** Every check so far scores a turn. The question they asked is about the whole
conversation, and no managed rule can answer it on its own.

**Show.** Run it, then open the good comparison session and its
`conversation-cart-integrity` score: a score on the session, with no trace id.

```bash
npm run evaluate:live
```

**Land.** The point to make: Langfuse has no session-level LLM-as-a-judge rule, and cannot,
because *"Langfuse does not inherently know when a session has concluded."* So a
conversation-level number comes from your own code
(`score.create({ sessionId })`), from a human annotating the session, or from a
dedicated observation you emit on the last turn for a judge to match. The app
decides when the conversation ended; the platform never can.

**Ask.** "Where in your app do you know that a conversation has ended?"

---

## Close · Measurable first, then measure what broke (1 min)

1. Same app, same answers: only the instrumentation decides whether you can evaluate it.
   Check your own project for the five defects before you build anything on top.
2. Count generations against the model calls your app made. A collapsed loop passes
   review and hides every per-step decision and cost.
3. Build deterministic checks on system state first, named after what broke, and keep
   the judge for the one question code can't answer. The taxonomy comes from reading
   your own traces.

Hand over the repo: `demos/grocery-assistant` runs on their own Langfuse project with the
steps in the README, self-hosted or Cloud.

---

## Show me the code

**1 · A stable trace name, with the session propagated to every observation — `src/assistant.ts:204`**
The good turn opens its root as `handle-chat-message` (`src/assistant.ts:109`) and calls
`propagateAttributes` at `src/assistant.ts:208`, so the session, user and tags land on
every child observation, not just the root.

**2 · One turn's input on the root, the history only to the model — `src/assistant.ts:220`**
The root gets `{ message }` and nothing else; the full history goes into the model call.
That keeps the Sessions view readable (defect 4 is the opposite).

**3 · Defect 1, reproduced with the real switches — `src/assistant.ts:323`**
Broken mode passes `recordInputs: false, recordOutputs: false`, the way it usually
happens: switched off for PII on day one. The good mode records both at
`src/assistant.ts:237`.

**4 · Defect 5: the session written to metadata instead of propagated — `src/assistant.ts:310`**
The broken root carries `sessionId` in free-form metadata, so no observation has a
session and the Sessions view cannot group the run.

**5 · Defect 6: the loop flattened into one generation — `src/assistant.ts:386`**
Collapsed mode hand-rolls one `generation` observation and turns the AI SDK's telemetry
off (`src/assistant.ts:404`), which takes the per-step generations and the tool spans
with it.

**6 · Grade the outcome in the environment — `src/evaluators/deterministic.ts:155`**
`unverifiedCartClaim` reads what the assistant said it added and checks it against the
cart, with no model call.

**7 · Not applicable is not a pass — `src/scoring.ts:42`**
A verdict that did not apply is never written as a score, so a pass rate keeps a
denominator that means something.

**8 · A score on the session, with no trace id — `scripts/score-live-sessions.ts:406`**
`score.create({ sessionId, ... })` attaches the number to the whole conversation.

**9 · The demo's `.env` is the only source of Langfuse settings — `src/env.ts:21`**
`loadDemoEnv()` (`src/env-file.ts`) clears every inherited `LANGFUSE_*`, requires the
`.env` and both keys, then `verifyProject()` refuses keys for another project.

**10 · Readiness counts real rows — `scripts/verify_demo.ts:530`**
It lists root observations through Observations API v2 (the v1 list on a 3.x server),
finds the newest comparison runs, then counts each run's generations and checks the
broken ones really have null input. It also requires the newest dataset run to cover
all five conversations and to carry evaluator scores on its own items (read from Scores
API v3 by the run's trace ids on a 3.x server).

---

## Talking points & objections

Full answers with code in [docs/CUSTOMER_QUESTIONS.md](docs/CUSTOMER_QUESTIONS.md).
The five most common, with the short version:

**"We turned off input/output capture for PII — is that wrong?"** Redact fields
rather than disabling capture. A redacted value is still evaluable; an absent one
is not, and you lose every downstream feature.

**"How do we evaluate a whole conversation rather than a turn?"** You can't point a
managed judge at a session. Put the transcript on one observation and target that,
or write a session score from your code.

**"Can we chain a cheap check into an expensive judge?"** Yes as a pattern in your
code — this demo does it. No as a platform feature; the shipped controls are rule
filters and sampling rate. Don't let anyone sell you the wiring.

**"How many observations is too many?"** The only rule stated: if an observation has
neither input nor output, ask whether it should exist. Framework noise (`GET /api/...`,
`sql`) also counts toward billable units.

**"Your *good* trace has empty spans in it — isn't that your own anti-pattern?"**
Expect this from whoever is reading the tree most carefully, and do not wave it
away: they are right about the facts. On the demo's default conversation the good
run has **17 empty observations out of 67**, and every one of them is a `step N`
span from the AI SDK.

The answer is that two rules from the same page pull in opposite directions here,
and the nesting rule wins. Those empty spans are the **structural parents**:

```
AGENT  invoke_agent
├── SPAN  step 1          ← empty, and load-bearing
│   ├── GENERATION  chat        ← the model asking for a tool
│   └── TOOL        manage_cart ← the tool it asked for, as a SIBLING
└── SPAN  step 2
    └── GENERATION  chat        ← what it decided after the result
```

That is precisely the shape Langfuse asks for — *"a tool call should nest under the
`agent` or `span` that orchestrates the step, as a sibling of the `generation` that
requested it"*. `step 1` **is** that orchestrating span. Filter it out and its
children are orphaned (the OTel integration docs warn about exactly this), and you
lose the interleaving that Act 2 exists to show.

So read the empty-observation rule as being about observations that carry nothing
**and organise nothing**. These organise. `LangfuseSpanProcessor` does take a
`shouldExportSpan` hook if you want to drop spans — use it on genuine noise like
`GET /api/...`, not on the spans holding your tree together.

Worth conceding the one real cost out loud: those 17 spans are billable units that
carry no data. If someone pushes on that, the honest answer is that it is the
framework's choice rather than yours, and the tree is worth more than the ingest.

---

## Known limits

- Don't open with the architecture. Open with the broken trace.
- Don't claim Langfuse says history duplication is an anti-pattern. It doesn't;
  it's a consequence of the per-turn-trace guidance. Say it that way — technical
  audiences check.
- Don't promise the evaluator library will fit them. The point of Act 3 is that it
  probably won't.
- The judge in Act 3's list is written but not run by any script here. Say the four
  deterministic checks are what the demo runs.
- Each conversation makes a failure reachable, not certain. If a check passes on the
  day, say so.

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

Nothing a rehearsal leaves behind needs deleting: every run gets new session ids, and
`verify_demo.ts` reads the newest.

```bash
npm run seed:dataset                # puts the dataset back: reports each item created, updated or unchanged (archive extras in the UI)
npm run compare                     # a fresh broken/good pair with new session ids
npm run compare:loop                # a fresh collapsed/good pair, for Act 2
npm run evaluate:live               # session scores for the new sessions
npx tsx scripts/verify_demo.ts      # READY again
```

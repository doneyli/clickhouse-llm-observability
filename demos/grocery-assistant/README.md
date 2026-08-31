# Grocery Assistant — a retail-grocery demo about trace shape and evaluator choice

A conversational shopping assistant for **Northwind Grocers**, a fictional
regional grocery chain, instrumented with Langfuse. TypeScript, Vercel AI SDK 7,
`@langfuse/*` v5.

This demo exists to answer two questions that come up in almost every first
conversation with a team putting an AI assistant into production:

1. **[What does a good trace look like?](docs/GOOD_TRACE.md)** — and what do you do
   when yours does not. The demo ships the *same assistant* instrumented two ways:
   one with five real defects, one correct. Same model, same tools, same answers —
   only one of them is measurable.
2. **[Which evaluator should I build first?](docs/FIRST_EVALUATOR.md)** — the answer
   here is four deterministic checks before the first model call, and the reasoning
   generalises well beyond groceries.

There is also **[CUSTOMER_QUESTIONS.md](docs/CUSTOMER_QUESTIONS.md)** — the
questions engineering teams actually ask, with worked answers and the code.

> **Not grocery-specific.** The domain is a shopping cart, but every lesson here is
> about conversational agents with tools and multi-turn state. A support assistant,
> a booking agent, or a banking assistant hits the same five defects and benefits
> from the same evaluator order.

---

## Why a broken mode

The uncomfortable thing about instrumentation defects is that **the application
works**. The shopper gets a good answer, the cart is correct, latency is fine. The
only thing that is broken is your ability to tell — and you find that out later,
when you try to add an evaluator and discover there is nothing to score.

So the demo makes that concrete rather than describing it:

```bash
npm run chat:broken     # the same conversation, badly instrumented
npm run chat            # the same conversation, correctly instrumented
npm run compare         # both, side by side, with the numbers
```

`npm run compare` queries the Langfuse API and prints a table proving the
difference — distinct trace names, generations with null I/O, observations missing
`sessionId`, whether the root carries input and output. It counts rather than
asserts, because that is the habit worth building.

The five defects, all reproduced faithfully (details and fixes in
[docs/GOOD_TRACE.md](docs/GOOD_TRACE.md)):

| # | Defect | What it costs you |
|---|---|---|
| 1 | Generations with null input/output | No evaluator can read anything. Blocks everything else. |
| 2 | Trace named after the user's message | Nothing groups; no rule can target the endpoint |
| 3 | No input/output on the root observation | Blank Traces table; root-targeted judges see nothing |
| 4 | Conversation history restated on every root | The Sessions view becomes unreadable |
| 5 | `sessionId` on the root only, not propagated | Observation filters miss; cost never rolls up to the session |

Defect 1 is reproduced with the AI SDK's real `recordInputs`/`recordOutputs: false`
switches — the way it usually happens: turned off early for PII, never turned back
on.

---

## Setup

Prerequisites: **Node 22+** (the `@langfuse/vercel-ai-sdk` integration requires it;
this was developed on 24), and a Langfuse instance.

```bash
cd demos/grocery-assistant
npm install
./scripts/provision-project.sh      # creates a dedicated 'grocery-assistant' project + keys
```

`provision-project.sh` targets the **self-hosted** stack from this repo's root
(`docker compose --profile langfuse up -d`, Langfuse on `:3001`). It creates the
project directly in Postgres, mints an API keypair, verifies the keys over HTTP,
and writes `.env`. It is idempotent.

Then add a model key to `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

**Langfuse Cloud instead?** Create a project in the UI, then put its keys plus
`LANGFUSE_BASE_URL=https://us.cloud.langfuse.com` and
`LANGFUSE_PROJECT_NAME=<your project>` in `.env` and skip the provisioning script.
Everything else is identical — nothing here depends on self-hosting.

`src/env.ts` loads this folder's `.env` with `override: true` and refuses to run if
the keys resolve to an unexpected project. That guard exists because a stray
`LANGFUSE_*` export in your shell will otherwise send every trace somewhere else,
and the resulting 404s look like application bugs.

---

## Running it

```bash
npm run demo             # one-shot: seed, compare traces, run conversations, score sessions
npm run demo -- --quick  # skip the experiment

npm run chat -- --list                        # the available conversations
npm run chat -- --conversation dietary        # one conversation, good instrumentation
npm run chat:broken -- --conversation dietary # the same one, badly instrumented
npm run compare                               # side-by-side proof

npm run seed:dataset     # the conversation dataset
npm run experiment       # run the dataset, score every turn, record a dataset run
npm run evaluate:live    # write a session-level score per conversation

npm run typecheck
```

Each conversation is built so a specific failure is *possible* — the assistant may
or may not fall into it on a given run, which is the honest version of a demo. The
evaluators tell you which happened.

---

## Layout

```
src/
  env.ts                     credentials, key isolation, project verification
  instrumentation.ts         NodeSDK + LangfuseSpanProcessor + registerTelemetry
  catalog.ts                 the fictional catalog, order history, and offers
  tools.ts                   6 tools; enum'd vocabularies, unsupported filters reported
  assistant.ts               runTurn(), instrumented two ways on purpose
  conversations.ts           5 multi-turn shopper fixtures, one per failure mode
  scoring.ts                 verdicts -> Langfuse scores (observation / trace / session)
  evaluators/
    deterministic.ts         the four to build FIRST — no LLM in any of them
    judge.ts                 the one that needs language understanding
scripts/
  provision-project.sh       dedicated Langfuse project + keys
  run-conversation.ts        drive one conversation, either mode
  compare-traces.ts          the headline: broken vs good, counted from the API
  seed-dataset.ts            the dataset
  run-experiment.ts          dataset run + scores + pass rates with denominators
  score-live-sessions.ts     session-level scores
  run-demo.ts                one-shot prep
docs/
  GOOD_TRACE.md              lesson 1
  FIRST_EVALUATOR.md         lesson 2
  CUSTOMER_QUESTIONS.md      the FAQ, with code
DEMO_SCRIPT.md               presenter runbook
```

---

## Two details that will bite you in your own build

**`fields` must include `io`.** Reading observations back over the API,
`GET /api/public/v2/observations?...&fields=core,basic,io` — without `io`, `input`
and `output` come back undefined even when populated, which looks exactly like
missing data. And they arrive as **serialized JSON strings**, so `JSON.parse` them.

**AI SDK 7 changed how telemetry is enabled.** It is
`registerTelemetry(new LangfuseVercelAiSdkIntegration())` once at startup, and
telemetry is then on by default. The `experimental_telemetry: { isEnabled: true }`
per-call flag is the **v6** path and does nothing on 7 — which produces no LLM
spans at all and looks identical to a broken exporter.

---

## Notes on scope

- Every evaluator here is **reference-free**, so all of them can run on live
  traffic. Reference-based checks belong in the dataset experiment.
- The judge is deliberately singular. Four free checks first is the lesson.
- The catalog, the brand, the offers, the order history and the shoppers are all
  invented.

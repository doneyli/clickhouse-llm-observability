# What a good trace looks like

> Run `npm run compare` to see everything on this page as two real traces, side
> by side, in your own Langfuse.

A trace is not a log. Langfuse builds four features directly on top of trace
structure, which is why shape is load-bearing rather than cosmetic:

> "LLM-as-a-judge evaluators target observations by name and type, and read their
> input and output. Dashboards filter and aggregate metrics by trace and
> observation names. Dataset experiments compare trace input and output across
> runs. Saved views on the tracing table reference names and attributes."
> — [Langfuse: what does a good trace look like?](https://langfuse.com/docs/observability/best-practices)

The practical consequence: **an app can be working perfectly and still be
unmeasurable.** That is the situation this demo reproduces, then fixes.

---

## The shape, for a conversational app

**One trace per turn. One session per conversation.**

> "For a chatbot this means one trace per turn and one session per conversation —
> you don't know upfront when a conversation ends, and the per-turn model keeps
> traces small and easy to navigate in the session view."

That parenthesis — *you don't know upfront when a conversation ends* — is worth
holding on to. It is the reason there is no such thing as a session-level
LLM-as-a-judge rule, and it comes back in
[FIRST_EVALUATOR.md](./FIRST_EVALUATOR.md).

```
session: shopper-4471                     ← the conversation
├── trace: handle-chat-message  (turn 1)  ← one invocation of your system
│   ├── generation: ai.generateText       ← carries model, tokens, cost
│   ├── tool: search_products
│   └── tool: manage_cart
├── trace: handle-chat-message  (turn 2)
└── trace: handle-chat-message  (turn 3)
    └── span: conversation-snapshot       ← emitted once, on the last turn
```

---

## The five defects, and what fixes each

Run `npm run chat:broken` and `npm run chat` to produce one of each.

### 1. Generations with null input and output

**Symptom.** The trace tree looks right — a generation per LLM call, nested
correctly — and every one of them is an empty box.

**Cause, in this demo and in the wild.** Input/output recording was switched off
early, usually for PII, and never switched back on:

```ts
telemetry: { recordInputs: false, recordOutputs: false }   // ← the whole bug
```

**Why it is the first thing to fix.** Every evaluator reads input and output off
an observation. With them null there is nothing to score, so no judge can run, no
dataset experiment can compare anything, and annotators open a queue item and see
a blank. This defect blocks every other capability, which makes it the gate.

**Fix.** Record them. If some fields are genuinely sensitive, redact the fields
rather than disabling capture wholesale — a redacted value is still evaluable, an
absent one is not.

### 2. A high-cardinality trace name

**Symptom.** The Traces table is a list of unique strings. Nothing groups.

```ts
// broken — every trace gets its own name
startActiveObservation(`chat: ${message.slice(0, 60)}`, ...)

// good — one stable name; the question goes in the INPUT
startActiveObservation("handle-chat-message", ...)
```

Langfuse states this one outright:

> "**Keep dynamic values out of names.** Use `process-order`, not
> `process-order-8945` … A name should identify the operation, not a single
> execution of it — otherwise every trace produces new names and you can no
> longer group, filter, or target them. Put run-specific values in metadata
> instead."

And the reason it matters more than it looks:

> "Because names are referenced in all these places, treat them like an API: when
> a name changes, evaluators, dashboard queries, and saved filters that target the
> old name silently stop matching."

Two corollaries the docs also state: name observations verb-first
(`retrieve-context`, not `context`), and **never name one after the model** —
every filter breaks the day you swap `claude-sonnet` for something else, and the
model is already a first-class attribute on `generation` observations.

### 3. No input or output on the root observation

**Symptom.** Blank input and output columns in the Traces table, and a
root-targeted evaluator that produces nothing.

> "The **root observation** deserves the most care: the trace-level input and
> output are derived from it. They are shown in the tracing table, read by
> evaluators, and compared across runs in dataset experiments."

In v4 there is no separate trace record to fall back on:

> "A trace is all rows that share a `trace_id`, and trace-level attributes are
> copied onto every row." … "Trace-level input/output is deprecated across the
> product."

So an empty trace input is not necessarily a bug — check the observation. But if
the root has nothing either, there is genuinely nothing there.

```ts
root.update({
  input: { message },   // what a reviewer needs at a glance,
  output: answer,       // not a JSON blob of function arguments
});
```

Raw payloads belong in `metadata`, which the docs recommend for exactly that.
(One caveat worth knowing: metadata set through `propagateAttributes` is capped at
200 characters per value and dropped above it, so propagated metadata is for short
scalars. Metadata set directly on an observation has no such cap.)

### 4. Conversation history restated on every turn's root

**Symptom.** The Sessions view is unreadable: turn 3 shows turns 1 and 2 again,
turn 4 shows all three, and so on.

**Be precise about the provenance of this one.** Langfuse does not name history
duplication as an anti-pattern anywhere. It is a consequence of a rule Langfuse
*does* state — per-turn traces exist so that "the per-turn model keeps traces
small and easy to navigate in the session view" — and restating the transcript on
each root gives that property away.

**The distinction people collapse:**

| Needs the full history | Does not |
|---|---|
| The **model call** — that is how a follow-up resolves | The **trace root's** input |
| **One** dedicated observation, if you want to judge the whole conversation | Every turn's root, repeatedly |

```ts
// The model gets the history.
messages: [...history, { role: "user", content: message }]

// The root does not.
root.update({ input: { message } });
```

### 5. Session and user set only on the root

**Symptom.** Filtering observations by `sessionId` returns almost nothing, and
per-generation cost never rolls up to the conversation.

Langfuse flags this as *Important* for OTel-direct ingestion:

> "If you want to filter and aggregate by `userId`, `sessionId`, `metadata`,
> `version`, `release`, or `tags`, you need to propagate these trace-level
> attributes to every span in the trace." … "Langfuse filters and aggregations
> increasingly operate across individual observations rather than only at the
> trace level."

```ts
await propagateAttributes(
  { traceName: TRACE_NAME, sessionId, userId, tags, metadata: { turn: "3" } },
  async () => { /* every observation created in here inherits them */ },
);
```

Using the Langfuse SDK, `propagateAttributes` handles this. Sending OTel spans
directly, you set the attributes yourself on every span — and note that plain OTel
attributes land in an unqueryable `metadata.attributes` catch-all unless you use
the `langfuse.trace.metadata.*` / `langfuse.observation.metadata.*` prefix.

---

## Three more things worth getting right

**Observation types are what make cost work.** An LLM call must be a `generation`,
because only a generation carries model, token usage, and cost. A tool call should
be a `tool`, which then becomes filterable when you scope an evaluator. Langfuse
needs three things on a generation to attribute spend: a model name that matches
its pricing table, usage details, and optionally explicit cost details.

**Drop observations that carry nothing.**

> "In general, it's recommended that operations have an input and/or output. If an
> observation has neither, ask yourself if an observation is actually useful or if
> you can drop it."

The broken mode in this demo emits a `postprocess` span with no data, of the kind
that accumulates when instrumentation is added defensively. Framework noise —
`GET /api/...`, `sql`, `/ping` — is the same problem at scale, and it counts
toward billable units.

**Flush in short-lived processes.** Every script here calls `flushTraces()` before
exiting. Skipping it is the most common cause of "the run finished but Langfuse is
empty" — the batch never left the process.

---

## Verifying it yourself

`npm run compare` runs the same conversation twice and counts the difference. It
queries the API rather than asserting, because that is the habit worth building.

**Which read API you get depends on your server generation** — and this trips
people up, because the failure looks like missing data rather than a missing
endpoint. Check it first:

```bash
curl -s $LANGFUSE_BASE_URL/api/public/health     # {"status":"OK","version":"3.221.1"}
```

**On v4 (Cloud today, self-hosted v4):**

```
GET /api/public/v2/observations?...&fields=core,basic,io
```

Two traps in that one call, both of which look exactly like empty data:

- **`fields` must include `io`.** Without it, `input` and `output` come back
  undefined even when they are populated.
- **`input` and `output` arrive as serialized JSON strings**, not objects.
  `JSON.parse` them.

**On v3, that endpoint does not exist at all:**

```
HTTP 404
{"message":"The observations v2 API is only available in a Langfuse v4 write mode.",
 "error":"LangfuseNotFoundError"}
```

Use the v1 equivalents instead — page-based, and `input`/`output` come back as
plain objects with no `fields` parameter:

```
GET /api/public/observations?limit=N      # flat list
GET /api/public/traces/{id}               # the trace WITH its observations array
```

The most robust approach either way is to collect the trace ids your application
already returns and fetch those directly, rather than relying on server-side
session filtering.

This is worth knowing beyond this demo: the same v3/v4 split applies to the
Metrics API, and it is the kind of thing that reads as "my traces are empty" when
it is really "that endpoint isn't there".

---

## If you are on OpenTelemetry directly

Points that only apply to OTel-native ingestion, all from
[the OTel integration docs](https://langfuse.com/integrations/native/opentelemetry):

- Send `x-langfuse-ingestion-version: 4`, or directly-ingested data can be delayed
  by up to 10 minutes. The header selects the v4 path; it does not by itself make
  a legacy span shape v4-ready.
- Input/output are read from `langfuse.observation.input` / `.output`, falling back
  to `gen_ai.prompt` / `gen_ai.completion`, `input.value` / `output.value`, then
  the MLflow keys. Any span with a `model` attribute is treated as a `generation`.
- Do not re-export a span to update it after ingestion — v4 does not reliably
  deduplicate on the read path, so you get duplicate observations and inflated
  metrics.
- Be careful filtering out a parent span: its children become disconnected
  top-level traces, and Langfuse needs a root span to form the trace properly.
- gRPC is not supported; OTLP over HTTP/JSON or HTTP/protobuf only.

---

## Sources

- [What does a good trace look like?](https://langfuse.com/docs/observability/best-practices)
- [Langfuse v4 is live](https://langfuse.com/docs/v4) — observations-first, and the **2026-11-16** Cloud cutover
- [Empty trace input and output](https://langfuse.com/faq/all/empty-trace-input-and-output)
- [Observation types](https://langfuse.com/docs/observability/features/observation-types)
- [Sessions](https://langfuse.com/docs/observability/features/sessions)
- [OpenTelemetry integration](https://langfuse.com/integrations/native/opentelemetry)
- [Vercel AI SDK integration](https://langfuse.com/integrations/frameworks/vercel-ai-sdk)
- [Unwanted HTTP/database spans](https://langfuse.com/faq/all/unwanted-http-database-spans)

Checked against the Langfuse docs on 2026-08-28.

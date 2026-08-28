# Questions teams actually ask

Collected from real engagements with retail and e-commerce teams putting a
conversational assistant into production. Paraphrased and anonymised; the code is
the code we actually recommend.

Ordered roughly by when they come up.

---

## Tracing

### "Our LLM steps show null input and output. Is that us or Langfuse?"

It's you, and it's almost always the same cause: input/output recording was turned
off early — usually for PII — and never turned back on. On the Vercel AI SDK that
is two flags:

```ts
telemetry: { recordInputs: false, recordOutputs: false }   // the whole bug
```

**Why it matters more than it looks.** Every evaluator reads input and output off an
observation. With them null: no judge can run, dataset experiments have nothing to
compare, and a human opening an annotation queue sees a blank. It is not one broken
feature, it is the gate in front of all of them.

**What to do instead of switching capture off.** Redact the fields that are actually
sensitive and keep the rest. A redacted value is still evaluable; an absent one is
not. If your harness has a global "record trace IO" flag, that flag is almost
certainly costing you more than you think — scope it to fields, not to everything.

Reproduce it: `npm run chat:broken`, then open any generation.

### "How many observations should a turn have? We think we have too many."

The only rule Langfuse states:

> "In general, it's recommended that operations have an input and/or output. If an
> observation has neither, ask yourself if an observation is actually useful or if
> you can drop it."

So: no number, one test. Walk your trace tree and delete anything carrying no input
and no output. In practice most of the bloat is not your spans at all — it is
framework noise (`GET /api/...`, `sql`, `/ping`), which also counts toward billable
units. The JS SDK v5+ and Python v4+ filter non-LLM spans automatically; below those
versions there is no automatic filtering.

One caution: filtering out a *parent* span orphans its children into disconnected
top-level traces, and Langfuse needs a root span to assemble a trace properly.

### "We're carrying the whole conversation history into every turn's trace. Bad?"

Yes, and the fix has a subtlety worth getting right, because "stop carrying history
forward" is half correct and the other half is load-bearing.

| Needs the full history | Does not |
|---|---|
| **The model call.** That is how a follow-up resolves. | **The trace root's input.** |
| **One** dedicated observation, if you want to judge the whole conversation. | Every turn's root, repeatedly. |

```ts
// The model gets the history:
messages: [...history, { role: "user", content: message }]

// The root gets this turn only:
root.update({ input: { message }, output: answer });
```

The reason: Langfuse's guidance is one trace per turn, because "the per-turn model
keeps traces small and easy to navigate in the session view". The session view
renders each trace as one turn — so a root containing the transcript renders the
whole history as a single turn, compounding on every turn.

Be precise if you repeat this to your own team: Langfuse does not name history
duplication as an anti-pattern anywhere. It is a consequence of a rule it does
state.

### "Our session IDs are our workflow engine's run IDs, so sessions never end. Is that good or bad?"

Bad, but the fix is a product decision you own, not a setting.

A score attached to an unbounded session has no stable denominator — it is a number
over an arbitrary, growing window, so it cannot trend. We have seen a single
"session" run 187 hours across 100+ traces.

Pick a boundary that matches how people actually use the product. If your assistant
carries a rolling context window of N days, that is your boundary:

- the shopper explicitly clears the conversation → new session
- N days of inactivity → new session

**"But context from the old session still carries into the new one."** True, and it
does not need to break the boundary. Record the previous session id in metadata on
the first trace of the new session. Reporting stays clean, and you can still follow
the thread when debugging. (This is ordinary metadata, not a Langfuse feature.)

### "Which attributes have to go where?"

If you use the Langfuse SDK, `propagateAttributes` handles it:

```ts
await propagateAttributes(
  { traceName: "handle-chat-message", sessionId, userId, tags, metadata: { turn: "3" } },
  async () => { /* every observation in here inherits them */ },
);
```

If you send OTel spans directly, Langfuse is explicit that trace-level attributes
must be on **every** span, not just the root — `userId`, `sessionId`, `metadata`,
`version`, `release`, `tags` — because "filters and aggregations increasingly operate
across individual observations rather than only at the trace level". Two more OTel
traps: plain attributes land in an unqueryable `metadata.attributes` catch-all unless
you use the `langfuse.trace.metadata.*` prefix, and without the
`x-langfuse-ingestion-version: 4` header directly-ingested data can be delayed up to
10 minutes.

### "Our trace names are the user's message. Does that matter?"

It does, and this one Langfuse states outright: "Keep dynamic values out of names …
otherwise every trace produces new names and you can no longer group, filter, or
target them." Names are referenced by evaluators, dashboards and saved filters, so
"treat them like an API" — when a name changes, everything targeting the old one
silently stops matching.

Stable name, question in the input. Also: never name an observation after the model,
because every filter breaks the day you swap it.

---

## Evaluation

### "Which evaluator should we build first?"

Not the one you are thinking of. Full reasoning in
[FIRST_EVALUATOR.md](./FIRST_EVALUATOR.md); the short version:

**Grade the outcome in the environment, not the claim in the transcript.** Your
assistant says "I've added oat milk to your cart." A judge reading that sentence
believes it. You don't have to read the sentence — you can look in the cart.

Four deterministic checks in this demo before the first model call. All free, all
exact, none needing calibration.

And before any of that: read 30–50 of your own traces yourself. Langfuse's own
guidance is blunt about skipping it — "teams that skip this step and jump straight
to automated evaluation often end up measuring things that don't matter."

### "Can we chain evals — a deterministic check first, then a judge on top?"

Yes as a pattern in your code. **No as a platform feature**, and the distinction
matters commercially, so don't let anyone blur it.

What you can do, and what this demo does:

```ts
// Cheap check first. Most turns never pay for a judge at all.
const oos = outOfStockItemsInPlay(ctx);
if (oos.length === 0) return notApplicable(name, "nothing to be honest about");
return await callJudge(ctx);   // only now
```

What Langfuse actually ships for controlling judge volume: **rule filters** (by
observation name, type, metadata, plus trace-level userId/sessionId/tags/version)
and a **sampling rate** on the rule. There is no wiring where one evaluator's score
conditionally gates another's rule.

This is also how the "score every conversation against ~1500 flagged keywords" class
of requirement stays affordable: regex over all of it costs nothing, and only the
hits reach a judge that decides intent.

### "How do we evaluate a whole conversation instead of one turn?"

You cannot point a Langfuse-managed judge at a session, and this is by design:

> "They cannot be applied directly to sessions, as Langfuse does not inherently know
> when a session has concluded."

Three routes, all used in this repo:

1. **A dedicated observation.** Emit one span on the final turn whose input is the
   full transcript, and scope an observation-level rule to that name. Fires once per
   conversation instead of re-judging a growing transcript every turn.
2. **A session score from your own code** — the only way to get a number whose
   subject is the conversation:
   ```ts
   langfuse.score.create({
     sessionId,
     name: "conversation-cart-integrity",
     value: 0.8,
     dataType: "NUMERIC",
     comment: "4/5 turns where this applied passed. Turn 3 claimed an add that never happened.",
   });
   ```
3. **A human.** Annotation queues accept traces, observations **and sessions**, so a
   reviewer can grade the whole conversation.

Whichever you pick, **your application decides when the conversation ended.** The
platform never can.

### "Can we use datasets as a CI gate, so a prompt change has to pass thresholds before merge?"

Yes, and it is one of the higher-leverage things to build early. Run the dataset as
an experiment in CI, compare run-level aggregates against thresholds you commit to
the repo, and fail the build on a regression.

Two things learned the hard way:

- **Gate on the deterministic scores, not the judge.** Judge variance produces red
  builds that mean nothing, and a flaky gate gets switched off within a fortnight.
  Trend the judge; gate on the checks that cannot drift.
- **Run a same-prompt control.** Without a repeat run of the *unchanged* prompt you
  cannot distinguish a real delta from noise, and citing one anyway is the classic
  mistake.

### "Our first eval is a regex checking links point at our own domain. Is that too simple?"

It is a good first evaluator, precisely because it is that simple. It is
deterministic, free, reference-free (so it runs on production traffic), and it
catches a real, user-visible failure. That is a better first metric than any
five-point quality scale.

Two extensions worth making:

- Attach it as a score so it becomes a trend rather than a one-off script run
  locally. If it lives on someone's laptop, it is not a metric.
- Use it as stage one of the chain above: an off-domain link is also the cheapest
  possible signal for "this turn is worth a judge's attention".

### "Should we start from the evaluator library?"

Explore it, but expect to replace it. Langfuse's own position: ready-made metrics
like hallucination, toxicity or helpfulness "measure abstract qualities that may not
match how your application fails."

The naming rule is the practical version — **name after what broke**.
`missing_device_lookup` beats `information_quality`. In this demo,
`fabricated-purchase-history` beats `groundedness`: same failure, but one of them
tells an engineer what to go and fix.

### "How do we know the judge agrees with us?"

Calibrate it, and treat it as a recurring chore rather than a launch task. Build a
small dataset where *you* wrote the labels, run the judge prompt against it as an
experiment, and measure agreement.

The trap to know about: if the failure occurs in 10% of cases, a judge that answers
"pass" every time agrees with you 90% of the time and tells you nothing. Check each
class separately — precision and recall, not accuracy.

Realistic ceiling: 80–90% agreement with human reviewers, which is about what two
humans achieve with each other.

### "We can't show that quality is improving over time. How do we get that story?"

This is usually a data problem before it is a metrics problem, and it has a specific
shape: you need one number, over one fixed set, measured twice.

What makes it work:

- A **fixed dataset**, so the denominator does not move between runs.
- A **deterministic** metric for the headline, so the delta is not judge noise.
- Two runs with **one variable changed**, plus a same-prompt control run.

What we would show a stakeholder who cannot read a trace: one before/after
comparison of the same conversation, the old behaviour, the new behaviour, and a
score that moved. A single number with a readable transcript behind it travels; a
methodology does not.

### "The built-in dashboards don't do what we need."

Common, and there are two honest answers. Score analytics and custom dashboards
cover trends per score name, and the average of a boolean score is exactly its pass
rate — that is often all a leadership view needs. Beyond that, pull via the API or
MCP and build externally. Don't spend a quarter trying to make the built-ins do
bespoke reporting.

---

## Platform and timing

### "We're on v3. Does that matter?"

Yes, and there is a date. **Langfuse Cloud becomes v4-only on 2026-11-16**; legacy
APIs, features and ingestion are removed, and trace-level LLM-as-a-judge evaluators
stop producing results. Self-hosted v4 has been GA since 2026-07-29, and v3 receives
security patches through January 2027.

If you are building evaluators now, build them **observation-level**. Migrating a
trace-level rule later is real work; starting there is free.

### "Does the judge re-run our application?"

No. "Online evaluators … score the data already recorded on your traces at ingestion
time; they never re-execute your application or its LLM calls." Which is also why
trace shape matters so much — the judge sees exactly what you recorded, and nothing
else.

### "What does judge evaluation cost?"

Roughly $0.01–0.10 per assessment, driven by judge model and input size. Three
levers: sample (score 5% rather than 100%), target a specific observation rather
than a whole trace so the judge reads less, and use a cheaper model for simpler
criteria. Sampling lives on the rule.

---

## Sources

Every quoted line above is from Langfuse's own documentation — see the source lists
in [GOOD_TRACE.md](./GOOD_TRACE.md) and [FIRST_EVALUATOR.md](./FIRST_EVALUATOR.md).
Checked 2026-08-28.

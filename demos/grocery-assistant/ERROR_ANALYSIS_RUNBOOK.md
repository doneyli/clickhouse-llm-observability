# Error analysis — demo runbook

**Audience:** a team that has evaluators and still gets complaints, or a team
about to guess at their first evaluator.
**Length:** 20 minutes, 5 stops. Every link below is live.
**Project:** `grocery-assistant`, US Cloud.

> **The one-sentence version:** this assistant has four evaluators, all of them
> reasonable, and they were green on the two conversations that failed nothing —
> while 30% of turns failed in ways none of the four could see. Reading traces is
> what found the difference.

**Before you present:** the labels in this project are synthetic — written by
Claude against real trace content, not by a human reviewer. Every score carries
`metadata.provenance: claude-generated-demo`. Say so if anyone asks how the
labelling was staffed; the mechanics are real, the labour was not.

---

## Pre-flight (2 min, do it before the call)

```bash
cd demos/grocery-assistant
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
npm test                                              # 30 pass
npx tsx scripts/backtest-evaluators.ts --tag ea:pass-1 # 65/66 agree
```

Open these five tabs in order, then start:

1. [Traces](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces) — switch the environment selector to **`error-analysis`**
2. [Session: dropped-dietary-constraint p1](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/sessions/ea-2026-09-09-p1-dropped-dietary-constraint)
3. [Open-coding queue](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/annotation-queues/cmtu85mjy01z8ad0j3me0paeu)
4. [Category-labelling queue](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/annotation-queues/cmtudtnbz0138ad0e8uor24rq)
5. [Dashboard: Error Analysis Round 1](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/dashboards/cmtue2wdv0187ad0dgtllpur8)

---

## Stop 1 — the evaluators are green (3 min)

Most teams arrive here: a board of checks, all passing, and a product manager
who still hears complaints.

Open [stale-discount-quoted, turn 5](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/3322376907e7140f0c3a14713b82fa60).
The shopper removes two items, which silently invalidates a $2 offer that
genuinely applied a turn earlier. The assistant re-reads the offers, leads with
the fact the offer is gone, and gives the corrected total. That is the hardest
trap in the demo and it handled it perfectly.

Then [unverified-cart-claim, turn 5](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/aa1edbb9f362b4939fa8518de6adcf14).
The shopper asks directly whether the avocados made it. The assistant reads the
cart and says plainly that they did not.

**Say:** these two conversations are what the four existing evaluators were built
for, and across 14 turns they failed nothing. If you stopped here you would
report that the assistant is in good shape.

**Ask:** *when your evaluators are green, what tells you they are looking in the
right place?*

---

## Stop 2 — the traffic you can actually study (3 min)

On [Traces](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces),
switch the environment selector to **`error-analysis`**. 99 turns, 15 sessions,
five scripted conversations run three times each.

Point at the tags on any trace: `error-analysis`, `ea:cohort-2026-09-09`,
`ea:pass-1`. Three separations doing three jobs — environment isolates the study
from ordinary demo traffic, the dated cohort tag lets you compare this round
against the one you run after the fix, and the pass tag exists because the
scripts are fixed, so any difference between passes is the model's own
nondeterminism.

**Say:** the reason to label a study cohort this carefully is that error analysis
is not a one-off. You will run it again after the next prompt change, and the
comparison is the whole value.

**Land:** you cannot do this step at all on traces you cannot read. Show a
`compare:collapsed` trace from the default environment if you want the contrast —
same app, same answers, deliberately mis-instrumented, and nothing to annotate.

---

## Stop 3 — read one conversation properly (5 min)

This is the stop that changes minds. Do not rush it.

Open [the dropped-dietary-constraint session](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/sessions/ea-2026-09-09-p1-dropped-dietary-constraint)
and walk the seven turns in order. Then open the two traces that matter:

**[Turn 4](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/d084053573d2e56c10c90f67619e7605)** —
the shopper asks "I'm trying to keep this trip under $35, how am I doing?"

Expand the tool calls before you read the answer. `manage_cart` returns
`{"cart":[],"subtotal":"$0.00"}`. Now read the answer: an itemised table,
**"Subtotal so far $14.97"**, and "you've got plenty of room left under your $35
budget."

Let that sit. The cart is empty. It called the tool that told it so, in the same
turn.

**[Turn 7](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/208206a56dff818a6f31ea444a361c91)** —
the shopper says "that's me done, read back the final cart." The assistant
notices, unprompted: *"I notice I haven't actually added anything to the cart
yet!"* Six turns of quoted totals, and it was right the whole time that nothing
had been added.

**Say:** the conversation is named `dropped-dietary-constraint` and the dietary
constraint never dropped — every search carried the `gluten_free` filter and
every product returned was compliant. The check for the thing we designed passed.
The thing that actually broke was not on anyone's list.

**Ask:** *how would you have found this? It is not a crash, not a latency
spike, and not a low score.*

Then show [the open-coding queue](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/annotation-queues/cmtu85mjy01z8ad0j3me0paeu):
two fields, `open_coding` free text and `pass_fail_assessment`. Describe
behaviour, do not diagnose. 33 turns reviewed, 10 failed.

---

## Stop 4 — the taxonomy and the rates (4 min)

Open [the dashboard](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/dashboards/cmtue2wdv0187ad0dgtllpur8).

Seven categories came out of clustering ten failure notes:

| Category | Rate |
|----------|------|
| `quoted_total_for_empty_cart` | 9.1% |
| `unverified_product_recommended` | 9.1% |
| `explicit_add_not_executed` | 6.1% |
| `readback_request_unanswered` | 3.0% |
| `capability_overpromised` | 3.0% |
| `false_self_report` | 3.0% |
| `data_declared_unavailable` | 3.0% |

Point at the pivot table, not just the bar chart — it shows the rate beside the
denominator, so a 3% category reads as *one turn out of thirty-three* rather than
a trend. Say out loud that you would not ship an evaluator on n=1.

Then the five NUMBER tiles along the bottom — failing turns per conversation:

```
substitution-handling        4 of 6   67%
dropped-dietary-constraint   4 of 7   57%
fabricated-purchase-history  2 of 6   33%
stale-discount-quoted        0 of 7    0%
unverified-cart-claim        0 of 7    0%
```

**Land:** the two conversations from stop 1 — the ones the evaluators cover
best — are the two at zero. Failure was concentrated somewhere nobody was
looking, and **none of the seven categories is covered by any of the four
original evaluators.**

For the individual stories, these three traces carry the room:

- [Invented substitutes](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/6302df6e44182ce6c66aa5c2332ef962) — salmon search returns nothing, so it offers tilapia, cod, shrimp and trout. It searched for none of them. None are in the catalog.
- [Apologising for something it did](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/82434f90a25fb36799e6c17387de0778) — "I owe you an apology, I jumped ahead without checking your history first." It called `get_order_history` in the previous turn. The tool call is right there in the trace.
- [Declaring data unavailable that it just quoted](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/traces/cfaa97a59a3185b0bd4913d18eed6153) — tells the shopper it can only see an item count and to check their email, one turn after reading out the contents of that exact order.

---

## Stop 5 — what you do with it (5 min)

Seven categories do not become seven evaluators. Work down by rate and ask "can
we just fix this?" first.

| Category | Decision |
|----------|----------|
| `explicit_add_not_executed` | prompt fix — and it probably takes `quoted_total_for_empty_cart` with it |
| `quoted_total_for_empty_cart` | code check — compare any quoted total against the cart tool in the same turn |
| `unverified_product_recommended` | LLM judge — needs judgement about whether a search returned it |
| `readback_request_unanswered` | code check |
| `data_declared_unavailable` | LLM judge |
| `capability_overpromised` | prompt fix — give the model its tool inventory |
| `false_self_report` | monitor — n=1 |

Two are now real code, numbers 5 and 6 in
`src/evaluators/deterministic.ts`. Show the backtest live:

```bash
npx tsx scripts/backtest-evaluators.ts --tag ea:pass-1
# compared 66   agreed 65   disagreed 1   → 98.5%
```

**This is the best moment in the demo.** The one disagreement was the *label*
being wrong, not the evaluator. Turn 7 was hand-labelled
`readback_request_unanswered = true` — but "I haven't actually added anything to
the cart yet" does answer "read back the final cart." Writing the check in code
forced a precision the prose note did not have, and the label is now corrected in
Langfuse with the reason in the score comment.

**Say:** this is why you write the evaluator even when the rate looks too small
to bother. It is not only measurement, it is a second opinion on your own
judgement.

**Ask:** *which of your current evaluators came from reading traces, and which
came from a planning meeting?*

---

## Objections

**"Our engineers will not hand-label 100 traces."**
They will not label 100 forever — they label 30 to 50 once, and get a taxonomy
plus two evaluators that run on everything afterwards. Note the two zero-rate
conversations: automation aimed at the wrong target costs more than the labelling
did.

**"Can an LLM do the open coding?"**
For a demo, yes — that is exactly what these labels are, and they still produced
a usable taxonomy. For your own product, the reading is the point: the person who
reads 30 traces learns what users actually ask for, and that transfers to every
decision afterwards. Also worth saying plainly: a judge built from a taxonomy an
LLM invented inherits whatever the LLM failed to notice.

**"We already have evaluators."**
Good — then the question is not "what should we measure", it is "what is failing
that these do not catch". That is the same process with a sharper question, and
this project is the worked example: four sensible evaluators, seven uncovered
failure modes.

**"Why not one 1-to-10 quality score?"**
Ask what you would do differently at 6.3 versus 6.8. Every category here names a
specific broken thing with a specific fix. A single number would have averaged a
67%-failure conversation against a 0% one and reported "fine".

---

## Appendix — commands and IDs

```bash
# regenerate a labelled cohort (one conversation, one pass)
LANGFUSE_TRACING_ENVIRONMENT=error-analysis \
  npx tsx scripts/run-conversation.ts \
    --conversation dropped-dietary-constraint \
    --session-id ea-2026-09-09-p1-dropped-dietary-constraint \
    --tag error-analysis --tag ea:cohort-2026-09-09 --tag ea:pass-1

# read the cohort back (v4 observations; `traces list` is refused on Cloud)
langfuse --env .env api observations list \
  --environment error-analysis --from-start-time 2026-09-09T00:00:00Z \
  --fields core,basic,io,trace_context --limit 500 --all

# the rates, from the same data the dashboard uses
langfuse --env .env api metrics get --query '{"view":"scores-boolean",
  "dimensions":[{"field":"name"}],"metrics":[{"measure":"value","aggregation":"avg"}],
  "filters":[{"column":"environment","operator":"=","value":"error-analysis","type":"string"}],
  "fromTimestamp":"2026-09-09T00:00:00Z","toTimestamp":"2026-09-10T00:00:00Z"}'
```

| Thing | ID |
|-------|-----|
| Project | `cmtu6y6ox01ciad0ckrnkzijz` |
| Open-coding queue | `cmtu85mjy01z8ad0j3me0paeu` |
| Category queue | `cmtudtnbz0138ad0e8uor24rq` |
| Dashboard | `cmtue2wdv0187ad0dgtllpur8` |

Reference and the traps worth knowing before you rebuild any of this:
[docs/ERROR_ANALYSIS.md](docs/ERROR_ANALYSIS.md).

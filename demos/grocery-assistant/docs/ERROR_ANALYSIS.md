# Running error analysis on this demo

> Round 1 cohort: **2026-09-09**. 5 conversations × 3 passes = 99 turns, all
> labelled `environment=error-analysis`.

Error analysis is the step that comes *before* you write an evaluator: you read
real traces, write down what you see, cluster those notes into named failure
categories, then count how often each one fires. The output is a prioritised
list of things to fix — and, for the categories that need judgement rather than a
prompt edit, a specification for the evaluator you were about to guess at.

The authoritative process is Langfuse's
[error analysis guide](https://langfuse.com/guides/cookbook/error-analysis-llm-applications)
(sample selection → open coding → clustering → labelling → deciding what to fix).
This page covers only what is specific to *this* demo: how the cohort is
labelled, how to filter it back out, and the one trap this demo sets for anyone
running the process here.

---

## How this activity is labelled

Traffic generated for error analysis is separated from ordinary demo traffic on
three axes at once, because each one answers a different question.

| Axis | Value | Why this axis |
|------|-------|---------------|
| **Environment** | `error-analysis` | Hard isolation. Langfuse treats environment as a first-class dimension, so the cohort stays out of the default demo view entirely rather than being mixed in and filtered down. |
| **Tags** | `error-analysis`, `ea:cohort-<date>`, `ea:pass-<n>` | Granular slicing *within* the cohort — one round versus the next, one repeat versus another. |
| **Session id** | `ea-<cohort>-p<pass>-<conversation>` | Human-readable in the Sessions list, and re-runnable: the same command overwrites nothing and appends a new pass. |

The cohort tag is dated rather than versioned on purpose. Error analysis is not a
one-time activity — you re-run it after a prompt rewrite, a model switch or an
incident — and `ea:cohort-2026-09-09` versus `ea:cohort-2026-11-02` is how you
compare the failure distribution before and after a change.

> **This is a second axis on top of the demo's own run labelling, not a
> replacement for it.** [README.md](../README.md#telling-the-runs-apart-in-the-langfuse-ui)
> documents the standing scheme — `compare:good` / `compare:broken` /
> `compare:collapsed`, `conversation:<id>`, and session ids shaped
> `<mode>-<conversation>-<stamp>` — and that page is the authority on it. The
> `ea:*` tags and the dated session ids here exist only to carve one *study
> cohort* out of that traffic, and the session id is set explicitly with
> `--session-id`, which is why it does not follow the standing shape.
>
> Two things from that section that matter here: the `--tag` values reach the
> `broken` and `collapsed` paths as well as `good`, and a **broken-mode run
> carries no `sessionId` at all** — that is defect 5, so the Sessions view cannot
> see it. Everything below assumes a `good`-mode cohort, which is the only mode
> where the per-turn Sessions navigation the reviewer needs actually works.

`ea:pass-<n>` exists because the five conversations are **fixed scripts**. The
only variance between passes is the model's own nondeterminism, which turns out
to be the useful signal: a failure that appears in one pass out of three is a
different engineering problem from one that appears in all three, even at the
same headline rate.

### Generating a labelled cohort

`--tag` is repeatable and lands on every trace of the run. The environment comes
from `LANGFUSE_TRACING_ENVIRONMENT`, which the Langfuse span processor reads
directly — no code change, and `src/env.ts` loads `.env` with `override: true`
so it will not be clobbered.

```bash
LANGFUSE_TRACING_ENVIRONMENT=error-analysis \
  npx tsx scripts/run-conversation.ts \
    --conversation dropped-dietary-constraint \
    --session-id ea-2026-09-09-p1-dropped-dietary-constraint \
    --tag error-analysis --tag ea:cohort-2026-09-09 --tag ea:pass-1
```

> **Check your shell first.** If `LANGFUSE_HOST` or `LANGFUSE_PUBLIC_KEY` are
> exported in your shell they will point somewhere else — commonly the
> self-hosted stack on `localhost:3001`. This demo is hardened against that
> (`dotenv` with `override: true`), but the CLI is not: pass `--env` to every
> `langfuse` invocation, or the traces you wrote to Cloud will read back as 404s
> from a different project.

### Filtering it back out

In the UI, switch the environment selector to `error-analysis`, or filter
Traces on the `ea:cohort-2026-09-09` tag.

From the CLI — note `--env`, and note that `traces list` is refused against
Cloud as a deprecated v3 endpoint, so reads go through the v4 observations API:

```bash
langfuse --env .env api observations list \
  --environment error-analysis \
  --from-start-time 2026-09-09T00:00:00Z \
  --fields core,basic,io,trace_context \
  --limit 500 --all
```

Narrow to one pass with `--filter` on tags, or to one scripted conversation with
the `conversation:<id>` tag that `driveConversation` adds on its own.

---

## What to annotate

**The root `handle-chat-message` span, one per turn.**

This demo does not have the problem the Langfuse guide spends most of its
warnings on. In many OpenTelemetry-instrumented apps trace-level input and
output are null and the readable content lives in a `GENERATION` observation, so
annotating the trace shows the reviewer nothing. Here the root span carries the
shopper's message as input and the final assistant reply as output on every
turn — verified present on 21 of 21 good-mode roots — so it is directly
annotatable.

Two consequences worth knowing:

- **The unit is the turn, not the session.** 99 turns across 15 sessions gives
  ~99 independently judgeable units. The guide's alternative — annotate the last
  turn per session — would leave 15, which is not enough to compute a rate you
  would act on.
- **Context comes from the session view, not the span.** The root span's input is
  only the current message; the full history lives on the child `GENERATION`. For
  the failures that span turns — a constraint stated once and dropped four turns
  later — the reviewer needs the Sessions view open alongside the queue.

Exclude anything tagged `compare:collapsed`. Those traces are *deliberately*
mis-instrumented — they are the teaching artifact behind
[GOOD_TRACE.md](GOOD_TRACE.md) — and coding them fills the taxonomy with
instrumentation artifacts instead of application failures. Which is itself the
lesson: error analysis is only possible on traces that were instrumented well
enough to read.

---

## The trap this demo sets

**This demo already ships a failure taxonomy, and it is printed to your terminal
on every run.**

`src/conversations.ts` documents the specific failure each conversation is
engineered to provoke, and `src/evaluators/deterministic.ts` implements four
named evaluators that are scored and printed per turn by
`scripts/run-conversation.ts`.

The Langfuse guide's first listed mistake is *"brainstorming failure categories
before reading traces"*, because a pre-supplied list produces confirmation bias:
you find the categories you were handed and stop looking. Here that list is not
merely available, it is unavoidable.

So the honest framing for running error analysis on this demo is not "discover
the taxonomy from scratch". It is the question the guide points at when an eval
setup already exists:

> **What is failing that the four existing evaluators do not catch?**

That is a real question with a real answer, and the demo is built to reward it —
`src/conversations.ts` notes that `substitution-handling` has no evaluator of its
own, on purpose. Open coding is still done the same way, and the rule still
holds: **describe behaviour, do not diagnose**. Write "said it added the salmon,
but the cart read-back two turns later does not list it", not
"substitution logic is broken".

If you are running this as a rehearsal for doing error analysis on a *real*
application, the discipline to practise is writing observations without reaching
for a category name you already know.

---

## Round 1 setup

| Artefact | Value |
|----------|-------|
| Project | `grocery-assistant` (`cmtu6y6ox01ciad0ckrnkzijz`), US Cloud |
| Score config | `open_coding` — TEXT |
| Score config | `pass_fail_assessment` — CATEGORICAL, `Fail=0` / `Pass=1` |
| Queue | [2026-09-09 Open Coding - Grocery Assistant](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/annotation-queues/cmtu85mjy01z8ad0j3me0paeu) — 99 items, `OBSERVATION`, queued in conversation → pass → turn order |

> **Read back before you trust a count.** The first selection pass came up one
> turn short (98 of 99) because the last conversation had not finished being
> ingested — the observations API answers successfully from a partially-written
> cohort, so a count taken immediately after a run is not authoritative. Re-read
> until the number is stable rather than treating the first `200` as the answer.

> **Annotation queues cannot be modified after creation.** Score configs have to
> exist first, and adding a category label later means creating a *new* queue.
> Scores already recorded on an observation survive that, so re-adding the same
> items to a second queue preserves the open-coding notes — which is exactly the
> mechanism step 4 of the guide relies on when it adds one boolean config per
> discovered failure category.

The per-turn deterministic verdicts printed by `run-conversation.ts` are **not**
ingested as Langfuse scores; `scripts/score-live-sessions.ts`
(`npm run evaluate:live`) is the script that does that. Keeping them out of the
project during open coding is deliberate here: it keeps the human labels
independent, which is what makes them usable afterwards as a calibration set for
the machine verdicts.

---

## Round 1 results

Open coding covered the 33 turns of pass 1. **10 turns failed (30%).** Clustering
those ten notes produced seven categories, each a `BOOLEAN` score config; the
non-failing turns are labelled `false` rather than left absent, because the
average of a boolean score is only the failure rate if the denominator is
recorded.

| Category | Rate | True when |
|----------|------|-----------|
| `quoted_total_for_empty_cart` | 9.1% (3/33) | A subtotal, running total or budget position described items not in the cart |
| `unverified_product_recommended` | 9.1% (3/33) | A specific product, price or stock status was asserted that no search returned |
| `explicit_add_not_executed` | 6.1% (2/33) | A direct instruction to add was answered with a confirmation question |
| `readback_request_unanswered` | 3.0% (1/33) | A read-back was requested and the reply contained no cart contents |
| `capability_overpromised` | 3.0% (1/33) | An action was offered that no tool can perform |
| `false_self_report` | 3.0% (1/33) | The assistant described its own earlier behaviour incorrectly |
| `data_declared_unavailable` | 3.0% (1/33) | The shopper was told data was unavailable that a tool had already returned |

Twelve true labels across ten turns — two turns carry two categories.

One label in that table was **corrected after the fact**, by the evaluator it
produced. See "What backtesting changed" below.

### What the distribution says

Failure is not evenly spread, and it is not where the existing evaluators look.

| Conversation | Fail rate | Has a deterministic evaluator |
|--------------|-----------|-------------------------------|
| `substitution-handling` | 4/6 (67%) | no, by design |
| `dropped-dietary-constraint` | 4/7 (57%) | yes |
| `fabricated-purchase-history` | 2/6 (33%) | yes |
| `stale-discount-quoted` | 0/7 | yes |
| `unverified-cart-claim` | 0/7 | yes |

Two things follow. First, the two conversations whose traps the deterministic
checks target most directly passed **14 of 14** — the checks work, and the
traffic that exercises them is not where the risk is. Second,
`dropped-dietary-constraint` failed four times *without a single dietary
failure*: every search carried the `gluten_free` filter and every product
returned was compliant. The constraint held. The cart was simply never filled,
while six consecutive turns quoted totals for it.

**None of the seven discovered categories is covered by the four existing
evaluators.** That gap is the entire argument for doing error analysis before
choosing what to measure.

### Step 5: what to do about each

| Category | Decision | Reasoning |
|----------|----------|-----------|
| `quoted_total_for_empty_cart` | code-based check | Objective and cheap: compare any total in the reply against the cart tool's `subtotalCents` in the same turn. No judgement needed. |
| `explicit_add_not_executed` | prompt fix | The add policy is too cautious. Likely collapses `quoted_total_for_empty_cart` as well — if the add fires there is no phantom total to quote. Fix first, then re-measure. |
| `unverified_product_recommended` | LLM-as-judge | Needs judgement about whether a named product was actually returned by a search in that conversation. Highest-impact category that a code check cannot settle. |
| `readback_request_unanswered` | code-based check | Detectable: the shopper asked for a read-back and the reply contains no SKU or subtotal. |
| `data_declared_unavailable` | LLM-as-judge | Requires comparing a claim of ignorance against everything the tools returned earlier in the session. |
| `capability_overpromised` | prompt fix | Give the model its own tool inventory and forbid offering anything outside it. |
| `false_self_report` | monitor | One occurrence. Watch it as more passes are labelled before committing to an evaluator. |

Two clustering calls worth revisiting as more turns are labelled:
`false_self_report` and `data_declared_unavailable` are both self-knowledge
failures and a reasonable reviewer merges them — kept separate here because the
fixes differ, and because separate booleans can always be merged at analysis time
while a merged one cannot be split. Likewise
`quoted_total_for_empty_cart` may be purely downstream of
`explicit_add_not_executed`; if the prompt fix removes both, they were one
problem.

### What backtesting changed

Two of the seven categories became code evaluators in
`src/evaluators/deterministic.ts` — `quoted_total_for_empty_cart` and
`readback_request_unanswered`, numbers 5 and 6 on the board. Replaying them over
the same 33 turns that produced them is the cheap half of judge calibration:

```bash
npx tsx scripts/backtest-evaluators.ts --tag ea:pass-1
# compared 66   agreed 65   disagreed 1   → 98.5%
```

That single disagreement was the human label being wrong, not the evaluator.
`dropped-dietary-constraint` turn 7 was hand-labelled
`readback_request_unanswered = true`, but the reply says *"I notice I haven't
actually added anything to the cart yet"* — which **does** answer "read back the
final cart". The turn is still bad, for reasons
`quoted_total_for_empty_cart` and `explicit_add_not_executed` already cover. The
label is now corrected in Langfuse, with the reason in the score's comment, and
the category dropped from 6.1% to 3.0%.

This is the argument for writing the evaluator even when the rate looks too low
to bother: **the code forces a precision that prose labels do not have.** "Didn't
read the cart back" felt obviously true while writing the note, and turned out to
conflate two different things.

Two reconstruction traps hit on the way, both worth knowing before replaying any
evaluator off trace data:

- **A mutation span tells you what one call did, not what the state became.** The
  assistant issues `manage_cart` adds in parallel, so several spans report a
  mid-flight basket — and neither the last to *start* nor the last to *finish*
  holds the final cart. Reconstructing from mutations scored a correct $14.46
  quote against a phantom $9.97 and produced three false failures. Prefer a
  read: `list_offers` recomputes the subtotal, so its figure is the settled one.
- **`v3/scores` returns its continuation token as `meta.cursor`**, where other
  paginated endpoints use `meta.nextCursor`. Getting this wrong fails silently —
  you compare 28 labels instead of 66 and the agreement percentage still looks
  respectable.

### The dashboard

[Error Analysis - Round 1](https://us.cloud.langfuse.com/project/cmtu6y6ox01ciad0ckrnkzijz/dashboards/cmtue2wdv0187ad0dgtllpur8)
— 8 widgets, seeded over `/api/public/unstable/{dashboards,dashboard-widgets}`:
a horizontal bar of `avg(value)` per category (the failure rate), a Pass/Fail
pie, a pivot table pairing each rate with its denominator, and one NUMBER per
conversation counting Fail verdicts.

Three things about the widget API cost real time here and are not obvious:

| Trap | What happens |
|------|--------------|
| `view` must be one of `observations`, `scores-numeric`, `scores-boolean`, `scores-categorical` | There is no generic `scores` view, and the legacy `traces` view is rejected outright. Boolean category scores and the categorical Pass/Fail need *different* views, so they cannot share one widget. |
| `tags` groups by the **whole tag array**, not per tag | A "rate by conversation" chart splits each conversation into one row per tag combination — `conversation_end` on the final turn is enough to double it — and the denominator silently becomes *category labels* rather than *turns*, reporting 57% as 11.9%. Tags work correctly as a **filter**; use one widget per value. |
| `sessionId` and `userId` are cardinality-gated **by field name** | The metrics API demands `config.row_limit` plus a descending `orderBy`, and the widget body has no field for either — `chartConfig` silently discards unknown keys. The widget is created successfully and renders nothing. Validate every widget against the metrics API before trusting it. |

The pattern that does work for a per-group breakdown on low-cardinality
categorical values: keep the group in a **filter** and create one small `NUMBER`
widget per value, rather than reaching for a dimension.

### Extending to passes 2 and 3

Passes 2 and 3 (66 turns) are in the round-2 queue as `PENDING` and are not yet
coded, so every rate above has a denominator of 33. Labelling them is what
separates a consistent failure from a flaky one — the same scripted input run
three times, so any variance is the model's own. A category at 9% in all three
passes is an engineering problem; one that appears in a single pass is a
different conversation about temperature and retries.

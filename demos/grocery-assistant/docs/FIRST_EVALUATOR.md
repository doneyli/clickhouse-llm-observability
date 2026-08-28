# Which evaluator should I build first?

> The short answer for this demo: **a four-line check on the cart, with no LLM in
> it.** Why that one, and how to get to your own version of it, is below.

---

## The order of operations

The most common mistake is starting here — picking a metric. Langfuse is explicit
that metric selection is not the first step:

> "Most of the time, you start by **manually reviewing outputs** to build intuition
> for what good and bad look like in your application. From there, you **identify
> specific failure modes** worth checking for. Once you can define them precisely,
> you **automate with dedicated evaluators**."
> — [Langfuse Academy: Evaluation](https://langfuse.com/academy/evaluate)

And on skipping it:

> "**Teams that skip this step and jump straight to automated evaluation often end
> up measuring things that don't matter.**"

The structured version of that reading is **error analysis**, and Langfuse gives
the timing rule plainly: run it *"**Before designing evaluators** — so your traces
define what's worth measuring, not generic criteria like 'helpfulness.'"*

The five steps, condensed from
[the error-analysis cookbook](https://langfuse.com/guides/cookbook/error-analysis-llm-applications):

1. **Gather traces** — a representative sample of real traffic.
2. **Open coding** — read each one, write a free-text note on the first thing that
   went wrong. No categories yet.
3. **Cluster** — group the notes into named failure categories. An LLM can draft
   the taxonomy; you must review it, because "LLMs cluster by surface similarity
   and can produce groups that look plausible but conflate different root causes".
4. **Label and measure** — tag the sample, compute a rate per category.
5. **Decide** — prompt fix, evaluator, or monitor-for-now.

Practical numbers the docs give: bootstrap with just two scores — a free-text
`open_coding` note and a `pass_fail_assessment` — read **30 to 50 traces**, then
create **one boolean score per category**. Stop when *"no new category in the last
20 traces. Around 100 total works for most apps."*

And do it yourself. The listed mistake is *"Delegating trace review to an LLM |
You miss the muscle-building"*, with the fix: *"You review the first 30-50 traces
yourself, always."*

---

## The three filters

Once you have candidates, most of them should die. In order:

**1. Fix first.** *"If a simple prompt change resolves a failure mode, just make
the change and forget about it."* An evaluator is for a failure that keeps coming
back, not for one you can eliminate today. The docs' own split: wrong date format
or invalid JSON — just fix it. Whether the answer is supported by the retrieved
context — that generalises, so measure it.

**2. Tie it to a decision.** *"For each metric candidate, determine what the action
changes when the metric moves: block the deploy, roll back the prompt, open an
investigation. If no action changes, the metric would be noise, and should not be
tracked."* The example is worth repeating to a sceptical stakeholder: in OpenAI's
receipt-processing walkthrough, merchant-name extraction was wrong **85%** of the
time — and the team stopped tracking it, because the errors were uncorrelated with
the audit decision the system existed to make.

**3. Let the budget push back.** *"The fewer metrics you can keep without feeling
like you're missing visibility, the better … when everything is important, nothing
is."*

One exception to "derive metrics from observed failures": **guardrails**.
Compliance, safety, and format contracts *"get an evaluator from day one, even if
you have never seen them break."*

---

## Do not start with a generic metric

This is the single most useful thing to tell a team that is about to build a
scorecard:

> "Generic qualities like 'helpfulness' or 'quality' are tempting starting points,
> but they rarely produce useful signal. An evaluator that checks a vague criterion
> will give vague results."
> — [Academy: Evaluation](https://langfuse.com/academy/evaluate)

> "**write evaluators for errors you discover; don't focus on imaginary ones**."
> — [Academy: Choosing what to evaluate](https://langfuse.com/academy/evaluate/choosing-what-to-evaluate)

> "Evaluator libraries with ready-made metrics like hallucination, toxicity, or
> helpfulness measure abstract qualities that may not match how your application
> fails."

And the naming rule, which is the version that actually changes behaviour:

> "**Name after what broke.** `missing_device_lookup` beats `information_quality`.
> `identity_not_disclosed` beats `transparency`."
> — [Error-analysis cookbook](https://langfuse.com/guides/cookbook/error-analysis-llm-applications)

**Attribution, precisely.** The docs name `helpfulness`, `quality`,
`hallucination`, `toxicity`, `relevance`, `transparency`, and
`information_quality` as examples of the too-generic class. The Langfuse **agent
skill**'s `setting-up-evals` reference goes further and instructs: *"Do not propose
generic starting metrics such as `helpfulness`, `quality`, `relevance`,
`hallucination`, `groundedness`, `task completion`, `task success`, or
`reliability`."* Both are Langfuse-authored; the longer list is the skill's, not
the public docs'. Worth knowing which you are quoting, because `task completion`
is treated as a legitimate agent metric elsewhere in the docs.

---

## Code or judge?

The decision rule, verbatim:

> "If the thing you want to evaluate is — visible in your system (a row was
> written, a ticket was closed, an order was placed), or comparable against an
> expected output you saved ahead of time — a code evaluator can often settle the
> question exactly, and is faster and a lot cheaper to run. **Prefer these over
> LLM-as-a-judge evaluators where you can.**"
> — [Academy: Writing evaluators](https://langfuse.com/academy/evaluate/writing-evaluators)

|  | Code evaluator | LLM judge |
|---|---|---|
| Cost | cheap | expensive ($0.01–0.10 per assessment) |
| Speed | milliseconds | seconds to minutes |
| Consistency | same input, same verdict, always | verdicts vary between runs |
| Scope | structure, state, comparisons | meaning, relevance, tone |

The judge's job is the thing code cannot do: *"A code evaluator can check that an
output contains the word 'refund,' but it cannot check whether the output correctly
explains the refund policy."*

### The line that decides it

> "It's fully possible that an agent ends a support conversation with 'Your refund
> of $200 has been processed, you're all set!' while no refund exists. If your
> evaluators are going off of only a transcript, you might have a lot of false
> positives. Instead, a check on the refunds table would catch every case.
> Anthropic's guide to agent evals condenses this into a rule: **grade the outcome
> in the environment, not the claim in the transcript**."

For a shopping assistant, **the cart is the refunds table.** The assistant says it
added oat milk. The cart either contains oat milk, or it does not. No judge needed,
no rubric to calibrate, no drift — and it catches the failure a shopper notices
within seconds.

---

## What this demo actually builds, in order

See [`src/evaluators/deterministic.ts`](../src/evaluators/deterministic.ts) and
[`src/evaluators/judge.ts`](../src/evaluators/judge.ts).

| # | Score name | Type | The question | Why here in the order |
|---|---|---|---|---|
| 1 | `unverified-cart-claim` | code, boolean | Assistant said it added X — is X in the cart? | Outcome in the environment. Free, exact, highest-visibility failure. |
| 2 | `fabricated-purchase-history` | code, boolean | Every item presented as a past purchase is really in order history | The observed real-world failure: asked for "my usual list", the assistant invents plausible groceries. Fluent, and completely wrong. |
| 3 | `dropped-dietary-constraint` | code, boolean | A requirement stated once still holds N turns later | The cross-turn failure. Turn 6 alone looks fine; it is only wrong in light of turn 1. |
| 4 | `stale-discount-quoted` | code, boolean | The discount quoted matches the offers that currently apply | Small, specific, checkable — and the shopper otherwise finds out at checkout. |
| 5 | `unavailability-obscured` | **judge**, boolean | Was an out-of-stock item admitted plainly and a real substitute offered? | The first thing on this list that genuinely needs language understanding. |

Four deterministic checks before the first model call. That ratio is the point,
and it is unusual only because most teams start at row 5.

Note what none of them are called: not `groundedness`, not `helpfulness`, not
`cart_quality`. Each name says what broke.

---

## Anything running on live traffic must be reference-free

> "A reference-based evaluator compares the output against a predefined expected
> output … A reference-free evaluator assesses the output on its own. **The
> advantage of reference-free evaluators is that they can be applied to unseen
> production data, while reference-based evaluators always need a pre-defined
> reference response.**"

All five evaluators here are reference-free — they compare the answer against
*system state*, not against a saved expected output. That is what makes them safe
to run on production, where, as the docs put it, the structural limitation of
online evaluation is simply: **"No ground truth."**

Reference-based checks are not worse; they just live in the dataset experiment.

---

## Designing the judge, when you get to one

**One evaluator per failure mode.** *"It might be tempting to create a single judge
that rates accuracy, tone of voice, and completeness together on a 1-10 scale.
This is sometimes also called a God Evaluator. The problem with this is that your
resulting score does not tell you what to fix."*

**Boolean or categorical, not a scale.** A pass/fail verdict *"is easily
verifiable. You can count exactly how often the evaluator catches a failure and how
often it clears a pass … There is no equivalent test for whether a 7 was the right
score."* Plus a genuinely funny reason: *"LLMs add a quirk of their own on top,
favorite numbers: GPT-3.5 has a preference for the number 7."*

**Label 10–20 real cases before writing the prompt**, because of *criteria drift*:
*"you need criteria to grade outputs, but grading outputs is what teaches you your
criteria."*

**The five-part prompt**, which `judge.ts` follows literally:

1. Context — what the system is, and what it cannot do
2. One precise criterion, **including what to ignore**
3. Labeled examples with reasons (optional — start without them)
4. Reasoning first, verdict last (*"measurably improves judge accuracy"*)
5. An explicit way out — let it answer `unknown` rather than guess

The bar to aim for: *"a new colleague could read it and reach the same verdicts you
would."*

---

## Calibrate before you believe it

Run the judge prompt as an experiment against a dataset where **you** wrote the
labels, and measure agreement. Simple mode gives accuracy; advanced mode gives a
confusion matrix, and you want that *"when one class is much rarer than the other,
or before you trust the judge for high-stakes automation."*

The trap, stated well:

> "Suppose the failure you are checking for occurs in 10% of cases. A judge that
> answers _pass_ every time agrees with you 90% of the time, which you could
> interpret as a good judge, but actually tells you nothing. So check each class
> separately."

Realistic ceiling: *"strong LLM judges … achieve 80-90% agreement with human
evaluators on many quality dimensions, which is comparable to inter-annotator
agreement between humans."*

And it is not one-and-done — Goodhart's law is named explicitly: *"when you tune
prompts against certain metrics, you can overfit at some point. It's important to
re-validate against new human labels from time to time."* Also: *"Retire metrics
that stopped catching things … a score that sits at 100% for months carries no
information."*

---

## Controlling what judges cost

Three levers, from [the evals overview](https://langfuse.com/blog/2025-11-12-evals):
**sampling** (score 5% of matching observations), **targeting a specific
observation** rather than a whole trace so the judge reads less text, and **cheaper
judge models** for simpler criteria. Sampling and filters live on the *rule*, not
the evaluator.

The pattern this demo uses:

> "**Deterministic pre-checks with code evaluators, which are effectively free, can
> filter what reaches the judge at all.**"

`unavailabilityObscured` starts with a free check for whether any out-of-stock item
is even in play, and returns not-applicable without calling a model on most turns.

**Be honest about what this is, though.** It is a pattern you implement in
application code. Langfuse ships no wiring that lets one evaluator's score
conditionally gate another's rule — the shipped controls are rule filters and
sampling rate. Do not demo it as a platform feature.

---

## One more thing: not-applicable is not a pass

Every evaluator here returns `applicable: false` when the turn gave it nothing to
check, and [`src/scoring.ts`](../src/scoring.ts) then writes **no score at all**
rather than a pass.

This matters more than it sounds. If six of ten items score a free pass, the metric
reads a confident 100% and — the real damage — becomes **insensitive**: a
regression on those six cannot move a number that is already at its ceiling. A rate
needs a denominator that means something, which is why the experiment output here
always reports how many items each evaluator actually applied to.

---

## Sources

- [Academy: Evaluation](https://langfuse.com/academy/evaluate)
- [Academy: Choosing what to evaluate](https://langfuse.com/academy/evaluate/choosing-what-to-evaluate)
- [Academy: Writing evaluators](https://langfuse.com/academy/evaluate/writing-evaluators)
- [Academy: Error analysis](https://langfuse.com/academy/monitoring/error-analysis)
- [Cookbook: Error analysis for LLM applications](https://langfuse.com/guides/cookbook/error-analysis-llm-applications)
- [Evaluation core concepts](https://langfuse.com/docs/evaluation/core-concepts)
- [Code evaluators](https://langfuse.com/docs/evaluation/evaluation-methods/code-evaluators)
- [LLM-as-a-judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)
- [Annotation queues](https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues)
- [Calibrating an LLM-as-a-judge](https://langfuse.com/guides/llm-as-a-judge-calibration-skill)
- [Evals in Langfuse: an overview](https://langfuse.com/blog/2025-11-12-evals)

Checked against the Langfuse docs on 2026-08-28.

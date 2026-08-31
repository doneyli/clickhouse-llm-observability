# Support Triage Parallel — Demo Script (Parallelization on ClickHouse + Langfuse)

A ready-to-run demo of **Pattern #3 (parallelization)** in one trace: a support
ticket fans out into **4 concurrent analysis branches** (*sectioning*) and its
embedded data question is answered by **5 SQL samples majority-voted by
ClickHouse** (*voting*). Every branch is a sibling observation in **Langfuse**;
the vote tally is metadata; consensus is a score.

- **Fan-out engine:** raw Anthropic async API + `asyncio.gather` (`triage_pipeline.py`, `sql_voting.py`)
- **Vote counter:** the ClickHouse public playground (`sql.clickhouse.com`) — candidates are *executed*, not string-matched (`ch_validator.py`)
- **Observability backend:** Langfuse (`http://localhost:3001`), trace tag `support-triage-parallel`
- **Run length:** ~15–20 min full; Acts 1–3 = 8-min short path

> All code lives in `demos/support-triage-parallel/`. The trace shape and env
> knobs are in [`README.md`](README.md).

---

## How to run this script

It's a **conversation, not a walkthrough**. Every act does four things:

- **Frame** — the problem, in their terms (say this *before* you touch the screen).
- **Show** — the exact clicks / commands.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

The short path is Acts 1–3; add 4–7 when there's appetite. Don't rush the **Ask**.

---

## 0 · Pre-flight (do this BEFORE the meeting)

```bash
# CRITICAL: clear leaked Langfuse keys — exported shell vars override .env and 401 silently.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Langfuse (+ its ClickHouse) up; build the demo image.
docker compose --profile langfuse up -d
docker compose --profile demo build support-triage-parallel

# Deploy node + datasets + evaluators (idempotent):
python demos/support-triage-parallel/scripts/seed_all.py       # 7 prompts + 2 datasets
./scripts/seed-support-triage-evaluators.sh                    # managed judge: correlated-vote-risk
./scripts/seed-code-evaluators.sh                              # (re)seeds consensus-margin-guard

# Pre-run the TIE trace and PIN it (never hunt for a split vote live):
FAULT= docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-002
#   → confirm the step log shows "tie_break=True"; open that trace in Langfuse and pin the tab.
#   If needed later, filter Traces by score consensus_confidence < 1.

# Create the two Monitors (Langfuse → Monitors → New) — settings in Act 5.
```

**Browser tabs ready:** Langfuse Traces (`:3001`, `demo@example.com` / `demodemo1!`),
the pinned TCK-002 tie trace, a terminal, and Langfuse → Dashboards.

> **Tip:** for a snappier fault demo (Act 7) export `BRANCH_TIMEOUT_S=8` so the
> dropped branch resolves in ~8s instead of 30s.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| **Parallel = latency of the slowest branch** | Act 1 — `--sequential` vs default wall-clock |
| **Sibling branches under one parent** (typed obs, Timeline overlap) | Act 2 — Langfuse Timeline |
| **Guardrail-as-a-branch** (`policy_flagged` span score) | Act 2 — `branch-policy-guard` |
| **Vote tally as queryable metadata** (`votes`, `margin`, `tie_break_used`) | Act 3 — `tally-votes` |
| **Consensus as a score** (`consensus_confidence`) | Act 3 — trace score |
| **ClickHouse arbitrates semantic SQL equivalence** | Act 4 — `explain-candidate` tool spans |
| **N× cost, visible and bounded** | Act 5 — cost-per-trace Monitor |
| **Aggregation strategy is a testable variable** | Act 6 — Experiment (3 strategies) |
| **Graceful degradation on branch failure** | Act 7 — `FAULT=slow-branch` |

---

## Opening · Locate the pain (2 min, no screen)

**Frame.** When one LLM call isn't good enough, teams retry by hand or ship the
doubt. There are two cheaper answers: **split the task** so each consideration
gets a focused call, or **ask five times and count hands**. Both cost more —
the question is whether you can *see* that cost and whether the disagreement
becomes something you can act on.

**Ask (these steer the session):**
- "Where does a single model call's uncertainty actually hurt you today — a
  classification, a generated query, a moderation decision?"
- "When you're unsure a single answer is right, what do you do — retry, escalate,
  or just ship it?"
- "Do you fold everything into one big prompt, or split concerns? How do you know
  which is better?"

**Land.** "I'll show a triage app that does both — four focused branches in
parallel, then five SQL samples that ClickHouse itself votes on — and every bit
of it is a queryable number in Langfuse: the overlap in the timeline, the vote
tally, the consensus confidence, and the cost."

> **Coming from the text-to-sql demo?** Pivot line: *"There, one model wrote one
> query and we hoped. Here, five samples compete and the database decides the
> winner — and we get a confidence score for free."*

---

## Act 1 · Sequential vs parallel wall-clock (5 min)

**Frame.** The first assumption to test: does splitting a task actually buy you
anything, or is it just more calls? Two things: latency, and prompt quality.

**Show.** Run the same ticket sequentially, then in parallel:
```bash
docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-001 --sequential
docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-001
```
Read the step log line: `fan-out 4/4 branches (...) | Xs wall / Ys sum`. Sequential:
wall ≈ sum. Parallel: **wall ≈ the slowest single branch**, roughly ¼ of the sum.

**Land.** "Sectioning buys two things at once. **Latency** — the wall-clock
collapses to the slowest branch, not the sum. And **quality** — four narrow Haiku
prompts (summary, sentiment, category, a policy screen) each beat one omnibus
prompt that tries to do everything, because each call keeps its full attention on
one job. The guardrail is its own branch, so screening the ticket doesn't slow the
answer down."

**Ask.** "How many of your LLM steps are genuinely independent — could run at the
same time — versus truly sequential? And are you asking one prompt to do three
jobs anywhere?"

> **Fallback:** if the API is slow/rate-limited, the *ratio* (wall ≪ sum) still
> holds even if absolute numbers wobble; re-run once.

---

## Act 2 · The trace: siblings under one parent (6 min)

**Frame.** A timing line in a terminal is nice for you; it doesn't scale. Can you
*see* the parallelism across thousands of runs — which branches ran, how they
overlapped, what each decided?

**Show.** Langfuse → **Traces** → `triage-support-ticket` (the TCK-001 run) →
**Timeline**. Four `branch-*` observations start together and **overlap in time**
— the single most convincing "this is parallel" visual.

- Open `branch-policy-guard` → it's a typed **guardrail** observation with its own
  **`policy_flagged`** score (0 on a clean ticket).
- Note the branch names are low-cardinality (`branch-summary`, not
  `branch-summary-1`) — the run detail lives in metadata (`branch: "summary"`).
- Open `synthesize-triage-brief` → its **input** is the labeled branch outputs;
  the nested `synthesis-llm` generation links its `production` prompt.

Now open the **`--sequential`** run's trace: identical tree, but the branches form
a **staircase** — no overlap. Same code, one flag.

**Land.** "The typing is what makes Langfuse draw this as an agent graph instead
of a flat log, and the overlap is the proof that we're actually running
concurrently. The guardrail is a first-class branch with its own score — a policy
screen you can chart and alert on, that never slows the primary answer."

**Ask.** "If every parallel step were a labeled, timed, independently-scored span,
what would you look at first — the slow branch, the failed one, or the guardrail?"

> **Fallback:** empty Timeline = trace still ingesting (async worker); wait ~20s
> and refresh, or open a slightly older `triage-support-ticket` trace.

---

## Act 3 · The vote tally — the money shot (6 min)

**Frame.** Voting is the other half of parallelization: ask the same question five
times and count. The interesting part isn't the answer — it's the **disagreement**,
and whether you can turn it into a number you'd gate a decision on.

**Show.** Open the pinned **TCK-002** trace (the ambiguous "most active repos"
ticket) → `vote-sql` → note the **5 `vote-candidate` generations, all the same
name**, with `sample_index: 0..4` in metadata. Then open **`tally-votes`**:

- **metadata** is a literal tally: `votes {sig-…: 2, sig-…: 2, sig-…: 1}`,
  `invalid`, `winner`, `margin`, **`tie_break_used: true`**.
- **input** holds every candidate (SQL + validity + signature) — one place.
- The **`tie-break-judge`** child (Opus) is present *because the vote split*.
- The trace carries a **`consensus_confidence`** score (e.g. 0.4 — "2 of 5 valid
  samples agreed").

Contrast with the pinned **TCK-001** trace: a clean **5–0**, `consensus_confidence
= 1.0`, no judge child.

**Land.** "Disagreement between samples is now a queryable number in ClickHouse.
A 5–0 is trustworthy; a 2–2–1 tells you the *question* was ambiguous, not the
model — and the tie-break judge only fires when it needs to. You can filter every
run by confidence, chart it over time, and gate on it."

**Ask.** "What decision in your world would you gate on `consensus_confidence <
0.6` — auto-approve above it, route to a human below it?"

> **Fallback:** voting uses temperature 0.9, so the exact split varies. TCK-002 is
> *designed* to split; if a live run comes back clean, use your pre-pinned tie
> trace (that's why we pinned it in pre-flight).

---

## Act 4 · ClickHouse counts the votes (4 min)

**Frame.** How do you decide two SQL answers are "the same"? String comparison
can't: `GROUP BY 1` and `GROUP BY borough` are identical in meaning and different
as text. So don't compare strings — compare *results*.

**Show.** In the TCK-002 trace open **`validate-candidates`** → the **5
`explain-candidate` tool spans** (`EXPLAIN` against `sql.clickhouse.com`; invalid
ones are `level=WARNING`) and the **`sql_validity_rate`** span score. Explain the
default `result-signature` strategy: each valid candidate is *executed* and its
sorted result set is hashed — semantically-equal queries land on the **same
signature** and vote together.

**Land.** "The database is the vote counter. Two queries that compute the same
answer by different SQL vote together because ClickHouse ran them and the rows
matched — string-match voting can't see that. And it's the *same engine* that
stores these very traces: vectors, business data, observability, and now the vote
arbiter, all ClickHouse."

**Ask.** "Where do you compare 'are these two answers equivalent?' today — and are
you doing it on text when you should be doing it on results?"

> **Fallback:** if the playground is unreachable, candidates fail closed
> (`sql_validity_rate` drops); mention that safety posture and move on — the
> concept stands on the trace you already have.

---

## Act 5 · The cost of confidence (Monitor) (3 min)

**Frame.** Every one of these wins costs money — parallelization multiplies spend
~N×. The failure mode isn't the pattern; it's *not seeing* the bill until it
arrives. Someone bumps `VOTE_SAMPLES` to 10 and cost silently doubles.

**Show.** Langfuse → **Dashboards** → the "Parallelization economics" dashboard
(cost/trace, observation-count/trace, avg `consensus_confidence`, avg
`sql_validity_rate`). Then **Monitors**:

1. **`triage-cost-per-trace`** — dataSource `Traces`, filter
   `name = triage-support-ticket`, metric `sum totalCost`, threshold ≈ 1.5× your
   measured budget for 4 Haiku branches + 5 Sonnet samples + synthesis (+ occasional Opus).
2. **`triage-branch-failures`** — dataSource `Observations`, filter
   `level = WARNING` + the trace name, metric `count`.

Then prove it fires:
```bash
VOTE_SAMPLES=9 docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-001
```
Watch cost/trace jump. The Metrics API behind the Monitor:
```bash
curl -s -H "Authorization: Basic $(echo -n $LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY | base64)" -G \
  --data-urlencode 'query={"view":"observations",
    "metrics":[{"measure":"totalCost","aggregation":"sum"},{"measure":"count","aggregation":"count"}],
    "dimensions":[{"field":"traceName"}],
    "filters":[{"column":"traceName","operator":"=","value":"triage-support-ticket"}],
    "fromTimestamp":"2026-07-01T00:00:00Z","toTimestamp":"2026-08-01T00:00:00Z",
    "orderBy":[{"field":"sum_totalCost","direction":"desc"}]}' \
  http://localhost:3001/api/public/v2/metrics
```

**Land.** "N× cost is the pattern's tax. Here it's a number on a dashboard and an
alert threshold — not a surprise on the invoice. You decide the confidence you're
willing to pay for, and you can *see* when someone changes that decision."

**Ask.** "What's your ceiling — how much extra would you spend to move a decision
from 'one guess' to 'five-sample consensus'? And would you set that per use case?"

---

## Act 6 · Which aggregator wins? (Experiment) (4 min)

**Frame.** We picked `result-signature` voting. Is it actually better than the
alternatives, or just our taste? That's a testable question — hold everything
fixed, vary one component.

**Show.**
```bash
python demos/support-triage-parallel/scripts/run_experiment.py --strategy all
```
Langfuse → **Datasets** → `support-triage/sql-voting` → **Runs**: three runs
side-by-side (`aggregator-result-signature`, `aggregator-majority-exact`,
`aggregator-judge-consensus`), each with per-item `item_accuracy` and a run-level
**`voting_accuracy_rate`** + `mean_consensus_confidence`. Expect
`result-signature` ≥ `judge-consensus` > `majority-exact`.

**Land.** "The aggregation strategy is a variable, not a religion. Branch and
voter prompts are pinned at `production`; only the aggregator changes; a run-level
metric decides. `majority-exact` loses because string voting can't see that two
queries compute the same answer — exactly the point from Act 4, now measured. Add
`--ci` and it's a certification gate: below 0.8 accuracy, the build fails."

**Ask.** "When you change one component of an LLM system, how do you prove it
helped — vibes, or a run-level metric on a fixed dataset?"

> **Bonus (one flag):** `--vary-n-samples 1,3,5` charts the diminishing-returns
> asymptote — voting gains flatten fast.

---

## Act 7 · When a branch dies (fault injection) (3 min)

**Frame.** Parallel systems fail *partially*. One slow branch must not stall the
fan-out or corrupt the result — and you need to *see* that it happened.

**Show.**
```bash
BRANCH_TIMEOUT_S=8 FAULT=slow-branch \
  docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-005
```
Open the trace: `branch-sentiment-urgency` is `level=WARNING` (timed out),
`analyze-sections` and `synthesize-triage-brief` metadata both show
`failed_branches: 1` / `degraded: true`, the brief says **"sentiment: insufficient
data"**, and the trace is tagged **`fault:slow-branch`**. The
`triage-branch-failures` Monitor counts it.

**Land.** "The aggregator degrades gracefully — it proceeds with N-1 and *says so*
rather than inventing the missing dimension — and the trace proves it. Partial
failure is observable, not silent."

**Ask.** "When one step of a parallel pipeline fails for you today, does the
system notice — and does the final answer admit what's missing?"

---

## Closing · Why this matters (1 min)

**Land three takeaways:**
1. **Parallel = the latency of the slowest branch**, visible as overlap in the
   Timeline — and narrower prompts to boot.
2. **Voting turns model variance into a confidence *score*** with ClickHouse as
   the arbiter — semantic equivalence decided by executing SQL, not matching strings.
3. **The whole thing is governed** — cost monitored, aggregator experimented,
   consensus evaluated (deterministic *and* independent judge), prompts deployed
   from Langfuse by label.

Then hand them the asset: "This repo is public and self-contained — the fan-out,
the voter, the validator, the seeds. Clone it, point it at your own tickets and
datasets, and this is your prototype."

---

## Under the hood — how it's instrumented (for the "show me the code" moment)

All in `demos/support-triage-parallel/`.

**1 · Concurrent siblings via `asyncio.gather` inside a parent span — `triage_pipeline.py`**
```python
with lf.observe("analyze-sections", metadata={"branch_count": 4, "mode": "parallel"}) as parent:
    results = await asyncio.gather(*(run_branch(*b, ticket) for b in BRANCHES))
```
*Why it matters:* opening the branches inside the parent's context makes OTel
propagate the parent into each asyncio task — the branches auto-nest as siblings
with overlapping Timeline spans. No manual span-passing.

**2 · Low-cardinality names, index in metadata — `sql_voting.py`**
```python
with lf.observe("vote-candidate", as_type="generation", input=question,
                metadata={"sample_index": index}, **gen_kwargs) as gen:  # same name on all N
```

**3 · The tally lives on the aggregator — `sql_voting.py` (`tally_votes`)**
```python
with lf.observe("tally-votes",
        input={"candidates": [...]},            # every sample, one place
        metadata={"strategy": strategy}) as agg:
    agg.update(metadata={"votes": tally["votes"], "invalid": ..., "winner": ...,
                         "margin": ..., "tie_break_used": ...})
    lf.score_current_trace("consensus_confidence", confidence)
```
*Why it matters:* branch outputs on the aggregator's **input** are what let an
observation-level evaluator (the `correlated-vote-risk` judge) score the whole
vote — it cannot auto-pull the N child observations.

**4 · ClickHouse is the vote counter — `sql_voting.py` / `ch_validator.py`**
```python
def _result_signature(sql):                       # execute + hash sorted rows
    rows = ch_validator.execute_readonly(sql)
    return "sig-" + hashlib.sha1(repr(sorted(...)).encode()).hexdigest()[:8]
```

**5 · Graceful degradation — `triage_pipeline.py` (`run_branch`)**
```python
except (asyncio.TimeoutError, Exception) as e:
    obs.update(level="WARNING", status_message=f"branch dropped: {e}")
    return {"branch": name, "ok": False, "output": None}
```

**6 · No-op without keys — `langfuse_config.py`**
```python
def observe(name, as_type="span", ...):
    if get_langfuse_client() is None:
        yield _NullObs(); return           # obs.update(...) is always safe
```

> One-liner for the room: *"Each branch is a typed `with`-block gathered
> concurrently; the tally is one `update`; consensus is one score; and it all
> no-ops without Langfuse keys."*

---

## Talking points & objections

- **"Isn't N× cost a dealbreaker?"** Only if you can't see it. Cost is per-observation
  in Langfuse, summed per trace, alertable via a Monitor — you buy exactly the
  confidence you decide is worth it, per use case.
- **"Voting can be confidently wrong."** Correct — correlated errors defeat majority
  vote. That's why the independent `correlated-vote-risk` judge reads all samples
  and flags a suspicious consensus, and why `consensus_margin_ok` is a separate
  deterministic check. "Who checks the vote-counter" is a first-class score.
- **"Why not string-match the SQL?"** `GROUP BY 1` ≡ `GROUP BY borough`. Executing +
  hashing results catches semantic equivalence string voting misses — the
  `result-signature` vs `majority-exact` experiment measures the gap.
- **"Framework lock-in?"** None — raw Anthropic async API + the Langfuse SDK. The
  same fan-out pattern applies to any framework or plain code.
- **"Determinism / auditability?"** Voting relies on temperature > 0, so it's less
  reproducible than a single call by design — but every sample, its validity, and
  the tally are persisted, so the *decision* is fully auditable after the fact.

---

## Reset / re-run

```bash
# Re-run the batch, a single ticket, or interactively
docker compose --profile demo run --rm support-triage-parallel python main.py
docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-002
docker compose --profile demo run --rm support-triage-parallel python main.py --interactive

# Re-seed (idempotent)
python demos/support-triage-parallel/scripts/seed_all.py
./scripts/seed-support-triage-evaluators.sh

# Try a different aggregation strategy live
VOTE_STRATEGY=majority-exact docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-002

# Pure-code tests (no services)
cd demos/support-triage-parallel && python -m pytest
```

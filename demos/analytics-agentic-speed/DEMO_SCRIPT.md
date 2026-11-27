# Analytics at Agentic Speed — Demo Script

A ready-to-run demo of an analytics agent that answers what-if questions by **sweeping
hundreds of scenarios** against ClickHouse Cloud, fully traced to **Langfuse**, with
the database's own latency on every query span.

- **Data:** US domestic flights, 231.6M rows (1987 → 2026), table `flights.ontime` on a
  ClickHouse Cloud service, sorted by `(Origin, Year, Month, Reporting_Airline, FlightDate)`
- **Agent:** `explorer/agent.py`. Claude Opus 5.5 writes ONE SQL template and a grid of
  lever values; the code runs every scenario, 32 in flight; the agent reads the results
  and decides whether to refine
- **Observability:** Langfuse Cloud project `analytics-agentic-speed`: trace `analytics-agentic-speed`,
  dashboard "Analytics at Agentic Speed", dataset `scenario-explorer-questions`
- **Run length:** ~15 min; 8-min short path is Acts 1–2 + the Experiment A compare view

> Everything lives in `demos/analytics-agentic-speed/`. Measured numbers in this script come
> from runs on a 2-replica, 16 GiB-per-replica Cloud service in AWS us-west-2, with the
> agent running on a laptop over the public internet. Re-measure on your own setup
> before quoting them.

---

## How to run this script

It's a conversation, not a walkthrough. Every act opens with the problem in the room's
terms (say it before you touch the screen), shows how the platform answers it, lands
what that's worth, then hands a question back to the room. The questions matter more
than the clicks: they tell you which act to spend time on.

### The day before

Act 4 opens two Experiment A runs that must already exist in the project. If you're on
a fresh project, or want your own numbers:

```bash
.venv/bin/python scripts/run_experiment.py --breadth both --concurrency 2   # ~20 min, ~$4
```

### Pre-flight (10 minutes before)

```bash
cd demos/analytics-agentic-speed
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
.venv/bin/python scripts/warmup.py --runs 4        # wakes both replicas' caches
.venv/bin/python main.py --question q-padding      # one fresh hero trace to open live
./run_ui.sh                                        # the live view, http://localhost:8099
.venv/bin/python scripts/verify_demo.py --links    # READY or not, plus the link for every act
```

`verify_demo.py` reads everything back: the agent user is read-only, the table holds
231.6M rows, a hero-shaped scenario runs warm, the keys map to this project, both prompts
have a `production` version, the dataset has 15 graded items, the dashboard has all 20
widgets, and the latest hero trace carries its scores. It exits 1 on a required failure.
The optional acts (Experiment A runs, the concurrency act, the live view) only warn.
Measured numbers for every act, with the command that produced each, are in
[docs/MEASUREMENTS.md](docs/MEASUREMENTS.md).

**Live view or terminal?** For most rooms, run Act 2 from the live view: the round
heatmaps fill in as each query returns, and the time-split bar makes the punchline
without narration. The terminal (`main.py`) prints the same run as text lines. Both
produce the same Langfuse trace.

- **The IP access list.** The Cloud service accepts only listed addresses. Add the
  venue's address before you start; a TLS error on connect (curl exit 35) means it's missing.
- **Cold vs warm is real.** The first query on a cold replica runs 150–400 ms; warm, the
  same scenario runs in 10–40 ms. The warm-up exists so the audience sees the steady
  state. If someone asks, say so: it's caching, not a trick.
- Open three tabs: the fresh trace, the dashboard, and the dataset's experiment runs.

---

## Act 1 — Agents don't ask one question (2 min)

**Frame.** "When people picture an agent on top of a database, they picture text-to-SQL:
one question, one query. That's not how a good analyst works, and it isn't how a good
agent works either. It explores: it tries a hypothesis, compares, narrows down, checks
again. Every one of those steps is a query. So how fast the database answers sets how
far the agent can look before someone gets bored."

**Show.** Nothing yet. Read the hero question aloud:

> *In 2019, for evening departures at the 10 busiest airports, how many minutes of
> schedule padding would lift on-time arrival above 85%, and where does it take the
> fewest minutes?*

"An analyst would try a few airports and a few buffer values. The answer here hinges on
a single minute, so a 5-minute grid gets it wrong."

**Land.** "The question isn't 'can the database answer a query'. It's 'can the agent
afford to ask six hundred of them'."

**Ask.** "In your team, what decision gets made on three or four scenarios today because
running fifty would take a week?"

---

## Act 2 — One run, six hundred queries (5 min)

**Frame.** "Let's watch the agent do it, then look at where the time went."

**Show.**

```bash
.venv/bin/python main.py --question q-padding
```

Narrate the live lines. Measured on the reference run:

| Step | What happens | Time |
|---|---|---|
| profile | cached facts about the table (one `count()` to validate the cache) | 0.3 s |
| plan | the model looks up the 10 busiest 2019 airports, then writes one template + a 10 airports × 30 buffers grid | ~20 s |
| round 1 | **300 scenarios in 1.37 s wall**, server p50 14 ms | 1.4 s |
| analyse | "the top rows only show the highest buffers; I can't see where each airport crosses 85%" → refine | ~25 s |
| round 2 | **300 scenarios in 1.36 s wall**, server p50 18 ms | 1.4 s |
| analyse + verify | answer, then a deterministic re-run of the winning scenario | ~12 s |

Answer: **LAX, 9 minutes** (85.07%; 8 minutes gives 84.6%), then ATL at 10. That
matches the reference SQL exactly.

Then open the trace in Langfuse:

1. **The timeline.** Two `sweep` spans, each a dense comb of 300 `clickhouse.query`
   spans, sitting between `plan` and `analyse` generations that are 10–25 s each.
   "The slivers are the database. The long bars are the model thinking."
2. **Click one `clickhouse.query` span.** Input is the rendered SQL and its parameters
   (`origin=LAX`, `buffer=9`). Metadata: `db_server_ms` (ClickHouse's own number, from the
   response's `statistics.elapsed`), `db_roundtrip_ms`, `db_network_ms`, `db_read_rows`
   (the rows ClickHouse actually scanned: roughly one airport's year, thanks to the
   sort key), `db_query_id`. The span's `support` output is the flights the metric
   covers (68,453 completed evening departures for LAX). The same number
   sits on the span as a `db_server_ms` score.
3. **The trace scores.** `scenarios_evaluated` 600 · `llm_calls` 4 · `db_wait_ms_total`
   3.3 s · **`db_time_share` 0.053** · `recommendation_grounded` true · `run_cost_usd` $0.14.

**Land.** "Six hundred queries, and the agent spent **5% of its time waiting on the
database**. Most of that is network between this laptop and Oregon: each query is
10–20 ms of ClickHouse work inside a ~100 ms round trip. The other 95% is the model.
The database has dropped out of the latency budget."

**Ask.** "Where does your agent's time go today? Do you know, per tool call?"

> **If someone asks "couldn't one GROUP BY do this?"** Yes, for this question, and the
> agent will do it if you let it: our first version wrote one query per airport that
> searched all 61 buffer values inside the SQL. That's fast and clever, and it hides the
> search. Real what-ifs (a policy simulated over several steps, a scenario that changes
> a join or a cohort definition) often don't collapse into one aggregate. So the prompt
> asks the template to *measure* one scenario and leave the *search* to the system, so
> each scenario is auditable on its own. That's a design choice; say so.

---

## Act 3 — The agent left alone (3 min)

**Frame.** "Nobody told it to run a second round."

**Show.** In the trace, open the first `analyse` generation. Its output says, in effect:
*"Round 1 ranked by on-time share, so the top rows are all high buffers; I can't see
where each airport first crosses 85%. Re-run with a metric that marks the crossing."*
Then show the second `round`'s `compile` span: a new template and a new grid.

Point at the LLM calls: **4**. "The second round cost one more model call and 1.4
seconds of database time. That's the trade: the expensive thing, the model, runs once
per round. The cheap thing, ClickHouse, runs once per scenario."

**Land.** "An agent that can afford to check its own work does. This one second-guessed
its metric because re-running 300 scenarios cost it a second."

**Ask.** "Which of your workflows would you trust more if the agent could re-check every
assumption for a second of compute?"

---

## Act 4 — More experiments per hour (4 min)

**Frame.** "Same agent, same prompt, same model, and the same two rounds of thinking.
The only knob: how many scenarios it may run per round, 10 or 300. If database time
were expensive, you'd keep that knob low."

**Show.** Langfuse → Datasets → `scenario-explorer-questions` → the two Experiment A runs
(`breadth-10-20261126T232112Z` and `breadth-300-20261126T232112Z`) → compare. Fifteen
questions, each with an exact answer computed by a reference SQL query, so grading is
deterministic: no judge.

Measured on the reference run (Claude Opus 5.5, both breadths held to 2 rounds):

| | 10 scenarios / round | 300 scenarios / round |
|---|---|---|
| **Accuracy** | **40% (6/15)** | **80% (12/15)** |
| Scenarios per question | 19 | 419 |
| Model calls per question | 4.1 | 3.6 |
| Wall time per question | 55 s | 66 s |
| Cost per question | $0.118 | $0.144 |
| Time waiting on ClickHouse | 2.8% | 6.7% |

Then click into the hero row, `q-padding`. The narrow run answered **LAX, 10 minutes**,
one minute off: ten scenarios per round means a coarse grid, and a coarse grid lands on
the wrong side of the threshold. The wide run tested every whole minute and found
**LAX, 9**.

**Land.** "Twenty-two times the database work bought twice the accuracy, for about 11
more seconds and under three cents a question. Model calls didn't go up; they went
down. Breadth is a knob you can afford to turn up, because the extra work lands on the
database, and the database does it in milliseconds."

**Ask.** "What's the knob in your evals you keep low because turning it up is slow?"

> **The misses are part of the story.** The wide run still got 3 of 15 wrong. Two picked
> the runner-up (weather share: DTW instead of ATL; late-aircraft share: LAX's 22:00
> block instead of LGA's 20:00), and one was off by a single minute (LGA/OO at 40 instead
> of 41). Open one in the compare view: the item's trace shows exactly which scenarios
> it ran and which it never tried. Breadth raises accuracy; it doesn't make a judgment
> call for you.
>
> **If someone asks whether the comparison is rigged:** it's constrained, on purpose, and
> you should say how. Both breadths get the same two rounds, because a round is a model
> call (10–25 s) and a scenario is a query (milliseconds). The agent's fact-finding
> lookups may list candidates but not measure outcomes, and templates may not search
> inside the SQL; both rules are enforced in code. The first, unconstrained run scored
> 93% vs 93%. The 10-scenario agent got there by ranking the whole space in one lookup
> query and by bisecting over extra rounds: still ClickHouse doing the search, through a
> side door. The constraints make the experiment measure what it claims to.

> **Two caveats on the runs above, both fixed since.** First, four questions
> (`q-padding`, `q-late-departure-block`, `q-padding-carrier`, `q-connection-window`)
> named "the busiest airports of 2019" but didn't say to *measure* 2019, and on all four
> both breadths measured every year. Both sides had the same handicap, so the comparison
> holds, but the LGA/OO miss (40 vs 41) is most likely this, not breadth. The texts now
> open with the year ("In 2019, …"), and a test keeps it that way. Second, those traces
> predate the span inputs, so `profile`, `round` and `sweep` show "undefined" in
> Langfuse. Rerun Experiment A the day before (see "The day before") and quote your own
> numbers.

**Land.** "Breadth is a knob you can afford to turn up. Accuracy moves; the bill and the
model's time barely do, because the extra work lands on the database, and the database
does it in milliseconds."

**Ask.** "What's the knob in your evals you keep low because turning it up is slow?"

---

## Act 5 — Land (1 min)

"Two last things. Every trace you've looked at is stored in ClickHouse too: Langfuse runs
on it. And the dashboard (open it) is just these scores, charted: DB share per run, query
latency percentiles, scenarios per minute, LLM calls against scenarios. The agent's
environment and its observability backend are the same database."

---

## Optional — Many agents, one service (2 min)

**Frame.** "One agent is a demo. A team running ten of these at once, against one
service, is a platform question."

**Show.**

```bash
.venv/bin/python scripts/run_concurrency.py --levels 1,5,10 --rounds 2 --concurrency 8 --yes
```

Measured on the reference service (2 replicas × 16 GiB, each agent on a different
question, 8 queries in flight per agent):

| Agents at once | Queries | Server p50 | p95 | p99 | Failed queries |
|---|---|---|---|---|---|
| 1 | 603 | 10.0 ms | 18.6 ms | 23.7 ms | 0 |
| 5 | 1,991 | 8.7 ms | 124.7 ms | 223.9 ms | 0 |
| 10 | 3,755 | 8.7 ms | 70.1 ms | 162.5 ms | 0 |

In Langfuse: Sessions → `concurrency-agents-010-…` groups the ten traces, and the
dashboard's "Concurrency" widget charts the same percentiles per level.

**Land.** "Ten agents, almost four thousand queries, nothing failed, and the median query
didn't move. The tail grows when sweeps overlap; that's where you size the service, and
the trace tells you exactly when you hit it."

**Ask.** "How many agents do you expect to point at one dataset a year from now?"

> **Be honest about the tail.** p95 rises from ~19 ms to 70–125 ms between 1 and 5–10
> agents; the shape depends on how many sweeps overlap in the same second. Measured at
> 32 queries in flight from one agent, this service saturates at about 250 scenario
> queries per second (server p50 rises from 10 to 36 ms at 64 in flight). Quote the
> curve, not one number.
>
> In the reference run, 9 of the 10 agents answered. The tenth failed before running any
> scenarios: the model refused its planning call (a classifier false positive on a table
> of delay statistics). Planner and analyst calls now retry once on a refusal and record
> its category on the generation.

---

## Optional — A cheaper prompt meets the gate (3 min)

**Frame.** "Someone on the team says the agent sweeps too finely: 'five-minute steps are
plenty, and it'll cost less.' That's a plausible change, it'll pass code review, and it
quietly breaks the answer. How would you know before it ships?"

**Show.** The candidate is already in Langfuse under the label `candidate-coarse`: the
same planner and analyst with a "5-minute grid" policy. `production` is untouched.

```bash
.venv/bin/python scripts/seed_candidate_prompt.py --show                            # the diff vs production
.venv/bin/python scripts/prompt_gate.py --prompt-label candidate-coarse --limit 3   # BLOCKED, exit 1
.venv/bin/python scripts/prompt_gate.py --prompt-label production --limit 3         # passes
```

Measured 2026-11-27 with `--limit 3` (about a minute and $0.27 each, 3 questions in parallel):

| Label | Correct | Hero (`q-padding`) | Cost / question | Gate |
|---|---|---|---|---|
| `production` v1 | 3/3 | LAX, 9 ✓ | $0.095 | pass |
| `candidate-coarse` v3 | 2/3 | LAX, 10 ✗ | $0.088 | **blocked** (hero + accuracy) |

Open the gate's dataset run in Langfuse (experiment "Prompt gate", trace version
`gate-candidate-coarse`). The hero item's `round` span shows the grid it chose:
13 buffers, 0 to 60 in 5-minute steps, so it never tested 9. In CI the same gate runs on every prompt label change:
Actions → "Analytics at Agentic Speed prompt gate" (setup in [cicd/README.md](cicd/README.md)).

**Land.** "It saved under a cent a question and got the headline answer wrong by a minute.
The gate caught it because the dataset has exact answers and ClickHouse can afford to
check them every time a prompt changes."

**Ask.** "What would your gate check: accuracy, cost, or both?"

> **Know how it can fail.** A softer version of this candidate ("10-minute steps are
> precise enough") did *not* get blocked: the model read the question, saw that it asks
> for whole minutes, and swept 1-minute steps anyway. The seeded v3 is a firm policy.
> And `--limit 3` is a demo sample: the CI gate runs all 15 questions (~6 min at 3 in
> parallel, ~$2).

---

## Honest limits (know these before you present)

- **The network shows up.** From a laptop, round-trip time is ~100 ms against 10–20 ms of
  server time. Showing `db_server_ms` next to the span duration is the point; don't
  hide it. Run the agent in the service's region for the cleanest numbers.
- **The sort key decides the speed, and the trace shows it.** The table is sorted by
  `(Origin, Year, Month, …)`. The hero's scenarios filter on one airport, so each reads
  about 230K rows in 10–40 ms. `q-carrier-month` filters by carrier and month but not
  airport, so each of its 240 scenarios scans all of 2019 (~7M rows) at ~450 ms, and
  ClickHouse is 24% of that run. Both are fast for what they do; one is shaped for the
  table and one isn't. It's a good beat for an SA audience: open the two traces side by side.
- **"Padding" is a simple model.** A flight counts as on time if `ArrDelay <= 15 + buffer`.
  The demo is about exploration speed, not airline operations research.
- **The playground can't run sweeps.** `EXPLORER_TARGET=playground` exists for rehearsing
  without a Cloud service, but the public playground's `demo` user allows 20 runs per query
  shape per hour. A sweep hits that after about 20 scenarios.
- **Data gaps.** The public BTS bucket has no usable files for 2025-08 through 2026-02;
  those months are absent. No dataset question uses them.
- **Model time dominates the wall clock.** A run takes about a minute, mostly in two to
  four model calls. That's the point of the demo, but it's a long minute on stage:
  narrate the live lines.

---

## Show me the code

| What | Where | Why it matters |
|---|---|---|
| One query = one tool span with the DB's own timing | `explorer/agent.py:109` (`traced_query`) | Server ms, round trip, rows read and query id land on every span |
| The per-query score | `explorer/agent.py:140` | Langfuse's Metrics API can't chart metadata; a score makes `db_server_ms` chartable |
| Server time from ClickHouse | `explorer/ch_client.py:214` | `statistics.elapsed` from the response body: the database's number, not the client's |
| Typed server-side parameters | `explorer/ch_client.py:198` | `{origin:String}` + `param_origin=…`: no string formatting of values into SQL |
| No query cache | `explorer/config.py:76` | `use_query_cache=0`: every scenario is computed |
| The sweep | `explorer/agent.py:486` | asyncio tasks inherit the current span, so 300 queries nest under one `sweep` |
| Measure, don't search | `explorer/prompts.py:33` | Keeps each scenario a separate, auditable query |
| Grid compiler | `explorer/scenarios.py:116`, `:152` | Deterministic: the LLM writes one template, code expands the grid |
| Verify the answer | `explorer/agent.py:556` | Re-runs the winning scenario; `recommendation_grounded` |
| No duplicate LLM spans | `explorer/tracing.py:21` | Drops the Anthropic SDK's native OTel spans that double-count cost |
| A clean trace per UI run | `webapp/server.py` (`go()`) | FastAPI emits its own request spans; each run starts in an empty OTel context so its root has no dangling parent |
| Sort key | `sql/schema.sql` | `(Origin, Year, …)`: one airport's scenario reads 477 of 28,281 granules |

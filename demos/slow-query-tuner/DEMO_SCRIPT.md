# Slow Query Tuner — Demo Script (Autonomous Agent Loop on ClickHouse)

A ready-to-run demo of an **open-ended plan-act-observe agent** that optimizes a slow query
against a **live ClickHouse**, deciding *for itself* when it is fast enough, fully observable in
**Langfuse**, with caps, a kill switch and a runaway-cost Monitor as the rails.

- **App:** a CLI in a terminal (`main.py` in the `slow-query-tuner` container). No chat surface,
  by design: an unbounded loop with a spend cap doesn't belong behind a chat box
- **Observability:** Langfuse self-hosted (`http://localhost:3001`), project
  `LLM Observability Demo`, trace `tune-clickhouse-query`, tag `slow-query-tuner`
- **Models:** the planner is `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`) on the raw Anthropic
  tool-use API (`agent_loop.py`); the `goal_drift` judge uses the project's default eval model,
  which the seeder sets to `LANGFUSE_EVAL_MODEL` (default `claude-haiku-4-5-20251001`) when none exists
- **Data:** a disposable **ClickHouse 26.3** container (`clickhouse-tuning`) with a deliberately
  pessimal 30M-row table; the bad-query catalog is `queries.py`
- **Prompts:** `query-tuner-system` (`v1-naive`, `v2-disciplined` = `production`) and
  `query-tuner-goal`
- **Run length:** ~25 min full (Acts 1–5 plus the bonus Act 6); 12-min short path is the Opening,
  Acts 1 + 2 + 4 and the Close

> Setup lives in [README.md](README.md). The loop, termination check, HITL gate and compaction
> are in `agent_loop.py`.

---

## How to run this script

It's a **conversation, not a walkthrough**. Every act **frames** a problem the room already has,
**shows** how the platform answers it, **lands** the benefit, then hands a **question** back to
the room:

- **Frame** — the problem, in their terms (say it *before* you touch the screen).
- **Show** — the exact commands / clicks.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

The short path is Acts 1 + 2 + 4 (the variable loop + the runaway/Monitor beat). Add 3, 5, 6
when there's appetite, and let the answers to each Ask decide which. Acts 3 and 5 are part of
the full run; Act 6 is the bonus. Every act ends with a
**Fallback** note: ingestion is async and the loop is non-deterministic, so have the fallback ready.

---

## 0 · Pre-flight

From the repo root, before the meeting:

```bash
# Clear leaked Langfuse keys: shell exports override .env and send traces to another project.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL

# Langfuse (+ its ClickHouse) and the disposable tuning lab (seeds 30M rows on first start).
docker compose --profile langfuse up -d
docker compose --profile demo up -d clickhouse-tuning
docker compose --profile demo build slow-query-tuner

# Prompts (v1-naive + v2-disciplined/production), the root-level dataset, and the Monitor fields.
python demos/slow-query-tuner/scripts/seed_all.py
./scripts/seed-code-evaluators.sh            # includes runaway-loop-guard.ts
./scripts/seed-query-tuner-evaluators.sh      # the managed goal_drift judge

# Clear stale kill sentinels and any DDL a previous rehearsal approved.
python demos/slow-query-tuner/scripts/reset_demo.py

# Warm ingestion: one throwaway q1 run now (capped at $0.75).
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1

# READY, plus the link for every act: lab row count and shape, prompts, dataset, the q1 run.
python3 demos/slow-query-tuner/scripts/verify_demo.py --links
```

`verify_demo.py` exits 1 when a required check fails. Fix what it names before the meeting.

### The day before

Run Act 6's experiment once so the Runs tab is populated:
`docker compose --profile demo run --rm slow-query-tuner python scripts/run_experiment.py --sample 1`
(real spend, capped at $1.00 per item and arm).

**Monitors caveat (read this).** Monitors are a Langfuse **v4+** feature. This stack pins
Langfuse **v3.221.1**, which has no Monitors at all, so on this stack Act 4 always runs on its
**Fallback** (runaway cost vs a normal run, side by side), which still lands the point.
`verify_demo.py` checks what the Fallback needs (`run_cost_usd` and `cap_terminated` on a run).
The live "alert fires on stage" beat needs a v4+ Langfuse: there, create the two Monitors in
**Monitors → New Monitor** from the field values `seed_all.py` prints, before the meeting.
`verify_demo.py` can only warn on v4+, because no documented API reads Monitors. Don't upgrade
the stack for this demo; that is a separate change.

**Browser tabs ready:** Langfuse Traces (`:3001`, `demo@example.com` / `demodemo1!`), the q1
trace from `--links` (Agent Graph → Expanded), the Sessions view, and (v4+ only) Monitors.

---

## What each act proves

| Capability | Where in the demo |
|---|---|
| An agent decides its own path and its own stopping point | Act 1 |
| Ground truth from the environment, and a finish claim the controller re-checks | Act 1 |
| Variable-length loops read as graphs: Expanded per run, Aggregated across runs | Act 2 |
| Step-level scores (`semantics_preserved`) and context compaction | Act 2 |
| Pause/resume as one Langfuse session across two processes | Act 3 |
| Spend cap as a backstop, and a Monitor watching cost across the fleet | Act 4 |
| Human approval for irreversible actions; distinct, scored termination reasons | Act 5 |
| Outcome-graded prompt experiment with caps and tools pinned; deploy by label | Act 6 |

---

## Opening · Workflows vs agents (1 min)

**Frame.** "Yesterday's demos were workflows: the code knew the path. Today the code does
**not** know the path. I'm going to hand an agent a slow query and a target, and it will decide
how many steps it takes and when it's done."

**Ask.** "How many of your agents today decide their own number of steps? What stops one that
doesn't stop?"

**Land.** "The question you're all already asking is *how do you let an agent decide when it's
finished, and still sleep at night?* Three rails: verified self-termination, caps as a
backstop, and a Monitor watching the fleet. Let's watch."

---

## Act 1 · The agent does an SA's job (short run) (3 min)

**Frame.** "This is the job you'd hand a junior SA: here's a slow query, make it 5x faster,
don't change what it returns. Nobody tells the agent *how*."

**Show.**

```bash
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1
```

Narrate the live turns: `plan → explain-query` (full scan, String date parsed per row) →
`plan → run-query` (measured before and after, equivalence ok) → `plan → finish`. Call out the
last line: **the controller re-ran the final SQL itself and confirmed the speedup** before
accepting.

**Land.** "Nobody told it three turns. The ground truth is the real ClickHouse timing: it
*cannot* claim a speedup it didn't measure, and I re-check the claim independently. That's the
difference between an agent that says it's done and one that *is* done."

**Ask.** "What's your equivalent open-ended task, the one where the steps genuinely aren't
knowable up front?"

> **Fallback:** if q1 self-terminates in 2 turns, great, that's the point (the agent found it was
> fast enough after one rewrite). If a turn hits an env hiccup, note it surfaced as a `WARNING`
> observation and the loop continued.

---

## Act 2 · Same code, different shape (long run + the graph) (4 min)

**Frame.** "Same code, harder query. Watch the shape of the work change."

**Show.**

```bash
docker compose --profile demo run --rm slow-query-tuner python main.py --query q2
```

While it runs, open the q1 trace in Langfuse → **Agent Graph → Expanded**. When q2 lands, put
them **side by side**: a short chain vs a long one, *same trace name*. Then flip q2 to
**Aggregated**: the loop collapses to `plan-next-action → {tool} → assess-progress ↺` with repeat
counts. Point at the `semantics_preserved` span score going **red then green** on the turn the
DB rejected a candidate, and open a late `plan-next-action` **input** to show the **compacted**
turn summaries (`turn 3: run_query -> <ms> ms, equiv ok`).

**Land.** "Variable-length loops are unreadable as logs and legible as graphs: Expanded to debug
one run, Aggregated to answer *what does this agent do overall* when the step count is unbounded.
And it's all on ClickHouse."

**Ask.** "When your agents misbehave today, are you reading logs, or can you see the shape of
what they did?"

> **Fallback:** q2's turn count varies run to run. **Say so out loud**; that variability *is* the
> pattern. If it self-terminates fast, re-run; if it's slow, narrate the plateau while you talk.

---

## Act 3 · Pause/resume (the sessions beat) (3 min)

**Frame.** "Unbounded loops crash, or you kill them. Does your observability survive the process
dying?"

**Show.** Start `--query q2`; at ~turn 4 press **Ctrl-C**. It finishes the current turn, saves a
checkpoint and prints a resume command. Then:

```bash
docker compose --profile demo run --rm slow-query-tuner python main.py --resume <run_id>
```

It restores and completes. Open **Sessions**: two traces, **one session**, the full
investigation replayed end to end.

**Land.** "State plus a reused `session_id` means the investigation is one continuous thing in
Langfuse, even though it spanned two processes."

**Ask.** "How do you resume, or even reconstruct, a long agent run today after a restart?"

> **Fallback:** if Ctrl-C timing is fiddly, use the kill switch instead:
> `touch demos/slow-query-tuner/checkpoints/<run_id>.kill` mid-run.

---

## Act 4 · The runaway, and the alert (THE beat) (3 min)

**Frame.** "The pattern's nightmare is a \$400 overnight loop that never stops. We're going to
*cause* one, on purpose, and watch the rails catch it."

**Show.**

```bash
docker compose --profile demo run --rm slow-query-tuner python main.py --runaway
```

This pins the **naive v1 prompt** (no give-up rule), removes `propose_ddl` from the tool list,
retargets q3 to an impossible **50 ms**, raises the caps to 25 turns and \$2.50, and tags the
trace `fault:runaway`. Fast-scroll the churn: rewrite after rewrite, every `finish(success)`
claim **rejected** by the controller because it can't hit 50 ms. The app-side backstop ends it
at **`error_max_budget_usd`**. On a v4+ Langfuse, now open **Monitors** (on this stack's v3
server, go straight to the Fallback below): the **"query-tuner runaway
cost"** Monitor is in **alert** (warn/alert bands on the cost chart); show the **"query-tuner
turn count"** Monitor and the `cap_terminated` score next to it.

**Land.** "The app-side cap saved *this* run locally. The Monitor, aggregating in ClickHouse, is
what tells you it *almost* happened, and whether it's recurring across the fleet, and it
renotifies until someone fixes the prompt. That's the direct counterpart to 'never run an
unattended loop without a cap.'"

**Ask.** "What would a runaway agent cost you before anyone noticed today?"

> **Fallback:** ingestion is async and Monitors are v4+ (see Pre-flight). If the alert hasn't
> evaluated yet, or you are on this stack's v3 server, which has no Monitors: open the runaway
> trace and a normal q1 trace and put their **total cost** side by side, and show the `cap_terminated` /
> `turns_used` scores. Return to the Monitor at Q&A.

---

## Act 5 · Blocked ≠ failed (the HITL gate) (4 min)

**Frame.** "Sometimes the honest answer is *this needs a schema change*, and an agent shouldn't
make irreversible changes on its own."

**Show.**

```bash
docker compose --profile demo run --rm slow-query-tuner python main.py --query q3
```

The agent exhausts rewrites (a needle query on an unsorted 30M-row table), then calls
`propose_ddl` with a projection and a rationale. **The terminal pauses for y/n.** Approve it: the
projection is applied through the *admin* connection (the agent's own user can't), and a re-run
meets the target → `termination_reason=self_completed`. (Mention: deny → honest
`self_gave_up`, still a *good* termination.)

**Land.** "The agent knows the difference between 'I'm done,' 'I can't,' and 'I need a human',
and each is a distinct, scored `termination_reason` you can chart across the fleet."

**Ask.** "Where's the line in your world between what an agent may do and what needs a human
hand on the switch?"

> **Fallback:** for an unattended rehearsal, add `--auto-approve-ddl` (approve) or
> `--auto-deny-ddl` (the honest give-up). Materializing the projection on 30M rows takes a
> moment; narrate it. Run `reset_demo.py` afterwards, or the projection stays.

---

## Act 6 · Prove the prompt, don't vibe it (bonus, optional, 6 min)

**Frame.** "We changed the prompt to add a give-up rule. Did it actually help, or does it just
feel better?"

**Show.**

```bash
docker compose --profile demo run --rm slow-query-tuner python scripts/run_experiment.py --sample 2
```

Two arms, `v1-naive` vs `v2-disciplined`, with **caps and the tool list pinned identically**, so
any delta is the prompt. Show `pass_rate`, `avg_turns`, `avg_cost_usd`, `cap_hit_rate`, and the
`goal_drift` judge column in Langfuse → Datasets → `query-tuner/goals` → Runs.

**Land.** "Outcome-graded, never step-matched, because the same goal has many valid paths.
Discipline in the prompt is *measurable*. Flip the `production` label to that prompt and you've
deployed it with no code change: the full AI-engineering loop on one demo."

**Ask.** "How do you decide a prompt change is safe to ship today?"

> **Fallback:** a full 3-item, 2-arm run is real API spend and minutes long; use `--sample 1` to
> rehearse, and pre-run it so the Runs tab is populated (see The day before).

---

## Close · Rails, not trust (1 min)

1. **The agent decides, the environment arbitrates.** It picks its own path and stopping point;
   real ClickHouse timings and an equivalence probe decide whether it was right (Acts 1–2).
2. **Caps stop one run; Monitors catch the pattern.** The app-side budget ends a runaway, and
   the fleet view tells you it is happening again (Act 4).
3. **Irreversible means a human.** Schema changes go through an approval gate on a separate
   identity, and every way a run ends is a scored reason you can chart (Act 5).

Hand over the repo: `demos/slow-query-tuner` runs on their own ClickHouse and Langfuse with the
steps in the README, and `queries.py` is where they drop in their own slow query.

---

## Show me the code

**1 · Measured, not claimed — `ch_env.py:153`**
`run_query` times every execution with `perf_counter` and returns elapsed time, rows and bytes
read, and a result signature (`ch_env.py:166`). Errors come back as observations, never raised.

**2 · The database arbitrates equivalence — `ch_env.py:55`**
`result_signature` hashes the sorted rows, so two syntactically different queries that return the
same rows in any order match. Every candidate is checked against the baseline in `tools.py:151`.

**3 · A finish claim is re-executed — `agent_loop.py:96`**
`verify_finish_claim` runs the final SQL three times (`agent_loop.py:112`), takes the median and
checks the signature. A claim that fails is bounced back as the next observation
(`agent_loop.py:327`), so the agent keeps going or gives up honestly.

**4 · Backstops before every turn — `agent_loop.py:264` → `budget.py:62`**
`budget.check` returns a termination reason when the kill sentinel or SIGINT is set (checked
first, `budget.py:67`), or when turns, cost or wall clock pass their caps. A run that ends here is
recorded as a failure mode.

**5 · Cost from real usage — `budget.py:52`**
`cost_of` prices each response's input, output and cache tokens from the table at `budget.py:30`;
the loop adds it up at `agent_loop.py:292`. That running total is what the spend cap compares.

**6 · One tool call per turn — `agent_loop.py:286`**
`disable_parallel_tool_use` keeps the loop's contract: one `tool_use` block, one `tool_result`,
so the message history never carries a dangling tool id.

**7 · The runaway is configuration, not a code path — `main.py:104`**
`--runaway` retargets q3 to 50 ms, pins `v1-naive` and auto-denies DDL; `main.py:45` raises (never
removes) the caps; `tools.py:101` drops `propose_ddl`; `agent_loop.py:232` adds the
`fault:runaway` tag.

**8 · The human gate — `agent_loop.py:337`**
`propose_ddl` goes to `hitl_approve` (`agent_loop.py:75`), which prompts y/N on the terminal.
Only an approval reaches `apply_ddl`, the only method that opens the admin connection (`ch_env.py:146`).

**9 · The agent's identity cannot write — `sql/init/03_users.sql:10`**
`tuner_agent` is `readonly = 2` with quotas, behind an app-side allow-list
(`ch_env.py:40`) that rejects anything but a single SELECT/WITH/EXPLAIN/SHOW.

**10 · Resume reuses the session — `main.py:89`**
`--resume` loads the checkpoint and passes its stored `session_id` back into `agent_loop.run`;
`agent_loop.py:258` sets it with `propagate_attributes`, so both traces land in one session.

**11 · Compaction you can see — `checkpoint.py:92`**
Turn pairs older than `TUNER_COMPACT_AFTER` fold into one-line summaries on the goal message;
that is the input of a late `plan-next-action`.

**12 · Scores on the trace and the step — `agent_loop.py:197`**
`finalize` writes the five trace scores; `semantics_preserved` (`agent_loop.py:364`) and
`improvement_delta` (`agent_loop.py:376`) go on the spans they judge.

**13 · Readiness counts real rows — `scripts/verify_demo.py:374`**
`latest_root` reads Observations v2 and falls back to the v1 read only when v2 answers 404 and
the health endpoint reports a server below v4 (the pinned self-hosted stack). The check then counts
the trace's observations, reads its scores through Scores v3, and compares every seeded prompt
label and dataset item with the seeders' own definitions. It also reads the lab's row count and
table shape as `tuner_agent`.

---

## Talking points & objections

- **"Isn't a turn cap just a workflow with extra steps?"** No: the cap is never how a healthy run
  ends. Healthy runs end on a verified `finish`. A run that hits a cap is scored as a failure
  (`cap_terminated`), and the Monitor counts them.
- **"What stops it spending real money overnight?"** Three backstops checked before every turn:
  `TUNER_MAX_TURNS` (15), `TUNER_MAX_BUDGET_USD` (\$0.75, from the app's own price table and real
  token usage) and a 600 s watchdog, plus a kill switch (`touch checkpoints/<run_id>.kill` or
  Ctrl-C). They act at turn boundaries, so the turn in flight finishes: budget for one turn of
  overshoot.
- **"Can the agent change my schema?"** Not on its own. Its user is read-only; DDL needs a human
  y/N and runs on a separate admin connection.
- **"How do you know it didn't cheat to get fast?"** The controller re-runs the final query three
  times and compares the result signature to the original's. A rewrite that returns different
  rows is rejected, and the managed `goal_drift` judge checks it still answers the same question.
- **"Why not test each step against a golden trajectory?"** Because the same goal has many valid
  paths. The dataset grades outcomes (completion criteria), so a shorter, better path is not
  marked wrong.
- **"Why no chat UI?"** An unbounded loop with a spend cap belongs behind an operator CLI, not a
  chat box someone can fire repeatedly.

---

## Known limits

- **Monitors need Langfuse v4+.** This stack's v3 server has none, so here Act 4 always uses the
  cost side-by-side fallback; the live alert beat needs a v4+ server with the Monitors created.
- **Caps act between turns.** The turn in flight always completes, so a run can end one turn's
  cost past the budget. The spend figure is the app's estimate from its price table, not
  Langfuse's cost.
- **The thresholds are defaults, not calibrated.** The \$0.40 / \$1.00 Monitor bands and the
  runaway's \$2.50 cap have not been tuned against measured runs.
- **It's non-deterministic on purpose.** Turn counts, and whether q3 reaches `propose_ddl`, vary
  run to run.
- **Approved DDL sticks.** After Act 5, run `reset_demo.py` or the next q3 and runaway behave
  differently.

The full list is in the README's [Known limits](README.md#known-limits).

---

## Reset / re-run

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
python demos/slow-query-tuner/scripts/reset_demo.py        # drops approved DDL, clears kill sentinels
python demos/slow-query-tuner/scripts/seed_all.py          # puts prompt labels back if they moved
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1   # a fresh trace
python3 demos/slow-query-tuner/scripts/verify_demo.py      # READY again
```

`reset_demo.py --dry-run` shows what it would drop. If the table's `ORDER BY` itself was changed,
no `ALTER` undoes it: recreate the lab, which re-runs `sql/init/` on first start.

```bash
docker compose --profile demo rm -sf clickhouse-tuning
docker volume ls --format '{{.Name}}' | grep clickhouse-tuning-data   # then: docker volume rm <that name>
docker compose --profile demo up -d clickhouse-tuning
```

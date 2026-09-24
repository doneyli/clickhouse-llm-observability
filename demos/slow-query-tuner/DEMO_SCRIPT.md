# Slow Query Tuner — Demo Script (Autonomous Agent Loop on ClickHouse)

A ready-to-run demo of an **open-ended plan-act-observe agent** that optimizes a
slow query against a **live ClickHouse** — deciding *for itself* when it is fast
enough — fully observable in **Langfuse**, with caps, a kill switch, and a
runaway-cost Monitor as the rails.

- **Environment the agent acts on:** a disposable **ClickHouse 26.3** container
  (`clickhouse-tuning`) with a deliberately pessimal 30M-row table
- **Agent:** a raw Anthropic tool-use loop (`agent_loop.py`) — `plan → tool →
  assess ↺`, step count agent-decided (2–15+)
- **Observability backend:** Langfuse (`http://localhost:3001`), trace name
  `tune-clickhouse-query`, tag `slow-query-tuner`
- **No chat surface — by design** (an unbounded loop with a spend cap doesn't
  belong behind a chat box)
- **Run length:** ~25 min full; 12-min short path is Acts 1 + 2 + 4

> Everything lives in `demos/slow-query-tuner/`. The bad-query catalog is
> `queries.py`; the loop, termination judge, HITL gate and compaction are
> `agent_loop.py`.

---

## How to run this script

It's a **conversation, not a walkthrough**. Every act **frames** a problem the
room already has, **shows** how the platform answers it, **lands** the benefit,
then hands a **question** back to the room:

- **Frame** — the problem, in their terms (say it *before* you touch the screen).
- **Show** — the exact commands / clicks.
- **Land** — the "so what": the benefit, not the feature.
- **Ask** — an open question that maps it to their world.

The short path is Acts 1 + 2 + 4 (the variable loop + the runaway/Monitor beat).
Add 3, 5, 6 when there's appetite. Every act ends with a **Fallback** note —
ingestion is async and the loop is non-deterministic, so have the fallback ready.

---

## 0 · Pre-flight (do this BEFORE the meeting)

```bash
# CRITICAL: clear leaked Langfuse keys — exported shell vars override .env and 401 silently.
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY

# Langfuse (+ its ClickHouse) and the disposable tuning lab (seeds ~30M rows in seconds).
docker compose --profile langfuse up -d
docker compose --profile demo up -d clickhouse-tuning
docker compose --profile demo build slow-query-tuner

# Confirm the lab is seeded (expect ~30,000,000).
docker exec clickhouse-tuning clickhouse-client --user tuner_admin --password tuner_admin123 \
  --query "SELECT count() FROM tuning_lab.web_events"

# Prompts (v1-naive + v2-disciplined/production), root-level dataset, and MONITORS.
docker compose --profile demo run --rm slow-query-tuner python scripts/seed_all.py
./scripts/seed-code-evaluators.sh            # includes runaway-loop-guard.ts
./scripts/seed-query-tuner-evaluators.sh      # the managed goal_drift judge

# Clear any stale kill sentinels from a previous rehearsal.
rm -f demos/slow-query-tuner/checkpoints/*.kill

# Warm ingestion: one throwaway q1 run now.
docker compose --profile demo run --rm slow-query-tuner python main.py --query q1
```

**Monitors caveat (read this).** Monitors are a Langfuse **v4+** feature; this
stack pins Langfuse v3, so `seed_all.py` **prints the exact UI field values** for
the two Monitors instead of creating them. If you want the live "alert fires on
stage" beat, create them by hand in **Monitors → New Monitor** from those printed
values before the meeting. If you can't, Act 4's Fallback (runaway cost vs a
normal run, side by side) still lands the point.

**Browser tabs ready:** Langfuse Traces (`:3001`, `demo@example.com` /
`demodemo1!`), a pinned q1 trace (Agent Graph → Expanded), the Sessions view, and
Monitors.

---

## Opening · Frame (no screen)

"Yesterday's demos were workflows — the code knew the path. Today the code does
**not** know the path. I'm going to hand an agent a slow query and a target, and
it will decide how many steps it takes and when it's done. The question every one
of you is already asking: *how do you let an agent decide when it's finished — and
still sleep at night?* Three rails: verified self-termination, caps as a backstop,
and a Monitor watching the fleet. Let's watch."

---

## Act 1 · The agent does an SA's job (short run)

- **Frame.** "This is the job you'd hand a junior SA: here's a slow query, make it
  5x faster, don't change what it returns. Nobody tells the agent *how*."
- **Show.**
  ```bash
  docker compose --profile demo run --rm slow-query-tuner python main.py --query q1
  ```
  Narrate the live turns: `plan → explain-query` (full scan, String date parsed
  per row) → `plan → run-query` (measured, e.g. 4.1s → 0.4s, equivalence ok) →
  `plan → finish`. Call out the last line: **the controller re-ran the final SQL
  itself and confirmed the speedup** before accepting.
- **Land.** "Nobody told it three turns. The ground truth is the real ClickHouse
  timing — it *cannot* claim a speedup it didn't measure, and I re-check the claim
  independently. That's the difference between an agent that says it's done and
  one that *is* done."
- **Ask.** "What's your equivalent open-ended task — the one where the steps
  genuinely aren't knowable up front?"
- **Fallback.** If q1 self-terminates in 2 turns, great — that's the point (the
  agent found it was already fast enough after one rewrite). If a turn hits an env
  hiccup, note it surfaced as a `WARNING` observation and the loop continued.

## Act 2 · Same code, different shape (long run + the graph)

- **Frame.** "Same code, harder query. Watch the shape of the work change."
- **Show.**
  ```bash
  docker compose --profile demo run --rm slow-query-tuner python main.py --query q2
  ```
  While it runs, open the q1 trace in Langfuse → **Agent Graph → Expanded**. When
  q2 lands, put them **side by side**: a 3-node chain vs a 9-node chain, *same
  trace name*. Then flip q2 to **Aggregated** — the loop collapses to
  `plan-next-action → {tool} → assess-progress ↺` with repeat counts. Point at the
  `semantics_preserved` span score going **red then green** on the turn the DB
  rejected a candidate, and open a late `plan-next-action` **input** to show the
  **compacted** turn summary ("turn 3: PREWHERE rewrite, 1.9s→0.9s, equiv ok").
- **Land.** "Variable-length loops are unreadable as logs and legible as graphs —
  Expanded to debug one run, Aggregated to answer *what does this agent do overall*
  when the step count is unbounded. And it's all on ClickHouse."
- **Ask.** "When your agents misbehave today, are you reading logs — or can you
  see the shape of what they did?"
- **Fallback.** q2's turn count varies run to run — **say so out loud**; that
  variability *is* the pattern. If it self-terminates fast, re-run; if it's slow,
  narrate the plateau while you talk.

## Act 3 · Pause/resume (the sessions beat)

- **Frame.** "Unbounded loops crash, or you kill them. Does your observability
  survive the process dying?"
- **Show.** Start `--query q2`; at ~turn 4 press **Ctrl-C**. It prints a checkpoint
  message + a resume command. Then:
  ```bash
  docker compose --profile demo run --rm slow-query-tuner python main.py --resume <run_id>
  ```
  It restores and completes. Open **Sessions**: two traces, **one session**, the
  full investigation replayed end to end.
- **Land.** "State plus a reused `session_id` means the investigation is one
  continuous thing in Langfuse, even though it spanned two processes."
- **Ask.** "How do you resume — or even reconstruct — a long agent run today after
  a restart?"
- **Fallback.** If Ctrl-C timing is fiddly, use the kill switch instead:
  `touch demos/slow-query-tuner/checkpoints/<run_id>.kill` mid-run.

## Act 4 · The runaway — and the alert (THE beat)

- **Frame.** "The pattern's nightmare is a \$400 overnight loop that never
  stops. We're going to *cause* one — on purpose — and watch the rails catch it."
- **Show.**
  ```bash
  docker compose --profile demo run --rm slow-query-tuner python main.py --runaway
  ```
  This pins the **naive v1 prompt** (no give-up rule), retargets q3 to an
  impossible **50 ms**, raises the caps, and tags the trace `fault:runaway`.
  Fast-scroll the churn: rewrite after rewrite, every `finish(success)` claim
  **rejected** by the controller because it can't hit 50 ms. The app-side backstop
  kills it at **`error_max_budget_usd`**. Now open Langfuse → **Monitors**: the
  **"query-tuner runaway cost"** Monitor is in **alert** (warn/alert bands on the
  cost chart); show the **"query-tuner turn count"** Monitor and the
  `cap_terminated` score next to it.
- **Land.** "The app-side cap saved *this* run locally. The Monitor — aggregating
  in ClickHouse — is what tells you it *almost* happened, and whether it's
  recurring across the fleet, and it renotifies until someone fixes the prompt.
  That's the direct counterpart to 'never run an unattended loop without a cap.'"
- **Ask.** "What would a runaway agent cost you before anyone noticed today?"
- **Fallback.** Ingestion is async and Monitors are v4+ (see Pre-flight). If the
  alert hasn't evaluated yet, or Monitors aren't created on this v3 stack: open the
  runaway trace and a normal q1 trace and put their **total cost** side by side
  (cents vs dollars), and show the `cap_terminated` / `turns_used` scores. Return
  to the Monitor at Q&A.

## Act 5 · Blocked ≠ failed (the HITL gate)

- **Frame.** "Sometimes the honest answer is *this needs a schema change* — and an
  agent shouldn't make irreversible changes on its own."
- **Show.**
  ```bash
  docker compose --profile demo run --rm slow-query-tuner python main.py --query q3
  ```
  The agent exhausts rewrites (a needle query on an unsorted 30M-row table), then
  calls `propose_ddl` with a projection + a rationale. **The terminal pauses for
  y/n.** Approve it — the projection is applied through the *admin* connection (the
  agent's own user can't), and a re-run meets the target →
  `termination_reason=self_completed`. (Mention: deny → honest `self_gave_up`,
  still a *good* termination.)
- **Land.** "The agent knows the difference between 'I'm done,' 'I can't,' and 'I
  need a human' — and each is a distinct, scored `termination_reason` you can chart
  across the fleet."
- **Ask.** "Where's the line in your world between what an agent may do and what
  needs a human hand on the switch?"
- **Fallback.** For an unattended rehearsal, add `--auto-approve-ddl` (approve) or
  `--auto-deny-ddl` (the honest give-up). Materializing the projection on 30M rows
  takes a few seconds — narrate it.

## Act 6 (bonus) · Prove the prompt, don't vibe it

- **Frame.** "We changed the prompt to add a give-up rule. Did it actually help,
  or does it just feel better?"
- **Show.**
  ```bash
  docker compose --profile demo run --rm slow-query-tuner python scripts/run_experiment.py --sample 2
  ```
  Two arms — `v1-naive` vs `v2-disciplined` — with **caps and the tool list pinned
  identically**, so any delta is the prompt. Show `pass_rate`, `avg_turns`,
  `avg_cost`, `cap_hit_rate`, and the `goal_drift` judge column in Langfuse →
  Datasets → `query-tuner/goals` → Runs.
- **Land.** "Outcome-graded — never step-matched, because the same goal has many
  valid paths. Discipline in the prompt is *measurable*. Flip the `production`
  label to that prompt and you've deployed it with no code change — the full
  AI-engineering loop on one demo."
- **Ask.** "How do you decide a prompt change is safe to ship today?"
- **Fallback.** A full 3-item, 2-arm run is real API spend and minutes long; use
  `--sample 1` to rehearse, and pre-run it so the Runs tab is populated.

---

## Appendix — what to point at in the trace

| Beat | Where in Langfuse |
|---|---|
| Variable-length loop | Agent Graph **Expanded** (per run) vs **Aggregated** (the cycle) |
| Ground truth | each `run-query` tool span output: `elapsed_ms`, `read_rows`, `result_signature` |
| Self-termination verified | the `finish` span + `controller_verified` in its output |
| Per-turn correctness | span scores `semantics_preserved` (red→green), `improvement_delta` |
| Compaction | a late `plan-next-action` **input** (the folded turn summaries) |
| Termination reasons | trace metadata `termination_reason` + the `cap_terminated` / `termination_class` scores |
| Trajectory scores | trace scores `turns_used`, `run_cost_usd`, `verified_speedup`, `task_completed`, `trajectory_efficiency` |
| Goal drift | the managed `goal_drift` judge score on the trace root |
| Runaway | the `fault:runaway` trace + the cost/turn Monitors |
| Pause/resume | Sessions view — two traces, one `qtune-…` session |

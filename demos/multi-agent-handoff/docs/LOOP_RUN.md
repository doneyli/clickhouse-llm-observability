One pass round the AI engineering loop on bug F1, live on Langfuse Cloud (`multi-agent-handoff`
project), 2026-11-22. Commands are in the README's *The loop on one bug*; SPEC §17 is the design.
Production traffic is simulated customers (Haiku 5.5 with a persona and a goal) on the a2a topology.
Each traffic run is 24 conversations: the 12 conversation goals, twice each.

## The steps

| # | Step | Command | What happened |
|---|---|---|---|
| 0 | Ship the bug | `loop.py ship-bug` | `production` moved to concierge v3, the F1 prompt ("money back goes straight to billing; this includes outage credits") |
| 1 | Trace | `run_traffic.py --repeat 2` | 24 conversations, 81 turns. All 24 ended with the ledger in the right state. |
| 2 | Monitor | dashboard **Handoffs — quality** | The handoff judge flagged 17 of 96 handoffs, including the concierge's first handoff in 13 of 37 turns: every one an outage credit sent to billing. `customer-resolved` stayed at 0.82. 8 turns looped (billing passing the request on). |
| 3 | Datasets | `flag_handoffs.py --simulate-reviewer` | 13 flagged and 13 judged-right handoffs queued in **Flagged handoffs**. The simulated reviewer labelled the 16 turn-1 items (8 misroutes, 8 right); 10 later-turn items stay for a person. |
| | | `promote_to_dataset.py` | The 16 labelled handoffs became `handoff-routing` items linked to their traces (40 → 56 items). |
| 4 | Experiment | `loop.py fix` | concierge v4 → `candidate`, written on top of v3: plain money questions still skip a hop, and outage credits go to tech-support first. |
| 5 | Evaluate | `prompt_gate.py --label candidate --compare production` | **Passed.** See the table below. |
| | | `judge_calibration.py` | Judge vs. reviewer on the 16 labelled items: 16/16, kappa 1.0 (8 misroutes caught, 0 missed, 0 false flags) |
| ⟳ | Deploy | `loop.py promote` | `production` moved to v4. Nothing redeployed. |
| 1 | Trace again | `run_traffic.py --repeat 2` | 24 conversations, 82 turns. All 24 ended with the ledger in the right state. |

## The gate runs

`handoff-routing`, a2a topology, concurrency 6. Read back with `scripts/compare_runs.py`.

| Run | Items | routing (`handoff-target-correct`) | `path-match` | `resolution-correct` | `credit-amount-correct` | loop rate | Cost / item | p95 latency |
|---|---|---|---|---|---|---|---|---|
| production v1 (seeded prompt), before the loop | 40 | **0.975** | 0.95 | 1.00 | 1.00 | 0.00 | $0.0205 | 17.4 s |
| `fault-f1` v3 (the bug) | 40 | **0.65** ❌ | 0.60 | 1.00 | 1.00 | **0.20** ❌ | $0.0247 | 50.9 s |
| `candidate` v4 (the fix) | 56 | **1.00** ✅ | 0.97 | 0.97 | 1.00 | 0.00 | $0.0233 | 33.4 s |
| production during the loop (v3), same 56 items | 56 | **0.61** | 0.62 | 1.00 | 1.00 | — | $0.0285 | 34.5 s |

The gate blocks the bug on `routing-accuracy` and `loop-rate`. Every outcome metric passes it, because
billing recovers the skipped hop and the customer gets the right credit.

The bug also cost more. The edit was meant to save a hop, but billing has to send the request back,
so on the same 56 items the bug costs 22% more per item than the fix ($0.0285 against $0.0233). The
fix's higher p95 than the seeded prompt comes from the dataset, not the fix: the 16 promoted items
are mostly outage credits, which take the longest path (concierge → tech-support → billing).

## The dashboard, by prompt version

The dashboard's rows are the trace version, i.e. the prompt versions each turn ran. Production
traffic only, from the bug shipping onward.

| | concierge v3 (bug) | concierge v4 (fix) |
|---|---|---|
| Turns | 81 | 82 |
| Handoffs judged wrong (`handoff-appropriate` = false) | 17 of 96 (18%) | 7 of 90 (8%) |
| Turns whose first concierge handoff was flagged | 13 of 37 | 1 of 38 |
| Concierge → billing / → tech-support | 24 / 13 | 15 / 23 |
| Turns that looped (`handoff-loop`) | 8 | 0 |
| `customer-resolved` (end-to-end judge, mean) | 0.82 | 0.77 |
| Conversations with the right ledger outcome | 24 / 24 | 24 / 24 |
| LLM spend | $1.52 | $1.51 |

What to say about it:
- **The handoff judge and the routing check moved; the outcome did not.** `customer-resolved` and
  the ledger were green before the fix and stay green after it. The 0.82 → 0.77 change is within the
  noise of 24 simulated conversations. That is the F1 lesson: only a check on the handoff sees a
  misroute that the next agent recovers from.
- **The remaining flags are the next round, not this bug.** After the fix, 6 of the 7 false verdicts
  are on specialist → billing hops. There the judge wants the outage confirmation to be visible in the
  payload ("nothing in the handoff shows the outage was confirmed"). Either the payload should carry it
  or the judge prompt should accept the `outage_id`. Reviewing those is how the loop starts again.
- **Calibration here is optimistic.** The simulated reviewer labels from the scenario's ground truth,
  and only turn-1 items, which are the easy cases. The 10 later-turn items left in the queue are where
  a person and the judge are more likely to disagree (compare Act 7's `customer-resolved` example).

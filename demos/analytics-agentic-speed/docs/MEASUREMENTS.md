# Measurements

Every number the demo script quotes, with how it was measured. All runs below were taken
on 2026-11-26 against a ClickHouse Cloud service in AWS us-west-2 (2 replicas, 16 GiB
each, always on), with the agent on a laptop over the public internet (~350 ms round trip
to the service). The model is Claude Opus 5.5 at medium effort, and traces go to the
Langfuse Cloud US project `analytics-agentic-speed`. **Re-measure on your own setup before
quoting any of these numbers.** The timings depend on the service tier, the region and
the network path.

"Server ms" is ClickHouse's own `statistics.elapsed` for one query. "DB share" is the
fraction of a run's wall time spent waiting on ClickHouse (`db_time_share`, measured as
wait time, so concurrent queries are not double-counted).

The historical comparisons below predate the PR review fixes: stricter scenario SQL,
zero accuracy credit for errored runs, pinned gate prompt versions, explicit model
pricing, and bounded earlier-round history. Rerun Experiment A and the full prompt gate
before using these figures as a baseline for the fixed code.

## The data

Measured with `scripts/load_data.py`.

| | |
|---|---|
| Rows | 231,636,170 US domestic flights, 1987-10 to 2026-08 (2025-08 to 2026-02 missing at the source) |
| Load | 346 s from the BTS zips on S3 into `flights.ontime`, 3 parts |
| Sort key | `(Origin, Year, Month, Reporting_Airline, FlightDate)` |
| One airport's scenario | 477 of 28,281 granules read (the `Origin` prefix of the sort key); adding `Year = 2019` narrows it further |

## Act 2: the hero question

Measured with `main.py --question q-padding`.

| Run | Scenarios | Rounds | Sweep wall per round | Server p50 · p95 | DB share | LLM calls | Cost | Wall | Answer |
|---|---|---|---|---|---|---|---|---|---|
| First hero run | 600 | 2 | ~1.4 s per 300 | 14–18 ms · — | 5.3% | 4 | $0.14 | 62.6 s | LAX, 9 min ✓ |
| After the question fix | 624 | 3 | 1.5 s per 300 | 10 ms · 64–82 ms | 6.4% | 5 | $0.22 | 67.2 s | LAX, 9 min ✓ (85.07%) |
| Ambiguous wording (see below) | 600 | 2 | 9.2 s per 300 | 689 ms · 1,399 ms | 26.2% | 4 | $0.15 | 73.2 s | LAX, 9 min (by luck) |

**The sort key is the story in the third row.** Until 2026-11-26 the hero question ranked
"the 10 busiest airports of 2019" without saying to *measure* 2019. On that run the
model's template had no `Year` filter, so each scenario read ~4.4M rows (one airport,
every year) instead of one airport-year. Server time went up ~65×, and the DB share went
from 6% to 26%. The answer key still matched, by coincidence: the runner-up it reported
(PHX, 12 min) is wrong for 2019, where it is ATL at 10. Every question text now opens
with its measured period, and `tests/test_questions.py` enforces it.

`q-carrier-month` shows the same effect by design. It doesn't filter on airport (the sort
key's first column), so each scenario takes ~450 ms and ClickHouse is ~24% of the run.

## Warm vs cold

Measured with `scripts/warmup.py --runs 4`, then `scripts/verify_demo.py`.

| | Server ms, one hero-shaped scenario |
|---|---|
| Cold replica, first query | 150–400 |
| Warm | 10–40 (verify_demo: 11 · 11 · 10) |

## Queries in flight (one agent)

Measured with one 300-scenario hero sweep at each `--concurrency` setting.

| In flight | Scenarios / s | Sweep wall | Server p50 |
|---|---|---|---|
| 1 | 9 | ~33 s | — |
| 16 | 168 | 1.8 s | — |
| 32 (default) | 249 | 1.2 s | — |
| 64 | 244 | 1.2 s | 36 ms |

It saturates at about 32 in flight on this tier. Past that, queries queue on the server,
which shows up as a rising server p50 rather than more throughput.

## Optional act: many agents at once

Measured with `scripts/run_concurrency.py --levels 1,5,10 --yes` (each agent on a
different question, 16 queries in flight per agent).

| Agents | Queries | Failed | Server p50 | Server p95 |
|---|---|---|---|---|
| 1 | 603 | 0 | 10.0 ms | 18.6 ms |
| 5 | 1,991 | 0 | 8.7 ms | 124.7 ms |
| 10 | 3,755 | 0 | 8.7 ms | 70.1 ms |

9 of the 10 agents answered. One failed on a model refusal, which the agent retries once
and records.

## Act 4: Experiment A (sweep breadth)

Measured with `scripts/run_experiment.py --breadth both --concurrency 2`, on 15 dataset
items (`scenario-explorer-questions`, exact SQL ground truth).

**First run (`breadth-*-20261126T220710Z`), unconstrained:** 4 rounds, free lookups.
10 scenarios per round scored 93%, and so did 300. The narrow agent ranked the whole
space in one `GROUP BY` lookup and bisected over extra rounds. That is still ClickHouse
doing the search, just through a side door.

**Rerun (`breadth-*-20261126T232112Z`), constrained:** 2 rounds each. Lookups may list
candidates but not measure outcomes, and templates may not search inside the SQL. Both
rules are enforced in `explorer/scenarios.py`.

| | 10 per round | 300 per round |
|---|---|---|
| Accuracy | 40% (6/15) | 80% (12/15) |
| Scenarios per question | 19 | 419 |
| LLM calls per question | 4.1 | 3.6 |
| Wall per question | 55.2 s | 66.3 s |
| Cost per question | $0.118 | $0.144 |
| DB share | 2.8% | 6.7% |

**Caveat, found after the rerun:** four questions (`q-padding`, `q-late-departure-block`,
`q-padding-carrier`, `q-connection-window`) had the ambiguous wording described under
Act 2. On all four, neither breadth's template filtered on `Year`. That handicapped both
sides equally, so the comparison holds. But the wide run's `q-padding-carrier` miss
(LGA/OO at 40 vs 41 minutes) is most likely the wording, not breadth. Rerun
Experiment A before quoting these figures.

## Optional act: the prompt gate

Measured 2026-11-27 with `scripts/prompt_gate.py --prompt-label <label> --limit 3`
(questions `q-padding`, `q-carrier-month` and `q-late-departure-block`, 3 in parallel,
300 scenarios per round, 2 rounds). Thresholds are in `cicd/thresholds.json`.

| Label | Correct | Hero | Cost / question | Hero wall | Gate |
|---|---|---|---|---|---|
| `production` v1 | 3/3 | LAX, 9 ✓ | $0.095 | ~67 s | pass |
| `candidate-coarse` v2 ("10-minute steps are precise enough") | 3/3 | LAX, 9 ✓ | — | ~60 s | pass: the model swept 1-minute steps anyway |
| `candidate-coarse` v3 (firm 5-minute grid policy) | 2/3 | LAX, 10 ✗ | $0.088 | ~58 s | **blocked**: hero + accuracy |

On v3 the hero's grid was 10 airports × 13 buffers (0 to 60 in 5-minute steps), so 9 was
never tested.

## Experiment A on the dashboard

The dashboard's Experiment A widgets group by trace version (`breadth-10`,
`breadth-300`), so they average every Experiment A run in the window, including the
unconstrained one. For one run's numbers, use the dataset's compare view.

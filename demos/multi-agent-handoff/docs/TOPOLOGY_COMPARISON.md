Read back from `https://us.cloud.langfuse.com` on 2026-11-20 (`scripts/compare_runs.py --since 2026-11-20T03:58:00Z`): runs on `handoff-routing`, one row per run. Scores are means over the items they apply to; cost is the model cost Langfuse computed for the run's generations.

| Run | Items | resolution-correct | credit-amount-correct | outage-verified-first | reply-mentions-facts | handoff-target-correct | path-match | handoff-context-complete | unnecessary-handoff | Cost / item ($) | Mean latency (s) | p95 latency (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `a2a · production · 2026-11-20 04:01` | 40 | 1.00 | 1.00 | 1.00 | 1.00 | 0.97 | 0.93 | 0.97 | 0.12 | 0.0217 | 10.5 | 20.2 |
| `local · production · 2026-11-20 03:59` | 40 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.97 | 1.00 | 0.00 | 0.0213 | 10.0 | 19.3 |
| `single · production · 2026-11-20 03:58` | 40 | 1.00 | 1.00 | 1.00 | 1.00 | — | — | — | — | 0.0177 | 5.5 | 7.7 |

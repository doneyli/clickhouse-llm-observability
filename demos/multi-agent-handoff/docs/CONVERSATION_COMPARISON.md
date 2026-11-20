Read back from `https://us.cloud.langfuse.com` on 2026-11-20 (`scripts/compare_runs.py --dataset handoff-conversations --since 2026-11-20T04:38:24Z`): runs on `handoff-conversations`, one row per run. Scores are means over the items they apply to; cost is the model cost Langfuse computed for the run's generations.

| Run | Items | resolution-correct | credit-amount-correct | outage-verified-first | simulated-customer-satisfied | turns-to-resolution | transfers | handoff-target-correct | handoff-context-complete | Cost / item ($) | Mean latency (s) | p95 latency (s) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `a2a · production · 2026-11-20 04:42` | 12 | 1.00 | 1.00 | 1.00 | 0.58 | 4.08 | 1.33 | 1.00 | 1.00 | 0.0664 | 33.1 | 61.6 |
| `local · production · 2026-11-20 04:39` | 12 | 1.00 | 1.00 | 1.00 | 0.75 | 3.25 | 1.17 | 1.00 | 1.00 | 0.0589 | 39.1 | 73.1 |
| `single · production · 2026-11-20 04:38` | 12 | 1.00 | 1.00 | 1.00 | 0.67 | 3.83 | 0.00 | — | — | 0.0555 | 20.5 | 26.9 |

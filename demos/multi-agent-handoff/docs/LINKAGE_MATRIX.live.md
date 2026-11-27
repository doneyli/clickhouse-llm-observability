Measured 2026-11-20 against `https://us.cloud.langfuse.com` (environment `linkage-matrix`), read back through `GET /api/public/v2/observations`.

| Mode | Traces | Server roots (`isRootObservation`) | Remote obs. with session | …with user | Dangling parents | Roots | Matches offline |
|---|---|---|---|---|---|---|---|
| `none` | 3 | 3 | 0/5 | 0/5 | 0 | billing, support-turn, tech-support | yes |
| `traceparent` | 1 | 3 | 0/5 | 0/5 | 0 | billing, support-turn, tech-support | yes |
| `baggage-claim` | 1 | 1 | 0/5 | 0/5 | 0 | support-turn | yes |
| `baggage` | 1 | 1 | 5/5 | 5/5 | 0 | support-turn | yes |
| `session` | 3 | 3 | 5/5 | 5/5 | 0 | billing, support-turn, tech-support | yes |

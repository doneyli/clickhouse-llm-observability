| Mode | Traces | Logical roots | Remote obs. with session | …with user | Remote trace name | Remote environment | Dangling parents | Roots |
|---|---|---|---|---|---|---|---|---|
| `none` | 3 | 3 | 0/5 | 0/5 | None | production | 0 | billing@billing, support-turn@orchestrator, tech-support@tech-support |
| `traceparent` | 1 | 3 | 0/5 | 0/5 | None | production | 0 | billing@billing, support-turn@orchestrator, tech-support@tech-support |
| `baggage-claim` | 1 | 1 | 0/5 | 0/5 | None | production | 0 | support-turn@orchestrator |
| `baggage` | 1 | 1 | 5/5 | 5/5 | support-turn | production | 0 | support-turn@orchestrator |
| `session` | 3 | 3 | 5/5 | 5/5 | billing-task, tech-support-task | production | 0 | billing@billing, support-turn@orchestrator, tech-support@tech-support |

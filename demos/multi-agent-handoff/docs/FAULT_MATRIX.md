Measured 2026-11-20 on `https://us.cloud.langfuse.com` with `scripts/verify_demo.py --faults`: one live A2A turn per fault, read back from Langfuse (trace + online scores). 0 required failures, 0 warnings.

| Fault | Caught by | Observed | Missed by | Observed |
|---|---|---|---|---|
| baseline | — | clean | — | — |
| F1 | handoff-appropriate judge · handoff-target-correct | judge [True, True, False] · target 0 | customer-resolved (end-to-end judge) | [0.92] |
| F2 | handoff-context-complete (code evaluator) | transfer to tech-support is missing: line_id | the outcome on a lucky run | technician booked: True |
| F3 | handoff-loop (code evaluator) | ping-pong billing>tech-support>billing, tech-support>billing>tech-support; repeated hop tech-support>billing | per-hop judge (each hop looks reasonable) | 3/4 hops judged appropriate |
| F4 | trace integrity (verify_demo, dashboard) | 3 roots, 15 rows without session | quality scores (answers still correct) | customer-resolved [0.93]; 1/2 handoffs scored online |
| F5 | handoff ERROR level · handoff-succeeded | 1 failed handoff(s) | none: graceful degradation | I'm sorry, I couldn't reach our billing system just now, so I can't confirm your |


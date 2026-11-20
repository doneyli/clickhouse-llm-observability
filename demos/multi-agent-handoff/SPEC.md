# Multi-agent handoff demo (A2A) — design spec

**Status:** Draft, research in progress. Tracks DON-245.

## 1. What the demo proves

Feedback from the public training asked for a demo of multi-agent handoffs over A2A
(agent-to-agent) that shows:

1. What an agent handoff looks like in a Langfuse trace.
2. Whether a handoff lands in the same trace and the same session or splits across
   traces, and how to keep them linked (trace context propagation, session IDs).
3. How to evaluate single-agent and multi-agent setups when there is a handoff:
   per-agent spans vs. end-to-end, handoff correctness, final outcome.

The full design (architecture, trace and session design, evaluators, seeded failure
modes, build phases) follows in the next commit on this branch.

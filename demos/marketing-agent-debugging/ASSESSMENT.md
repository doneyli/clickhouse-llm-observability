# Assessment: can the existing demos run an agent-debugging workshop?

**The brief:** a team ships a probabilistic agent product. The agent gets instructions,
then decides which tools to call, what to retrieve and how to handle edge cases. Their
PMs and deployment team need to debug customer agents in Langfuse without depending on
engineers: why the agent did something, whether it retrieved the right information,
and latency. They want to debug a live trace on screen, and they will follow along
without their own accounts.

## Requirements

| # | Requirement |
|---|---|
| R1 | A real LLM tool loop where the model picks tools at runtime, not a fixed pipeline |
| R2 | Each decision readable in the UI: tool menu, tool calls, thinking, instructions version |
| R3 | A retrieval step where you can see what came back *and* why it was wrong |
| R4 | A latency and cost problem visible in the timeline |
| R5 | Agent-behaviour failures: loop, skipped step, edge case (not crashes) |
| R6 | Multi-tenant: filter by customer and by person |
| R7 | Symptom-first case files with an answer key, aimed at PMs, not engineers |
| R8 | Fixes a PM can make in the UI (instructions/prompt labels), verified with a live re-run |
| R9 | Runs on Langfuse Cloud with nothing local except Python |
| R10 | Domain close to the audience's (marketing / brand / content) |

## Coverage of the existing demos

✓ covered · ~ partly · ✗ missing

| Demo | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 |
|---|---|---|---|---|---|---|---|---|---|---|
| bank-assistant | ✓ LangGraph ToolNode | ✓ | ~ TF-IDF retriever with scores; `RETRIEVAL_K` fault | ✗ no latency fault | ~ release-knob faults (tool default window) | ~ user/segment only | ✓ symptom→root-cause arc, presenter-led | ✓ prompt labels | ✓ | ✗ banking |
| insurance-claims | ~ mostly a fixed 10-node graph, two tool loops | ✓ | ✓ release drops SOPs (best wrong-context fault in the repo) | ~ long timeline, nothing injected | ~ skipped tool → `tool-trajectory=0` | ✗ | ~ | ✓ | ~ also needs Docker for Jaeger + 2 venvs | ✗ insurance |
| real-estate | ✓ 4 tools | ~ tools are plain spans; tool list and system prompt are **not** on the generation input | ~ untyped search span | ✗ | ~ `no_search`/`wrong_tool` (realistic) + cruder text faults | ✗ | ~ | ✓ | ✓ | ✗ |
| slow-query-tuner | ✓ | ~ no tool list on the generation | ✗ | ✓ real query timings | ✓ `--runaway` loop | ✗ | ✗ | ✗ | ✗ Docker | ✗ ClickHouse tuning |
| cluster-health-investigator | ~ planner fan-out | ✓ | ✗ | ~ | ~ `--fault overplan` | ✗ | ✗ | ~ | ✗ Docker | ✗ |
| agentic-rag | ~ routed graph | ✓ | ~ titles only, no chunk text | ~ | ✗ no faults | ✗ | ✗ | ~ | ✗ Docker | ✗ |
| grocery-assistant | ✓ 7 tools (TypeScript) | ✓ | ~ | ✗ | ~ instrumentation defects, not agent behaviour | ~ | ~ error-analysis runbook | ~ | ✓ | ~ retail |
| brand-promo-multi-agent | ✗ fixed pipeline; CrewAI internals untraced | ✗ no observation types | ✗ retrieval untraced | ~ random 5 s timeout | ~ random faults, can't choose one live | ~ | ✗ | ✗ prompts not linked | ✗ localhost by default | ✓ |
| bank-assistant-workshop | ✓ | ✓ | ~ | ✗ | ~ | ~ | ✗ a *coding* workshop (learners edit `tracing.py`) | ~ | ✓ | ✗ |

**Closest fit:** `bank-assistant`. It has a real tool loop, a retriever with document
scores, environment-switchable bad decisions, a portal, and the only scripted
Symptom → Root cause walkthrough. It is the right fallback if this demo can't be used.

## What none of them cover

1. A **marketing/content agent** with a fully traced tool loop (R1 + R10).
2. **Customer as a first-class filter** (R6): customer metadata on every observation,
   several marketers per customer.
3. A **switchable latency + cost regression** you can explain from the timeline alone:
   sequential tool execution, slow dependency, and context bloat from one tool result (R4).
4. **Wrong context with the evidence split visibly:** what the model saw (passages, no
   dates) vs. what the index knew (archived status and date in the retriever metadata),
   plus a fact-checker fooled by the same index (R3).
5. A **customer-facing agent loop** driven by a tool that never reaches a final state
   *and* an instruction that says "retry until you have it" (R5).
6. An **instruction regression** a PM can find (version diff + commit message) and roll
   back with a label, then verify live (R5, R8).
7. **Symptom-first case files with an answer key and an owner per root cause** (R7).
8. The **tool list and thinking on every generation**, in the shape Langfuse parses for
   "available tools", tool-call cards and reasoning (R2). Two of the hand-instrumented
   demos above don't record them.

[README.md](README.md) and [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md) describe how this
demo covers each gap. Reusing it for another audience only takes `agent/world.py` (the
customers) and `agent/scenarios.py` (the tasks); the tools, faults and instrumentation
stay the same.

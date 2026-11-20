# Multi-agent handoff demo (A2A) — design spec

**Status:** Draft for review, not built. Tracks DON-245.
**Scope size:** Medium. New standalone demo, own venv, three local agent services.
Customer-agnostic: Northwind Mobile is fictional and all data is synthetic.

## 1. What the demo proves

Attendees of the public training asked to see agents hand work to each other over A2A
(the Agent2Agent protocol). They asked three questions:

1. **What does a handoff look like in a trace?**
2. **Does a handoff stay in the same trace and the same session, or does it split?** If
   it splits, how do you keep the pieces linked (trace context propagation, session IDs)?
3. **How do you evaluate a system with handoffs?** That covers single-agent vs.
   multi-agent setups, per-agent spans vs. end-to-end, handoff correctness, and the
   final outcome.

The demo answers each one with something you can run and click through. It does not
argue them in slides:

| Question | What the demo shows |
|---|---|
| 1. Shape | One customer turn crosses three processes over two A2A hops, and the result is **one trace**. Each handoff is a `tool` observation with a reason, the context it carried, and the A2A task id. The agent graph draws the agents as nodes. The same scenario run in-process (no A2A) gives the same tree, minus the process boundary. |
| 2. Linkage | A `--linkage` switch runs the same scenario four ways: no propagation, `traceparent` only, `traceparent` + W3C `baggage`, and session-linked traces. A measured matrix shows what each one does to trace count, logical roots, session coverage, and evaluators. The recommendation for each handoff style comes out of that matrix. |
| 3. Evals | The same dataset runs against a **single-agent baseline** and the **multi-agent system**, so Langfuse's experiment comparison shows outcome, cost, and latency side by side. Evaluators sit on four layers: the routing decision, the handoff payload, each specialist's task, and the end-to-end outcome. Seeded faults show why you need more than one layer. In one fault the end-to-end judge passes a misrouted conversation. In another every agent passes its own check, but the customer has to repeat their account number. |

### What makes this a true multi-agent handoff system

A fixed pipeline with an A2A call in the middle is not a handoff demo.
`insurance-claims` already has one of those. This system has to meet each bar below,
and each one is something you can check in a trace:

| Bar | How the demo meets it | Where you see it |
|---|---|---|
| **Agents decide to hand off.** Code does not route. | The concierge's model chooses `transfer_to_*` / `ask_*` from a roster. Specialists choose to `hand_back`, delegate onward, or transfer onward. No `if intent == "billing"` anywhere. | The `generation` before each `handoff` carries the `tool_use` and its reason |
| **Control moves, not just data.** | A transfer hands the *conversation* to the specialist. Later customer turns go straight to it on the open A2A task until it hands back. | Session view: the owner changes across turns (`active_agent_after` on each root) |
| **Separate agents, separately owned** | Each agent has its own prompt (versioned and labelled on its own), model, tools, team, and service, and its own agent card | `prompt_version`, `team`, and `service` metadata on each agent observation |
| **Multi-hop across processes** | tech-support delegates to billing directly (peer to peer). One customer turn crosses three processes. | One trace, three `service` values, two `handoff` nodes |
| **Discovery over a real protocol** | The concierge builds its roster from the agents' A2A 1.0 agent cards. Calls use the official `a2a-sdk`. | `a2a_agent_version`, task ids, and task states on each `handoff` |
| **Independent failure** | A specialist that is down or slow fails its A2A task. The concierge degrades and nothing crashes. | Fault F5: `handoff` at level `ERROR`, task state `TASK_STATE_FAILED` |
| **Multi-agent has to earn its cost** | The same dataset runs on a single-agent baseline | Experiment comparison (§9) |

## 2. Scope

| | In scope |
|---|---|
| Company | Northwind Mobile, a fictional mobile and home-internet carrier |
| Channel | Customer support chat (CLI first, optional chat portal) |
| Agents | `concierge` (front door), `billing`, `tech-support`; optional external `device-protection` partner |
| Handoff styles | **Delegate** (ask another agent and use its answer), **transfer** (another agent takes over the conversation), **hand back** |
| Topologies | `single` (one agent, all tools), `local` (multi-agent, one process), `a2a` (multi-agent, one service per agent) |
| Langfuse | Cloud project (default) or the repo's self-hosted stack (:3001) |

Out of scope: real telco systems, payments, voice, auth or identity, and production A2A
security (signed agent cards, OAuth). The demo names that security work as a gap. It
does not build it.

## 3. Why this demo and not an existing one

The repo has several multi-agent demos. None of them shows a **handoff across a
service boundary**, and none evaluates one:

| Existing demo | Multi-agent pattern | What it does not cover |
|---|---|---|
| `query-router` | Classify and dispatch | No conversation handoff. Handlers are HTTP calls with no agent-to-agent protocol. |
| `cluster-health-investigator` | Orchestrator–workers (LangGraph `Send`) | All in-process. No handoff. |
| `brand-promo-multi-agent` | LangGraph + CrewAI crews | All in-process. |
| `insurance-claims` | One Strands agent behind A2A | One fixed delegation step. No agent decides to hand off, and nothing evaluates a handoff. |

`insurance-claims` has already proved the plumbing: `traceparent` + `baggage` over an
A2A JSON-RPC call, extracted in the server before the agent runs. This demo reuses that
code (see §13) and puts the handoff itself at the center.

## 4. Architecture

```
                               ┌──────────────────── orchestrator process ─────────────────────┐
 customer ── CLI / portal :8097 ──► support-turn (root)                                         │
                               │     └─ concierge agent ── transfer_to_* / ask_* tools          │
                               │          │  transport = local | a2a                             │
                               └──────────┼───────────────────────────────────────────────────┘
                                          │ A2A (JSON-RPC over HTTP) + traceparent + baggage
                     ┌────────────────────┼─────────────────────┐
                     ▼                                          ▼
          tech-support service :9311                 billing service :9310
          (agent + A2A server)  ── A2A (peer hop) ──►  (agent + A2A server)
                     │
                     └── optional ── A2A, traceparent only ──► device-protection partner :9312
                                                              (third party: no spans to our project)

  each process: its own Langfuse client → the SAME Langfuse project
  fake CRM / outage feed / policy oracle: local JSON + Python, shared by all services
```

### Agents

| Agent | Owns | Tools | Model |
|---|---|---|---|
| `concierge` | The conversation, unless it has been transferred. Answers FAQs itself. | `faq_lookup`, `transfer_to_tech_support`, `transfer_to_billing`, `ask_billing`, `ask_device_protection` | `claude-haiku-5-5` (cheap routing) |
| `tech-support` | Connectivity and device issues | `check_outage`, `run_line_diagnostics`, `kb_search`, `ask_billing` (peer hop), `hand_back` | `claude-sonnet-5-5` |
| `billing` | Invoices, plan changes, credits | `get_invoice`, `get_plan`, `compute_credit` (deterministic policy), `issue_credit` (writes to the fake CRM), `hand_back` | `claude-sonnet-5-5` |
| `device-protection` (optional) | A partner insurer's claims agent, a black box | n/a from our side | `claude-haiku-5-5` |
| `single` baseline | Everything | Every non-handoff tool above | `claude-sonnet-5-5` |

### One agent definition, three topologies

Each agent is defined once: a prompt, a tool list, and a model. A **transport** decides
how a handoff tool reaches the target agent:

- `single`: no handoff tools. One agent gets every domain tool and a merged prompt. This
  is the baseline that question 3 needs.
- `local`: a handoff tool calls the target agent in-process. Nesting comes free from the
  OpenTelemetry context.
- `a2a`: a handoff tool sends an A2A message to the target agent's service. Trace
  context has to cross the wire (§6).

Because prompts, tools, and data are shared, the experiment comparison in §9 isolates
the architecture. The only thing that changes between runs is the topology.

### Framework decision

- The agents run on a small shared tool-use loop over the Anthropic SDK
  (`handoff/agents/base.py`), not a framework. The demo is about handoffs, and framework
  spans would bury the handoff: in this repo a three-node LangGraph path produced eight
  observations. Handoff tools follow the `transfer_to_<agent>`
  naming that the OpenAI Agents SDK and LangGraph swarm made familiar.
- A2A uses the official `a2a-sdk` **1.3.x** on both sides, speaking protocol **1.0**.
  The server is an `AgentExecutor` plus `DefaultRequestHandler`, mounted with the SDK's
  route factories (`create_jsonrpc_routes`, `create_agent_card_routes`). The client
  uses `create_client(...)` and `send_message(SendMessageRequest(...))`. That is what a
  customer would install today. `insurance-claims` speaks 0.3 over raw JSON-RPC, because
  `strands-agents[a2a]` still pins `a2a-sdk>=0.3,<0.4`. This demo reuses that demo's
  propagation helpers but not its client.
- **The SDK does not propagate trace context.** `a2a-sdk` 1.3.1 has no
  `inject`/`extract`. Its optional `telemetry` extra only creates its own CLIENT and
  SERVER spans (scope `a2a-python-sdk`), and Langfuse's default export filter drops
  that scope. So the demo injects the headers itself, on the A2A client only (§6).
- Optional Phase 6: one specialist rebuilt on **Google ADK** behind A2A (`to_a2a(agent)`).
  It shows that another framework's spans nest under the handoff once context is
  extracted. ADK is the choice because its `a2a-sdk>=0.3.4,<2` range admits 1.x;
  Strands and CrewAI would need a 0.3-compat server and their own venv. See Appendix A.

### Process and ports

| Process | Port | Notes |
|---|---|---|
| billing A2A service | 9310 | agent card at `/.well-known/agent-card.json` |
| tech-support A2A service | 9311 | |
| device-protection partner | 9312 | optional, Phase 6 |
| chat portal | 8097 | optional, Phase 6 |

No other demo in the repo uses these ports. The ones in use are 8000–8008, 8080, 8090,
8091, 8095, 8765–8769, 9190, 9210, 9211, and the main stack's 3001/3080/4000.

## 5. Handoff semantics

The A2A protocol is a client agent sending a **task** to a remote agent. Handoff styles
map onto it (A2A 1.0 method and state names) like this:

| Style | Meaning | Tool | A2A mapping | Conversation owner afterwards |
|---|---|---|---|---|
| **Delegate** | Ask a specialist and use its answer | `ask_<agent>` | One `SendMessage`, task ends `TASK_STATE_COMPLETED` with an artifact | Unchanged |
| **Transfer** | The specialist takes over the conversation | `transfer_to_<agent>` | Task stays open in `TASK_STATE_INPUT_REQUIRED`. Later user turns are sent with the same `taskId`. | The specialist |
| **Hand back** | The specialist returns control | `hand_back` | Task ends `TASK_STATE_COMPLETED` with a `handback` data part (summary and outcome) | The concierge |
| **Peer hop** | A specialist delegates onward | `ask_billing` from tech-support | Nested `SendMessage` from inside the remote agent | Unchanged |

In A2A, `contextId` groups the tasks and messages of one conversation. A client can
send a known `contextId` without a `taskId` to start a new task in that conversation.
The server derives the context from a `taskId` and rejects a mismatched pair. The demo
lets the **server mint** each `contextId` on first contact and stores it, instead of
forcing its own session id in. Server ids are opaque, and LangGraph's A2A endpoint, for
one, requires a UUID. The orchestrator keeps `active_agent`, `a2a_task_id`, and
`a2a_context_id` for each session. This router state is the hub: transfers always go through it, so the concierge
knows who owns the conversation. A peer hop is the exception, and it is deliberate: a
specialist calls another specialist directly. That is the case where trace context has
to survive two hops and three processes.

A handoff carries a **payload** that the sending agent fills in through the tool's
arguments: `reason`, `issue_summary`, plus the fields the target needs (`line_id`,
`period`, `symptom`, …). Required fields are declared per target agent in
`handoff/agents/registry.py`. The `handoff-context-complete` evaluator (§9) reads the
same declaration.

## 6. Trace and session design

### One turn = one trace, one conversation = one session

- `session_id` = conversation id (`conv-<8 hex>`). Each customer turn is one trace.
- `user_id` = customer id (`cust-1042`).
- Trace name `support-turn`. Release `HANDOFF_RELEASE`. Environment `production` for
  scenario runs, `sdk-experiment` for experiments.
- Tags (on the orchestrator only): `topology:<single|local|a2a>`, `scenario:<id>`.
  **Never put tags in baggage.** SDK 4.17 delivered a list as one garbage string, and
  Phase 0 re-checks this on v5.
- A2A `contextId` ≠ `session_id`. The two are mapped in router state and both are
  recorded on every `handoff` and remote agent observation (§5). The partner never sees
  our session id.

### Observation tree (a2a topology, the hero scenario)

Turn 1: *"My home internet has been down since last night. I want money back."*

```
support-turn              agent       role=orchestrator  [orchestrator]   ← logical root, judge target
  concierge               agent       role=agent, agent=concierge
    concierge-llm         generation  tool_use transfer_to_tech_support
    handoff               tool        role=handoff, kind=transfer, from=concierge, to=tech-support
      tech-support        agent       role=agent, agent=tech-support      [tech-support process]
        tech-support-llm  generation  tool_use check_outage
        check_outage      tool
        tech-support-llm  generation  tool_use ask_billing
        handoff           tool        role=handoff, kind=delegate, from=tech-support, to=billing, hop=2
          billing         agent       role=agent, agent=billing           [billing process]
            billing-llm   generation
            compute_credit tool
            billing-llm   generation
        tech-support-llm  generation  → reply to customer; task state TASK_STATE_INPUT_REQUIRED
```

Turn 2: *"OK, it's back now. Is the credit on my next bill?"* produces a new trace in the
same session. The router sees `active_agent=tech-support` and sends a `handoff`
(`kind=continue`) on the open task. Tech-support answers and calls `hand_back`. The
concierge closes the conversation.

Names are stable and low-cardinality: `support-turn`, `concierge`, `tech-support`,
`billing`, `handoff`, plus tool names. No ids go in names. The **metadata** holds the
specifics, and it is also what eval rules select on, never names. A rule that matches
on a name stops firing, silently, the day someone renames the observation.

| Observation | Metadata (flat keys for selectors, then detail) |
|---|---|
| `support-turn` | `role=orchestrator`, `topology`, `linkage`, `agent_path` (`"concierge>tech-support>billing"`), `handoff_count`, `active_agent_after`, `scenario` |
| `<agent>` | `role=agent`, `agent`, `team`, `service`, `prompt_version` |
| `handoff` | `role=handoff`, `handoff_kind`, `handoff_from`, `handoff_to`, `handoff_hop`, `handoff_reason`, `a2a_task_id`, `a2a_context_id`, `a2a_state`, `a2a_agent_version`, `linked_by` (which headers arrived) |

The handoff observation's **input** is the payload that was sent. Its **output** is
what came back: an artifact, a hand-back summary, or the error. It is a `tool`
observation because, to the sending LLM, a handoff is a tool call. That also puts it in
the agent graph between the two agent nodes.

The orchestrator writes `agent_path` and `handoff_count` on the root at the end of each
turn. The trajectory is assembled from the run record, not read back from Langfuse. A
code evaluator can only see the observation it matched, so the summary has to be on the
root for the loop check in §9.

### What you will see in Langfuse

**Trace tree, turn 1.** The tree above. Three services, one trace id, one logical root.
Each agent is an `agent` node, each handoff a `tool` node between two agents, and each
model call its own `generation` with tokens and cost.

**Agent graph, turn 1** (Graph tab, Expanded):

```
support-turn ─► concierge ─► handoff ─► tech-support ─► check_outage
                                             │
                                             └─► handoff ─► billing ─► compute_credit
```

**Session view, one conversation.** Three traces in order, one per turn. Each turn's
root metadata says who owned it:

```
conv-7f3a…   cust-1042
  turn 1  support-turn   concierge ► tech-support ► billing   (transfer + peer delegate)
  turn 2  support-turn   tech-support (continue) ► hand_back  (owner returns to concierge)
  turn 3  support-turn   concierge                            (closes; C3 session scores land here)
```

**Agent graph under fault F3** (Aggregated). The ping-pong shows up as a cycle with a
counter. Nobody has to read through twelve hops to see it:

```
concierge ─► tech-support (4/4) ⇄ billing (4/4)
```

**Linkage `traceparent` only (F4).** The trace tree still nests. But the
root-observation list shows two rows for one turn: `support-turn`, plus `tech-support`
with no session or user. The session view's cost misses everything the remote agents
spent.

**Linkage `session`.** Separate traces (`support-turn`, `tech-support-task`) side by
side in the same session. Each `handoff` output links to the remote task, and the
remote trace's metadata points back to the parent trace.

**Single-agent baseline.** One `agent` node, with all the tool calls under it and no
handoffs. It is the same customer turn, so the two trees can be compared side by side.

### Linkage modes: question 2, measured

`--linkage` changes only what crosses the wire:

| Mode | Sent on the A2A call | Expected in Langfuse (to be measured in Phase 0) | When to use it |
|---|---|---|---|
| `none` | nothing | Two traces. The remote one has no session, user, or release and is its own root. The concierge's `handoff` span keeps only the A2A task id as a manual link. | Never on purpose. This is the default if nobody wires propagation. |
| `traceparent` | W3C `traceparent` | One trace id, nested correctly. But the remote process marks its first span as an **app root** (`isRootObservation=true`) with no session, user, or environment. The root-observation list shows two rows, and a judge filtered on *Is Root Observation* fires twice. | A third-party agent: they can correlate if they trace, and nothing internal leaks. |
| `baggage` (default) | `traceparent` + `baggage` (Langfuse trace claim, session, user, trace name, release, environment), scoped to the one call | One trace, one logical root, and session and user on every row in every process | **Synchronous handoffs inside your platform** (delegate, transfer turns) |
| `session` | A2A message `metadata.session_id` / `user_id` (application level), no `traceparent` | Two or more traces in **one session**, cross-linked by `handoff.parent_trace_id` ↔ `a2a_task_id` | **Asynchronous or long-running handoffs**: the remote work outlives the turn (push notifications, human approval, hours later). It lets each agent's run be its own trace and get its own evaluation. |

The mechanism behind the `traceparent` row is already documented in this repo
(`docs/LANGFUSE_V4_MIGRATION_SPEC.md` §16 and the bank-assistant MCP hop). The receiving
SDK marks its first span as an app root unless the context carries the
`langfuse_trace_id` baggage claim. The fix is `propagate_attributes(..., as_baggage=True)`
scoped to the outbound call, with a `CompositePropagator([TraceContext, W3CBaggage])` on
both sides. Langfuse documents the risk: baggage goes into **all** outbound HTTP headers.
So the scope stays tight, no PII goes in it, and it is never sent to the partner.

### Where the headers get injected

The A2A spec defines no tracing field. Its enterprise guidance says to use
OpenTelemetry with W3C Trace Context in ordinary HTTP headers, and that is what the
demo does. Three implementation rules:

1. **Inject on the A2A client only**, through an `a2a-sdk` `ClientCallInterceptor` (or a
   request hook on the client's httpx instance; Phase 0 picks one). Do **not** install
   global httpx auto-instrumentation to get this. The Anthropic SDK also uses httpx, so
   the session and user baggage would ride along on every model API call.
2. **Inject from the `handoff` observation's context.** Leave the SDK's own spans off
   (`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`). If an `a2a-sdk` CLIENT span is
   current when the headers are written, the remote agent gets that span as its parent.
   Langfuse drops the span, and the remote agent then points at a parent that was never
   exported. Phase 0 checks `parentObservationId` across the hop.
3. **Extract in the executor before the agent runs.** Read `traceparent`, `tracestate`,
   and `baggage` from the server call context's headers, then `context.attach` them. This
   is the same pattern as `insurance-claims/doc_intake/service.py`, ported to the 1.x
   executor API. Then open the remote agent's observation, with `linked_by` recording
   which headers actually arrived.

The A2A samples repo has a *Traceability Extension*: a list of the agent's steps
returned in message or artifact `metadata`. That is an application-level call log, not
trace context. It is useful for the black-box partner (§19), where it is the only view
inside, and the demo records it on the `handoff` output when the partner sends one.

### Measuring the matrix

`scripts/linkage_matrix.py` runs the hero scenario once per mode. It then reads the
results back through the v2 observations API and prints the measured table: trace
count, `isRootObservation` count, the share of remote observations with `sessionId`,
the environment on remote rows, and how many times the root-filtered judge fired. The
README publishes that output, not the "expected" column above.

## 7. Data

All synthetic, under `data/`:

- `customers.json`: 40 customers with lines (`mobile` / `home-internet`), plans,
  addresses (zip only), and invoices.
- `outages.json`: outage windows by zip, including one that spans midnight (the
  credit-period edge case).
- `kb/`: about 25 troubleshooting articles (router reset, APN settings, eSIM transfer, …).
- `policy.py`: a **deterministic credit oracle**. Credit = pro-rated daily rate ×
  outage hours, rounded up to whole days, capped at one month, and only if the outage
  is confirmed. The oracle is the ground truth for `credit-amount-correct`.
- Fake CRM writes (`issue_credit`, `book_technician`) go to an in-memory store per run
  and are returned in the run record, so outcome evaluators can check state and not
  just prose.

### Datasets (Langfuse)

| Dataset | Items | Each item has |
|---|---|---|
| `handoff-routing` | ~40 single-turn requests: FAQ (no handoff), billing, tech, outage→credit (chain), device damage (partner), ambiguous | `input.message`, `input.customer_id`; `expected_output.path` (e.g. `["concierge","tech-support","billing"]`), `expected_output.first_handoff`, `expected_output.required_fields`, `expected_output.outcome` (`credit:12.40`, `technician_booked`, `faq_answered`, …) |
| `handoff-conversations` | ~12 multi-turn goals for transfer mode, driven by a user simulator (Haiku, persona + goal + stop rule) | `expected_output.outcome`, `max_turns`, `expected_owner_sequence` |

Multi-turn evaluation follows the pattern this repo has already verified: the simulator
drives the conversation inside one experiment item, so a multi-turn problem becomes a
single-item experiment. See the real-estate multi-turn work.

## 8. Agents and prompts

- Prompts live in Langfuse prompt management, one per agent: `concierge`,
  `tech-support`, `billing`, `single-agent`. They are fetched by label (`production`,
  `candidate`) with a cached fallback in code. Generations link to the prompt version
  that was served.
- The concierge prompt carries the **agent roster**: name, what it owns, and when to
  hand off. On the `a2a` topology the concierge builds the roster at startup from each
  service's **agent card** (`/.well-known/agent-card.json`, its `skills`). That is A2A
  discovery, so adding an agent means starting a service and registering its URL, not
  editing the concierge. On `local` the same cards come from `registry.py`. The
  `handoff-appropriate` judge reads the same roster from the handoff's metadata, so the
  prompt and the evaluator's idea of "the right agent" cannot drift apart.
- Each specialist prompt says when to `hand_back` and forbids transferring to the agent
  that just transferred in. Fault F3 removes that rule (§10).

## 9. Evaluators

Four layers. Each scores something the other layers cannot see.

| # | Layer | Score | Kind | Where it runs | Reads |
|---|---|---|---|---|---|
| A1 | Routing | `handoff-target-correct` | deterministic | experiment (item) | first handoff target vs. `expected_output.first_handoff` |
| A1' | Routing | `handoff-appropriate` | LLM judge | online, `role=handoff` | the handoff's input (payload, reason) and the roster in metadata |
| A2 | Handoff payload | `handoff-context-complete` | **code evaluator** (stdlib Python) | online, `role=handoff` | input payload vs. the target's required fields. The comment lists what is missing. Also flags disallowed fields (full card numbers). |
| A3 | Specialist task | `credit-amount-correct` | deterministic (oracle) | experiment (item) | the run record's `compute_credit` result and the amount stated in the reply vs. `policy.py` |
| A3' | Specialist task | `diagnosis-correct` | deterministic | experiment (item) | tech-support's outage/diagnostic conclusion vs. the scenario truth |
| B1 | Trajectory | `path-match` (exact, plus a subsequence variant) | deterministic | experiment (item) | `agent_path` vs. `expected_output.path` |
| B2 | Trajectory | `handoff-loop` | **code evaluator** | online, root (`role=orchestrator`) | `metadata.handoff_count > 4` or the same agent pair repeated |
| B3 | Trajectory | `unnecessary-handoff` | deterministic | experiment (item) | a handoff happened where `expected_output.path == ["concierge"]` |
| C1 | Outcome | `resolution-correct` | deterministic (state) + judge (reply) | experiment (item) | CRM writes vs. `expected_output.outcome`; the judge checks that the reply states it correctly |
| C2 | Outcome | `customer-resolved` | LLM judge | online, `isRootObservation = true` | the turn's input and output on `support-turn` |
| C3 | Conversation | `turns-to-resolution`, `transfers` | from code, `create_score(session_id=…)` | at conversation end | the router's session state |
| — | Run level | `routing-accuracy`, `loop-rate`, `mean-cost`, `p95-latency` | run evaluators | experiment (run) | aggregates of the above |

Design rules this table follows, each learned in another demo in this repo:

- An observation-level judge sees only the observation it matched. There are no
  siblings, children, or other turns. So the summaries it needs (`agent_path`, the
  roster) are written onto the observation it targets.
- Rules select by metadata (`role`, `agent`) plus a `traceName` scope guard, never by
  observation name. **The emitter ships before the rule.** If the rule ships first, the
  judge silently matches nothing.
- Code evaluators are standard library only, finish in under 2 seconds, and have no
  network. A2 and B2 meet that. Anything that needs the oracle runs in the experiment
  process.
- There is no session-target judge. Conversation-level scores come from code (C3) or a
  `conversation-snapshot` observation on the last turn. Transferred conversations go to
  a **SESSION** annotation queue for human review.
- Judges get a pinned model. A fresh self-hosted project with no default model blocks
  them with `DEFAULT_EVAL_MODEL_MISSING`, and nothing reports it.

### Single-agent vs. multi-agent (question 3, head on)

`scripts/run_experiment.py --topology single|local|a2a` runs `handoff-routing` three
times with a time-stamped run name. A run name that already exists appends to the old
run. Compare in Langfuse:

- **Outcome** (`resolution-correct`, `credit-amount-correct`): does splitting the work
  help?
- **Cost and latency per item**: what the split costs. `local` vs. `a2a` isolates the
  cost of the network hop. `single` vs. `local` isolates the cost of the extra agents.
- **Routing and trajectory scores**: these exist only for the multi-agent runs. For the
  single-agent run, the equivalent is the tool sequence (`tool-path-match` on the tool
  names), so both architectures get a process score.

The talking point does not depend on which architecture wins. Multi-agent has to earn
its cost on a metric you chose before you ran it. The demo publishes the measured
numbers and does not predict them.

## 10. Seeded failure modes

Each fault can be switched on live (a prompt label or an env flag) and has a layer that
catches it and a layer that misses it:

| # | Fault | How it is seeded | Caught by | Missed by (the lesson) |
|---|---|---|---|---|
| F1 | **Misroute.** "Credit for an outage" goes straight to billing, skipping tech-support's outage check, so billing credits an outage that never happened. | concierge prompt label `candidate` (a "simplified" roster) | A1, B1, A3 (the oracle refuses the credit) | **C2.** The reply is polite and confident, so the end-to-end judge passes it. |
| F2 | **Context loss.** A "token-saving" refactor drops `line_id` from the handoff payload. The specialist asks the customer again, or picks the wrong line. | `HANDOFF_SLIM_CONTEXT=1` | A2 (names the missing field), C3 (`turns-to-resolution` goes up) | **A3 on a lucky run.** Each agent did its own job correctly. |
| F3 | **Ping-pong.** Billing transfers "technical" questions to tech-support, and tech-support transfers "credit" questions to billing, so an ambiguous request loops until the hop cap. | specialist prompt labels `candidate` | B2, the agent graph (Aggregated mode draws the cycle), the cost dashboard | Any single-agent check. Every hop looks reasonable on its own. |
| F4 | **Broken propagation.** "The new A2A client dropped the baggage header." | `--linkage traceparent` (or `none`) | `verify_demo.py` trace-integrity check (remote rows without `sessionId`, more than one logical root), the dashboard widget "observations without session" | Every quality score. The answers are still correct, and only the observability broke. |
| F5 | **Remote agent down or slow.** | stop the billing service, or set `BILLING_LATENCY_MS=20000` | `handoff` observation level `ERROR`, A2A state `failed`, the concierge fallback reply, a `handoff-success` rate widget | none. Shows graceful degradation. |

## 11. Experiments and CI gate

- `scripts/run_experiment.py`: `dataset.run_experiment(...)` with the item evaluators
  and run evaluators from §9. Experiments run with `sample_rate=1.0`.
- `scripts/prompt_gate.py --prompt concierge --label candidate`: runs `handoff-routing`
  on the `a2a` topology and **blocks** if `routing-accuracy < 0.9`, `loop-rate > 0`, or
  `resolution-correct` drops more than 5 points below the `production` label. Copied from
  the repo's existing prompt-gate pattern (real-estate, bank, claims).
- `.github/workflows/handoff-prompt-gate.yml`: the same gate on a prompt-update event,
  following the claims workflow template. The CI services start the two A2A agents
  locally on the runner.

## 12. Dashboards (as code)

Seeded with `scripts/seed_dashboards.py` (Metrics API v2) and validated by
`scripts/check_dashboards.py`, the same pattern as real-estate and claims:

- Cost and p95 latency **per agent**: observations by `metadata.agent`
- Handoffs by `handoff_kind` and `handoff_to`; handoff success rate (`a2a_state`)
- `handoff-target-correct`, `handoff-context-complete`, and `handoff-loop` over time
- Observations without `sessionId` (the F4 tripwire)
- Single vs. multi: cost per resolved item, by experiment run

## 13. Reuse

Copy and adapt. Each demo stays standalone, which is the repo convention.

| From | What |
|---|---|
| `insurance-claims/claims/config.py` | Profile loading (shell `LANGFUSE_*` ignored), `CompositePropagator`, `inject_trace_context` / `extract_trace_context`, `should_export_span` (excludes duplicate native Anthropic spans if a framework also traces) |
| `insurance-claims/doc_intake/service.py` | Server-side extract before the agent runs. Wrapper observation with `linked_by` metadata. Failed task on exception, never a hang. That file uses the 0.3 executor API (`new_task`, the `TaskUpdater` signature), so port the pattern, not the code. |
| `insurance-claims/claims/prompts.py`, `scripts/prompt_gate.py`, `scripts/run_experiment.py` | Prompt fetch by label with fallback, the gate, time-stamped run names |
| `real-estate` | Dashboard seeding, the evaluator seeder that reconciles drifted rules, SESSION annotation queue, user simulator |
| `bank-assistant` | `verify_demo.py` shape and the zero-build portal pattern |

New: the agents, the transports and linkage modes, the router, the data, the handoff
evaluators, and `linkage_matrix.py`.

## 14. Versions and Phase-0 spikes

| Decision | Recommendation | Verify in Phase 0 |
|---|---|---|
| Langfuse Python SDK | **v5** (`langfuse>=5.2,<6`, Python 3.11+; 5.2.0 shipped 2026-11-05). This new standalone demo has no legacy calls to migrate, and Cloud removed the legacy v3 read endpoints on 2026-11-16. The repo's other demos stay on v4. v5 needs Cloud or self-hosted 4.59+, and the main stack runs 4.79.0. | `propagate_attributes(as_baggage=True)` behaves as on v4; v5's JSON-encoded metadata doesn't break the `stringObject` rule filter |
| A2A SDK | `a2a-sdk[http-server]==1.3.*` (1.3.1, 2026-11-17), the same pin on both sides | which client hook injects headers (§6); the executor reads headers from the server call context; with SDK telemetry off, remote `parentObservationId` = the `handoff` observation |
| A2A protocol | **1.0** (v1.0.1, 2026-05-28). Send `A2A-Version: 1.0`. | `TASK_STATE_INPUT_REQUIRED` keeps a task open across turns; a second message with the stored `taskId` continues it |
| Model spans | Anthropic SDK + explicit Langfuse `generation` observations, or the SDK's native GenAI spans if Langfuse maps them with model, usage, and cost | no double counting (claims saw duplicates when a framework also traced) |
| Code evaluators | created through the stable Evaluators + Evaluation Rules API | if the API can't create code evaluators, fall back to a documented UI step (the user owns it, as with claims automations) |

Gotchas already known from this repo, applied from day one: `temperature` is rejected by
current Anthropic models; one `Langfuse()` client per process (a second client disables
`get_client()`); flush before the A2A response returns; a sampled-out span has no
`.attributes`; unset shell `LANGFUSE_*` before running.

## 15. Layout

```
demos/multi-agent-handoff/
  SPEC.md  README.md  DEMO_SCRIPT.md  requirements.txt  .env.example
  handoff/
    config.py            # profile, Langfuse client, propagators (from claims)
    registry.py          # agents: prompt name, model, tools, required handoff fields
    agents/base.py       # traced tool-use loop
    agents/{concierge,tech_support,billing,single}.py
    tools/{crm,outages,kb,policy}.py
    transport.py         # LocalTransport, A2ATransport, linkage modes
    a2a_service.py       # generic AgentExecutor: serve any registry agent
    orchestrator.py      # turn → root observation, router state, session scores
    prompts.py
  partner/               # optional device-protection agent (Phase 6)
  evaluators/            # stdlib code evaluators: handoff_context_complete.py, handoff_loop.py
  data/                  # customers.json, outages.json, kb/, datasets/*.jsonl
  scripts/
    run_services.sh      # start billing + tech-support (+ partner, portal)
    run_scenario.py      # one scenario: --topology --linkage --fault
    run_traffic.py       # loop step 1: simulated production conversations
    promote_to_dataset.py # loop step 3: labelled queue items → handoff-routing
    linkage_matrix.py    # measures §6 against the live project
    seed_prompts.py  seed_datasets.py  seed_evaluators.py  seed_dashboards.py
    run_experiment.py  prompt_gate.py  verify_demo.py  check_dashboards.py
  tests/                 # router, transport (two-process in-memory OTel harness), oracle, evaluators
```

## 16. Build phases

| Phase | Deliverable | Done when |
|---|---|---|
| 0. Spike | Two-process harness: a2a-sdk client → server with traceparent + baggage. Linkage modes measured against Cloud. SDK v5 + a2a-sdk resolve in one venv. | The §6 matrix is filled with measured values. |
| 1. Agents, local | Registry, tool loop, data, oracle, `local` + `single` topologies, `run_scenario.py` | The hero scenario gives the §6 tree in one process |
| 2. A2A | Services, `A2ATransport`, transfer/continue/hand-back state, peer hop, linkage modes, `linkage_matrix.py` | The hero scenario on `a2a` gives one trace across three processes, and the matrix script reproduces Phase 0 |
| 3. Evals offline | Datasets, item and run evaluators, the three-topology comparison | Three experiment runs comparable in the UI |
| 4. Evals online + faults | Code evaluators, judges, annotation queue, F1–F5 switches, dashboards, `run_traffic.py` | Each fault trips its catching layer in `verify_demo.py` |
| 5. Loop + gate + script | `promote_to_dataset.py`, `prompt_gate.py`, workflow, `DEMO_SCRIPT.md`, README with measured numbers, repo indexes (demos/README, docs/README, AI_ENGINEERING_LOOP.md, CLAUDE.md layout) | One full pass of §17 runs live: F1 in production → flagged → labelled → fixed → gated → promoted → misroute rate down |
| 6. Optional | device-protection partner (black box), second-framework specialist, chat portal | |

## 17. The AI engineering loop, end to end

The demo goes round the repo's loop ([`AI_ENGINEERING_LOOP.md`](../../AI_ENGINEERING_LOOP.md))
once, on one bug. It starts the way real incidents start: **release 1.1 has already
shipped F1**. The concierge's `production` prompt is the "simplified roster" version,
and outage-credit requests now skip tech-support.

| # | Step | What happens in the demo | Artifacts |
|---|---|---|---|
| 1 | **Trace** | `scripts/run_traffic.py` drives about 50 simulated conversations through the `a2a` topology, the way production traffic would arrive. Every turn is one trace across the services, and every conversation is a session. | §6 trace design; `run_traffic.py` (user simulator + scenario mix) |
| 2 | **Monitor** | The online evaluators score live traffic: `handoff-appropriate` drops on billing handoffs, while `customer-resolved` stays green (the F1 blind spot). The dashboard shows misroutes by `handoff_to`, plus credits billing refused for "no confirmed outage". Simulated customers leave thumbs-down, which land as a `user-feedback` score. Optional: a Langfuse alert on the `handoff-appropriate` rate. | §9 A1′/A2/B2/C2; §12 dashboards |
| 3 | **Datasets** | Filter the flagged handoffs and send them to an annotation queue. A reviewer labels the **correct path** for each. `scripts/promote_to_dataset.py` turns the labelled items into `handoff-routing` items, with the reviewer's path as `expected_output.path`. Production failures become test cases. | annotation queue (handoff observations + SESSION items); `promote_to_dataset.py` |
| 4 | **Experiment** | Write the fix: concierge prompt v3, label `candidate`. Run it against `handoff-routing`, next to `production`, on `a2a`, and next to the `single` baseline. | `run_experiment.py --prompt-label candidate --topology a2a` |
| 5 | **Evaluate** | Compare the runs: `routing-accuracy`, `path-match`, `credit-amount-correct`, `resolution-correct`, cost, latency. Check the judge against the reviewer's labels from step 3 (agreement on `handoff-appropriate`) before trusting it in step 2 again. | §9 item + run evaluators; judge-calibration script (from bank/claims) |
| ⟳ | **Deploy** | Promote `candidate` to `production`. The label change fires the CI gate (§11). The gate re-runs the dataset and passes v3. A version that drops `routing-accuracy` would be blocked. The services fetch prompts by label at runtime, so nothing redeploys. Run step 1 again, and the dashboard shows the misroute rate back down. | `prompt_gate.py`; `.github/workflows/handoff-prompt-gate.yml` |

The same loop runs for F2 (the payload check flags it, the fix is the payload builder,
and the gate adds `handoff-context-complete`) and F3 (the loop check flags it, the fix
is the specialist prompts, and the gate adds `loop-rate = 0`). The difference: F1 and
F3 are prompt changes, shipped by label, while F2 is a code change, shipped by release.
`HANDOFF_RELEASE` on every trace shows which one fixed what.

## 18. Demo script outline (about 30 minutes)

Written later in the repo's client-script style (each act frames the problem, shows it,
lands the point, then asks a question). Outline only:

1. **A handoff, in a trace.** Run the hero scenario on `a2a`. Open the trace and walk
   the tree, then the agent graph. Point at the `handoff` observations: the reason,
   the payload, and the A2A task id. Three services, one trace. Then run it on `local`:
   the same tree, without the process boundary.
2. **Same trace? Same session?** Flip `--linkage` live: `none`, then `traceparent`,
   then `baggage`. Show the second root and the missing session, then the fix. Then
   `session`: the right answer when the remote work outlives the turn. Show the
   measured matrix.
3. **Evaluating handoffs.** Compare the experiments: single vs. multi on outcome, cost,
   and latency. Then the faults: F1 (the end-to-end judge passes a misroute, and the
   routing score catches it), F2 (each agent passes, but the payload check names the
   missing field), F3 (the loop shows up in the agent graph and on the cost chart).
4. **Gate it.** The F1 prompt goes through the CI gate and gets blocked.

## 19. Known limits (say them out loud)

- **No session-target judge in Langfuse.** Conversation-level quality comes from code
  scores, a snapshot observation, or human review in a SESSION queue.
- **A third-party agent is a black box.** You see your side of the A2A call: payload,
  latency, task state, and the artifact. You evaluate the handoff from there. Their
  spans go to their tool, not your project.
- **One trace needs one project.** A remote agent that exports to a different Langfuse
  project splits the trace across projects, even with perfect propagation.
- **Baggage is in every outbound header** while it is active. Scope it to the call. No
  PII, no tags, never to a third party.
- **Async A2A** (push notifications, long-running tasks) does work after the turn's
  trace has closed. Use `session` linkage for it. Do not reopen old traces.
- **A2A security is out of scope:** agent-card signing, auth schemes, and per-skill
  authorization.

## 20. Decisions for review

1. **Domain:** telco support (Northwind Mobile), chosen for a familiar handoff story
   with a crisp numeric oracle (credits). Alternatives: travel (partner hotels as true
   cross-company A2A) or SaaS support.
2. **Langfuse SDK v5** for this demo while the rest of the repo stays on v4 (§14).
3. **Partner black-box agent in v1 or Phase 6.** It is the strongest "A2A across
   companies" moment, but it adds a fourth service.
4. **Cloud project name:** `multi-agent-handoff` (US), keys minted with the org key the
   same way as `brand-promo-demo`.

## Appendix A. The same handoff in other frameworks

For an audience on a framework, here is how its native handoff maps to this demo's
`handoff` observation. All of these are in-process. Over A2A, all that links the two
sides is `contextId`/`taskId` plus the trace headers you send.

| Framework | In-process handoff | Expose over A2A | Call a remote A2A agent | `a2a-sdk` pin |
|---|---|---|---|---|
| OpenAI Agents SDK 0.25 | `transfer_to_<agent>` tool from `Agent(handoffs=[...])`; the new agent gets the history (trim with `input_filter`); exports a `handoff_span` | not native (community bridges only) | — | — |
| LangGraph | `Command(goto=…, graph=Command.PARENT)`; `langgraph-swarm`'s `create_handoff_tool` makes `transfer_to_<agent>` | Agent Server: built-in `/a2a/{assistant_id}` (state needs `messages`; `contextId` → `thread_id`, must be a UUID; no push or streaming resubscribe; 0.3-shaped responses) | not documented | own implementation |
| Google ADK 2.15 | `transfer_to_agent(agent_name, transfer_reason)` among `sub_agents` | `to_a2a(agent, port=…)` (`google-adk[a2a]`) | `RemoteA2aAgent` | `>=0.3.4,<2` |
| AWS Strands 1.65 | Swarm / graph patterns | `A2AServer(...).serve()` | `A2AAgent` | **`>=0.3.0,<0.4.0`** |
| CrewAI 1.15 | Crew delegation | `Agent(a2a=A2AServerConfig(url=…))` (card says protocol 0.3.0) | `A2AClientConfig` | **`~=0.3.10`** |
| Microsoft Agent Framework | Workflows / handoff orchestration | `A2AExecutor(agent)` + `a2a-sdk` route factories | `A2AAgent` | `>=1.0,<2` |
| Pydantic AI 2.71 | Agent delegation via tools | `fasta2a[pydantic-ai]` `agent_to_a2a` (`Agent.to_a2a()` was removed in v2) | not documented | own implementation |

The 0.3 pins in Strands and CrewAI mean those agents can't share a venv with a 1.x
client. Run each in its own environment and serve it with a 1.x route factory that has
`enable_v0_3_compat=True`, or speak 0.3 to them the way `insurance-claims` does. Third-party
A2A instrumentation packages exist (`traceai-a2a`, `a2a-otel-kit`, and others) but none
are from OpenTelemetry, OpenInference, or Langfuse. The demo does not use them: the
explicit injection in §6 is about ten lines, and you can read every one of them.

## Appendix B. Sources (checked 2026-11-19)

- A2A releases and the 1.0 changes: github.com/a2aproject/A2A (`releases`, `docs/whats-new-v1.md`, `docs/specification.md` §3.4, `docs/topics/enterprise-ready.md`)
- A2A Traceability Extension sample: github.com/a2aproject/a2a-samples/tree/main/extensions/traceability/v1
- `a2a-sdk` 1.3.1: pypi.org/project/a2a-sdk; source `a2a/utils/telemetry.py`, `docs/migrations/v1_0/README.md`
- Langfuse: `/docs/observability/features/trace-ids-and-distributed-tracing`, `/docs/observability/sdk/instrumentation` (cross-service propagation, `as_baggage`), `/docs/observability/features/agent-graphs`, `/docs/observability/best-practices`, `/docs/evaluation/evaluation-methods/code-evaluators`, `/docs/observability/sdk/upgrade-path/python-v4-to-v5`, `/docs/observability/features/mcp-tracing`
- Framework docs: adk.dev/a2a, strandsagents.com (agent-to-agent), docs.crewai.com (a2a-agent-delegation), docs.langchain.com/langsmith/server-a2a, openai.github.io/openai-agents-python/handoffs, learn.microsoft.com (agent-framework A2A hosting), github.com/datalayer/fasta2a
- This repo: `docs/LANGFUSE_V4_MIGRATION_SPEC.md` §16 (baggage across a process hop), `demos/insurance-claims/SPEC.md` §3–4

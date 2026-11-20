# Multi-agent handoff demo (A2A)

A support desk for **Northwind Mobile**, a fictional phone and home-internet provider. Agents hand
work to each other over **A2A 1.0**, and every handoff is traced and evaluated in Langfuse. All
customers, lines, outages and invoices are synthetic.

**The questions it answers** (from public-training feedback, DON-245):

1. What does a handoff between agents look like in a trace?
2. Does a handoff stay in the same trace and session, or split, and how do you keep the pieces
   linked across services?
3. How do you evaluate a system with handoffs: per agent, per handoff, end to end, and against
   a single agent doing the same job?

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 45 minutes)
- **Design and measured results:** [SPEC.md](SPEC.md)
- **Measured tables:** [linkage, live](docs/LINKAGE_MATRIX.live.md) ·
  [single vs. multi-agent](docs/TOPOLOGY_COMPARISON.md) ·
  [conversations](docs/CONVERSATION_COMPARISON.md) · [faults](docs/FAULT_MATRIX.md)

## What runs where

```
 customer (you, run_scenario.py, or the simulated customer in run_traffic.py)
        │
        ▼
 orchestrator process ── concierge agent (Haiku 5.5) ── transfer_to_* / ask_* tools
        │  A2A 1.0 (JSON-RPC) + traceparent + baggage, per call
        ├──────────────► tech-support service :9311 (Sonnet 5.5)
        │                     │  A2A peer hop: ask_billing
        └──────────────► billing service :9310 (Sonnet 5.5) ◄─┘
                 │
    one Langfuse client per process ──► Langfuse Cloud project `multi-agent-handoff`
    shared back-office ledger ──► .run/ledger.sqlite (credits, technician visits)
```

- **One customer turn is one trace** (`support-turn`) across all three processes.
- **One conversation is one session.**
- **A transfer hands the conversation over:** later turns go straight to the specialist on its open
  A2A task (`input-required`) until it calls `hand_back`.
- **A delegation (`ask_billing`) is a question:** the caller keeps the conversation and gets the answer
  back.
- **The models decide every handoff;** no code routes requests.

| Agent | Runs as | Owns | Can hand to |
|---|---|---|---|
| `concierge` | in the orchestrator | the front door: FAQs, account look-ups, routing | transfer: tech-support, billing · ask: billing |
| `tech-support` | A2A service :9311 | connectivity, outages, diagnostics, technician visits | transfer: billing · ask: billing · hand back |
| `billing` | A2A service :9310 | invoices, plans, credits (a policy oracle decides the amount) | transfer: tech-support · hand back |
| `single` | in the orchestrator | everything, with every tool (the single-agent baseline) | nothing |

The same agents run in three **topologies**:
- **`a2a`:** the specialists are services, as drawn above.
- **`local`:** the same agents in one process.
- **`single`:** one agent with every tool.

That makes the single- vs. multi-agent comparison like for like.

## Set up from scratch

You need Python 3.11+, [`uv`](https://docs.astral.sh/uv/), an Anthropic API key, and a Langfuse
Cloud project with its API keys. The judges also need an LLM connection; this project uses the
organization's connection named `Anthropic` (see [Troubleshooting](#troubleshooting)).

```bash
cd demos/multi-agent-handoff
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env            # then fill in LANGFUSE_PUBLIC_KEY / _SECRET_KEY / _BASE_URL and ANTHROPIC_API_KEY
.venv/bin/python -m pytest -q tests                        # 34 tests, no server or model calls
```

Seed the project. Every seeder is idempotent and reconciles drift, so re-running is safe.

```bash
.venv/bin/python scripts/seed_prompts.py --faults     # 4 agent prompts @production + fault variants @fault-f1 / @fault-f3
.venv/bin/python scripts/seed_datasets.py             # handoff-routing (40 items) + handoff-conversations (12)
.venv/bin/python scripts/seed_evals.py --judges --queue   # 2 code evaluators, 2 LLM judges, rules, review queue
```

Start the agents and generate data:

```bash
./scripts/run_services.sh                             # billing :9310 + tech-support :9311 (logs in .run/)
.venv/bin/python scripts/run_traffic.py               # 12 simulated conversations as production traffic (~3 min)
.venv/bin/python scripts/run_experiment.py --topology single    # the three-way comparison, ~2 min each
.venv/bin/python scripts/run_experiment.py --topology local
.venv/bin/python scripts/run_experiment.py --topology a2a
.venv/bin/python scripts/verify_demo.py               # readiness: should print 0 required failures
```

The `.env` is the only source of `LANGFUSE_*`. Shell-exported keys are ignored on purpose, because
a key for another project would silently send the traces there.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `.venv/bin/python scripts/verify_demo.py --faults` | Runs one live turn per fault and checks each one trips its catching layer (~4 min, a few cents) |
| 30 min before | `./scripts/run_services.sh` then `scripts/verify_demo.py` | Services up, prompts, datasets, evaluators active, queue present |
| 15 min before | `scripts/run_traffic.py --only conv-01 conv-02 conv-05 conv-07` | Fresh sessions, judge scores and queue items from today (conv-05 is the judge-vs-policy example in Act 7) |
| Tabs to open | Langfuse → Tracing, Sessions, Evaluators, Datasets → `handoff-routing` → Runs, Annotation queues | Each act starts on one of these |

Processes don't hot-reload. After any code or `.env` change, run `./scripts/run_services.sh stop`
and then start it again.

## Run it

```bash
.venv/bin/python scripts/run_scenario.py --list
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a     # the three-agent hero turn
.venv/bin/python scripts/run_scenario.py router-offline-tech --topology a2a    # a transfer held across turns
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology local   # same agents, one process
.venv/bin/python scripts/run_traffic.py --only conv-07                         # a billing question that turns into an outage
```

`run_scenario.py` prints each reply, the agent path, the trace id and the back-office effects.

**The hero turn** is "My home internet has been down since last night. I want money back for it."
It goes concierge → tech-support (confirms the 14-hour outage) → billing (peer hop: the oracle's
$2.67 credit) → the customer.

**What you see in Langfuse:** one trace, about 21 observations from three processes, a single root,
and the session on every row. Each `handoff` observation carries the payload that crossed the
boundary, the A2A task and context ids, and which headers linked it.

## Evaluation

Four layers, each catching what the others can't (SPEC §9):

| Layer | Where | Scores |
|---|---|---|
| Handoff | online, every `handoff` | `handoff-context-complete`, `handoff-payload-clean`, `handoff-succeeded` (code); `handoff-appropriate` (judge, Haiku 5.5) |
| Turn trajectory | online, every turn root | `handoff-loop`, `handoff-count` (code); `customer-resolved` (judge, Sonnet 5.5) |
| Conversation | session scores, from `run_traffic.py` | `turns-to-resolution`, `transfers`, `conversation-ended-by`, `resolution-correct` |
| Experiment | `run_experiment.py`, per item | `handoff-target-correct`, `path-match`, `resolution-correct` (ledger), `credit-amount-correct` (oracle), `outage-verified-first`, … |

How the online evaluators are wired:
- **Each online rule selects its observation by `metadata.role`** and runs only on production
  `support-turn` traces. Experiments (environment `sdk-experiment`) never trigger them.
- **An evaluator sees only the observation it matched,** so the transport writes onto each `handoff`
  what its evaluators need: the target's required fields and the agent roster.
- **Outcome scores read the shared ledger, not the reply,** so a confident reply about a credit that
  was never issued scores 0.
- **Transferred conversations go to the review queue.** `run_traffic.py` sends every conversation with
  a transfer to the **Transferred conversations** annotation queue, as a `SESSION` item.

## Single agent vs. multi-agent

One run each on the same items, prompts, models and tools, read back with `scripts/compare_runs.py`:

| | `single` | `local` | `a2a` |
|---|---|---|---|
| Routing: 40 single-turn requests, right outcome | 40/40 | 40/40 | 40/40 |
| Routing: cost / mean latency per item | $0.0177 · 5.5 s | $0.0213 · 10.0 s | $0.0217 · 10.5 s |
| Conversations: 12 multi-turn goals, resolved | 12/12 | 12/12 | 12/12 |
| Conversations: cost per conversation | $0.0555 | $0.0589 | $0.0664 |

On this desk, splitting the work buys no accuracy. It costs more, and almost all of the latency
comes from the extra agent turns; the A2A hop itself adds about half a second. The case for multi-agent
here is ownership (each team ships its own agent), and that brings the failure modes below.

## Seeded faults

| # | Fault | Switch | Caught by | Missed by |
|---|---|---|---|---|
| F1 | Misroute: credit requests skip tech-support | `--label fault-f1` | `handoff-appropriate` judge, `handoff-target-correct` | `customer-resolved` (0.92): billing recovered the hop |
| F2 | Context loss: `line_id` dropped from payloads | `HANDOFF_SLIM_CONTEXT=1` | `handoff-context-complete` names the field | the outcome: tech-support looked the line up itself |
| F3 | Ping-pong between specialists | `--label fault-f3` | `handoff-loop` (A>B>A, hop cap) | the per-hop judge: 3 of 4 hops look right |
| F4 | Broken propagation | `--linkage traceparent` | trace integrity: 3 roots, rows without session | quality scores; and online coverage drops on the remote hop |
| F5 | Remote agent down or slow | `BILLING_URL=http://127.0.0.1:9`, or `BILLING_FAULT_LATENCY_MS` with `HANDOFF_A2A_TIMEOUT_S` | `handoff` at ERROR, `handoff-succeeded` = false | nothing: the customer gets a graceful apology |

Measured 2026-11-20 with `scripts/verify_demo.py --faults` ([docs/FAULT_MATRIX.md](docs/FAULT_MATRIX.md)).

How to flip each fault:

```bash
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a --label fault-f1          # F1
HANDOFF_SLIM_CONTEXT=1 .venv/bin/python scripts/run_scenario.py router-offline-tech --topology a2a   # F2
.venv/bin/python scripts/run_scenario.py ambiguous-bill-and-slow --topology a2a --label fault-f3     # F3
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a --linkage traceparent     # F4
./scripts/run_services.sh stop && BILLING_FAULT_LATENCY_MS=8000 ./scripts/run_services.sh            # F5, slow billing
HANDOFF_A2A_TIMEOUT_S=5 .venv/bin/python scripts/run_scenario.py balance-quick --topology a2a        #     …now a timeout
./scripts/run_services.sh stop && ./scripts/run_services.sh                                          # back to normal
```

Fault labels apply per agent: under `fault-f1` only the concierge changes, and the others run
their production prompts. The fault prompts are the production prompts with only the faulty lines
changed, so comparing the two versions in Langfuse shows exactly what broke
([handoff/faults.py](handoff/faults.py)).

## The linkage matrix

```bash
.venv/bin/python scripts/linkage_matrix.py          # offline: deterministic stub agents, spans to files, ~25 s
.venv/bin/python scripts/linkage_matrix.py --live   # also on the project in .env, read back via the v2 API, ~2.5 min
```

| What crosses the wire | Traces | Roots | Remote rows with session | Use it for |
|---|---|---|---|---|
| nothing | 3 | 3 | 0/5 | never on purpose |
| `traceparent` only | 1 | **3** | 0/5 | a third-party agent (nothing internal leaks) |
| `traceparent` + baggage header, no `as_baggage` | 1 | 1 | **0/5** | never: looks fixed, isn't |
| `traceparent` + baggage, `propagate_attributes(as_baggage=True)` | **1** | **1** | **5/5** | synchronous handoffs (the default) |
| session id in A2A message metadata | 3 | 3 | 5/5 | asynchronous work that outlives the turn |

Measured on Langfuse Cloud with server-side `isRootObservation`; it matches the offline run in every
mode ([docs/LINKAGE_MATRIX.live.md](docs/LINKAGE_MATRIX.live.md)). Baggage is scoped to the one A2A
call, carries no PII, and never goes to a partner. The demo also turns off the Anthropic SDK's own
trace-header injection, which would otherwise send baggage to the model API.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Judges paused, `LLM_CONNECTION_MISSING` | An evaluator names its LLM connection by the connection's **name**, case-sensitively. This org's is `Anthropic` (not `anthropic`). A project key can't list organization connections. Set `HANDOFF_JUDGE_PROVIDER` and re-run `seed_evals.py --judges`; it prints each judge's status. |
| `verify_demo.py`: A2A service down | `./scripts/run_services.sh` (logs: `.run/billing.log`, `.run/tech-support.log`) |
| A code change has no effect on the services | They don't hot-reload: `./scripts/run_services.sh stop && ./scripts/run_services.sh` |
| `Prompt '…-label:fault-f1' not found` warnings | Expected: an agent without that label runs its production prompt |
| Traces go to the wrong project / 404s on read-back | Keys come only from the demo `.env`; check `GET /api/public/projects` with them |
| A credit is "already credited" | The ledger is per conversation; a new conversation starts clean. To wipe it: stop the services and delete `.run/ledger.sqlite`. |
| Online scores missing on a remote hop | Linkage not `baggage` (fault F4): remote rows lose the trace name the rules filter on |

## Layout

```
handoff/            the system: agents/ (registry, agent loop, A2A service handler), orchestrator.py (the hub),
                    transport.py (the handoff observation), linkage.py (what crosses the wire), a2a_client.py,
                    a2a_service.py (trace context in, task out), tools/ (fake back office, policy oracle, ledger),
                    prompts.py, faults.py, datasets.py, evals.py (experiment evaluators), conversation.py (simulator)
evaluators/         the code evaluators uploaded to Langfuse (stdlib Python)
scripts/            seed_*.py, run_services.sh, run_scenario.py, run_traffic.py, run_experiment.py,
                    compare_runs.py, linkage_matrix.py, verify_demo.py, show_trace.py
data/               customers, outages, KB, scenarios (synthetic; data/generate.py rebuilds them)
docs/               measured tables
tests/              oracle, ledger, evaluators (incl. the uploaded code evaluators), linkage modes
```

## Known limits

- **Small samples.** One run per topology, 40 routing items and 12 conversations: a one-item
  difference is noise.
- **Simulated customers.** `simulated-customer-satisfied` is a simulator's verdict, not a human's.
- **Not built yet:** dashboards, the prompt CI gate, a black-box partner agent (SPEC §16, phases 5–6).
- **Judges are probabilistic.** The F1 catch rests on `handoff-appropriate`, which `verify_demo.py`
  treats as a warning, not a failure, if it misses.

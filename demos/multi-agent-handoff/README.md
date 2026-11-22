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

It also runs the whole AI engineering loop on one bug: a bad routing prompt ships, the handoff judge
flags it in production, a reviewer labels the flagged handoffs, they become test cases, the fix goes
through a CI gate, and the dashboard shows the misroutes stop ([The loop on one bug](#the-loop-on-one-bug-f1)).

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (about 45 minutes)
- **Design and measured results:** [SPEC.md](SPEC.md)
- **Measured tables:** [linkage, live](docs/LINKAGE_MATRIX.live.md) ·
  [single vs. multi-agent](docs/TOPOLOGY_COMPARISON.md) ·
  [conversations](docs/CONVERSATION_COMPARISON.md) · [faults](docs/FAULT_MATRIX.md) ·
  [one pass round the loop](docs/LOOP_RUN.md)
- **CI gate setup:** [cicd/README.md](cicd/README.md)

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
.venv/bin/python -m pytest -q tests                        # 39 tests, no server or model calls
```

Seed the project. Every seeder is idempotent and reconciles drift, so re-running is safe.

```bash
.venv/bin/python scripts/seed_prompts.py --faults     # 4 agent prompts @production + fault variants @fault-f1 / @fault-f3
.venv/bin/python scripts/seed_datasets.py             # handoff-routing (40 items) + handoff-conversations (12)
.venv/bin/python scripts/seed_evals.py --judges --queue   # 2 code evaluators, 2 LLM judges, rules, 2 review queues
.venv/bin/python scripts/seed_dashboards.py           # 2 dashboards, 24 widgets (--check runs every widget query)
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
| 30 min before | `./scripts/run_services.sh` then `scripts/verify_demo.py` | Services up, prompts, datasets, evaluators active, queues and dashboards present |
| 30 min before, for Act 8 | `scripts/loop.py ship-bug` then `scripts/run_traffic.py --repeat 2` (~8 min) | Production runs the F1 bug, with a dashboard's worth of traffic and judge flags to start the loop from |
| 15 min before | `scripts/run_traffic.py --only conv-01 conv-02 conv-05 conv-07` | Fresh sessions, judge scores and queue items from today (conv-05 is the judge-vs-policy example in Act 7) |
| Tabs to open | Langfuse → Tracing, Sessions, Evaluators, Datasets → `handoff-routing` → Runs, Annotation queues, Dashboards → **Handoffs — quality** | Each act starts on one of these |
| After the demo | `scripts/loop.py status` | Production should hold the fix or the seeded prompt; `scripts/loop.py reset` if a rehearsal stopped half-way |

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

## The loop on one bug (F1)

The demo goes round the AI engineering loop once, the way an incident starts: **release 1.1 has
already shipped F1**. The concierge's production prompt is the "skip a hop" edit, so outage credits
go straight to billing. Each step is one command, and each label move is a real change to the
`concierge` prompt's labels in Langfuse.

```bash
.venv/bin/python scripts/loop.py ship-bug                     # 0  release 1.1: the F1 prompt becomes production
.venv/bin/python scripts/run_traffic.py --repeat 2            # 1  Trace: 24 simulated conversations (~6 min)
#                                                               2  Monitor: Dashboards → Handoffs — quality
.venv/bin/python scripts/flag_handoffs.py --simulate-reviewer # 3  Datasets: judge flags → review queue → labels
.venv/bin/python scripts/promote_to_dataset.py                #    labelled handoffs → handoff-routing items
.venv/bin/python scripts/loop.py fix                          # 4  Experiment: the fix, as concierge @candidate
.venv/bin/python scripts/prompt_gate.py --label candidate --compare production   # 5  Evaluate: the CI gate
.venv/bin/python scripts/judge_calibration.py                 #    does the judge agree with the reviewer?
.venv/bin/python scripts/loop.py promote                      # ⟳  Deploy: production → the fix (fires the CI gate)
.venv/bin/python scripts/run_traffic.py --repeat 2            # 1  again: the dashboard shows a new prompt row
```

What each step shows:
- **Monitor.** Production runs F1, and the `handoff-appropriate` judge flags every outage credit
  routed to billing. `customer-resolved` and the ledger stay green, because billing passes the
  request on and the credit comes out right. The dashboard splits every score by the **prompt
  versions the turn ran** (the trace version, for example `concierge v3 / tech-support v1 / billing v1`),
  so the bug is a row of its own.
- **Datasets.** `flag_handoffs.py` sends the concierge's first handoff of each flagged turn to the
  **Flagged handoffs** queue, plus an equal sample the judge passed, so the review can calibrate the
  judge in both directions. A reviewer labels `correct-handoff-target`. Label one live, and let
  `--simulate-reviewer` label the turn-1 items from the scenario's ground truth (say so).
  `promote_to_dataset.py` turns each label into a `handoff-routing` item linked to its trace.
- **Experiment and Evaluate.** `loop.py fix` writes the fix as a new version on top of the bug: keep
  "money questions skip a hop" and make the outage-credit exception explicit, so the version diff in
  Langfuse reads as a fix rather than a revert. The gate runs the dataset, now including the promoted
  items, on the a2a topology.
- **Deploy.** `loop.py promote` moves `production`. The agents fetch prompts by label (60 s cache), so
  nothing redeploys. With the Langfuse automation set up, the label change also runs the GitHub gate
  ([cicd/README.md](cicd/README.md)).

Measured on 2026-11-22, one pass ([docs/LOOP_RUN.md](docs/LOOP_RUN.md)):

| | Bug in production (concierge v3) | Fix in production (v4) |
|---|---|---|
| Gate, `routing-accuracy` (56 items, a2a) | 0.61: blocked (`fault-f1`, 40 items: 0.65, loop rate 0.20) | **1.00**: passed |
| Gate, `resolution-correct` | 1.00 (the blind spot) | 0.97 |
| Gate, cost per item | $0.0285 | $0.0233 (the "save a hop" edit cost 22% more) |
| Production: concierge's first handoff flagged by the judge | 13 of 37 turns | 1 of 38 |
| Production: concierge → billing / → tech-support | 24 / 13 | 15 / 23 |
| Production: turns that looped | 8 of 81 | 0 of 82 |
| Production: `customer-resolved`, ledger outcome | 0.82, 24/24 right | 0.77, 24/24 right |
| Review | 13 flagged + 13 control queued; 16 labelled, promoted to `handoff-routing` (40 → 56 items); judge vs. reviewer 16/16 | |

`loop.py status` shows which concierge version holds which label. `loop.py reset` puts production
back on the seeded prompt, and `loop.py promote --version N` rolls back to any version.

### Dashboards

`scripts/seed_dashboards.py` seeds two dashboards as code (unstable dashboards API, reconciled by
name). Every widget is scoped to environment `production`, so experiments and the judges' own model
calls stay out.

- **Handoffs — quality:** the handoff judge as a pivot by prompt versions and over time; the F1 blind
  spot (`customer-resolved` by prompt versions); handoffs to billing and to tech-support over time;
  the payload, loop and failed-handoff checks (F2, F3, F5); and spans without a session (the F4
  tripwire).
- **Handoffs — agents:** LLM cost, tokens and p95 latency per agent; p95 handoff and turn latency; cost
  by model; errors by observation.

A widget can't group by a metadata key, so "handoffs by target" is one widget per target, using a
metadata filter. "By prompt version" uses the trace version, which the orchestrator sets.

### The CI gate

`scripts/prompt_gate.py` runs `handoff-routing` on the a2a topology with the label under review, and
blocks on `routing-accuracy`, `loop-rate`, `resolution-rate`, `credit-accuracy`,
`outage-verified-rate`, `context-complete-rate` and `items-evaluated`
([cicd/thresholds.json](cicd/thresholds.json)). Every hard metric is deterministic; no judge decides a
release. [`.github/workflows/handoff-prompt-gate.yml`](../../.github/workflows/handoff-prompt-gate.yml)
runs it in CI on a Langfuse prompt automation event, with the two agent services started on the
runner. Setup: [cicd/README.md](cicd/README.md).

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
| A dashboard widget is empty | `scripts/seed_dashboards.py --check` runs every widget's query; most are production-only, so run `run_traffic.py` first |
| Production still runs the F1 bug after a rehearsal | `scripts/loop.py status`, then `loop.py promote` (the fix) or `loop.py reset` (the seeded prompt) |
| The gate aborts: "has no version labelled …" | The gated prompt must carry that label (`loop.py fix` creates `candidate`); otherwise the gate would grade production |

## Layout

```
handoff/            the system: agents/ (registry, agent loop, A2A service handler), orchestrator.py (the hub),
                    transport.py (the handoff observation), linkage.py (what crosses the wire), a2a_client.py,
                    a2a_service.py (trace context in, task out), tools/ (fake back office, policy oracle, ledger),
                    prompts.py, faults.py, datasets.py, evals.py (experiment evaluators), conversation.py (simulator)
evaluators/         the code evaluators uploaded to Langfuse (stdlib Python)
scripts/            seed_*.py (prompts, datasets, evals, dashboards), run_services.sh, run_scenario.py, run_traffic.py,
                    run_experiment.py, compare_runs.py, linkage_matrix.py, verify_demo.py, show_trace.py; the loop:
                    loop.py (label moves), flag_handoffs.py, promote_to_dataset.py, judge_calibration.py, prompt_gate.py
cicd/               the gate's thresholds and CI setup (the workflow is .github/workflows/handoff-prompt-gate.yml)
data/               customers, outages, KB, scenarios (synthetic; data/generate.py rebuilds them)
docs/               measured tables
tests/              oracle, ledger, evaluators (incl. the uploaded code evaluators), linkage modes
```

## Known limits

- **Small samples.** One run per topology, 40 routing items and 12 conversations: a one-item
  difference is noise.
- **Simulated customers.** `simulated-customer-satisfied` is a simulator's verdict, not a human's.
- **Not built yet:** a black-box partner agent (SPEC §16, phase 6).
- **The CI gate has not run on GitHub yet.** GitHub runs a dispatch-triggered workflow only from the
  default branch, and the Langfuse automation is a UI step. The gate script ran live, locally.
- **The simulated reviewer knows the scenario.** It labels turn-1 items from the conversation goal's
  ground truth, so judge calibration against it measures the judge on the easy cases. Later-turn
  items stay in the queue for a person.
- **Judges are probabilistic.** The F1 catch rests on `handoff-appropriate`, which `verify_demo.py`
  treats as a warning, not a failure, if it misses.

# Multi-agent handoff demo (A2A)

A Northwind Mobile (fictional) support desk where agents **hand work to each other over
A2A 1.0**, traced in Langfuse. It answers three questions from the public training:
what a handoff looks like in a trace, how to keep a handoff in one trace and one session,
and how to evaluate a system with handoffs. Design: [SPEC.md](SPEC.md).

> **Status:** Phases 0–3 are built and run on Langfuse Cloud: the linkage harness (verified
> live), the agents, the `single` / `local` / `a2a` topologies, managed prompts, both
> datasets, and the experiments with their evaluators. Phase 4 is in progress: the online
> code evaluators, the simulated-customer conversations, session scores and the review
> queue are live. The LLM judges, most seeded faults, the dashboards, the CI gate and the
> demo script are not built yet.

## The agents

| Agent | Runs as | Owns |
|---|---|---|
| `concierge` | in the orchestrator process | the front door; routes by calling `transfer_to_*` / `ask_*` tools |
| `tech-support` | A2A service :9311 | connectivity, outages, diagnostics; delegates credits to billing (peer hop) |
| `billing` | A2A service :9310 | invoices, plans, credits (the policy oracle decides the amount) |

The agents decide the handoffs. Nothing in code routes requests. A **transfer** hands
the conversation over: later customer turns go straight to the specialist on its open
A2A task until it calls `hand_back`. The same agents also run in one process (`local`)
and as one agent with every tool (`single`), for a like-for-like comparison.

## Run it

```bash
cd demos/multi-agent-handoff
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # add LANGFUSE_* for this demo's project and ANTHROPIC_API_KEY
```

Seed the project once (both are idempotent):

```bash
.venv/bin/python scripts/seed_prompts.py     # concierge, tech-support, billing, single-agent @production
.venv/bin/python scripts/seed_datasets.py    # handoff-routing: 40 items, expected values from the oracle
```

```bash
./scripts/run_services.sh                                   # billing + tech-support A2A services
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology local
.venv/bin/python scripts/run_scenario.py router-offline-tech --topology a2a --linkage traceparent
.venv/bin/python scripts/run_scenario.py --list
./scripts/run_services.sh stop
```

**Without a Langfuse server:** set `HANDOFF_CAPTURE_DIR=/some/dir` for the services and
the scenario. Every process then writes the spans its SDK would have sent to
`<dir>/<service>.jsonl`, and `scripts/show_trace.py <dir>` prints them as trace trees.

The back office is one shared ledger (`.run/ledger.sqlite`) that every agent in every
process reads and writes, keyed by conversation. A credit billing issues on
tech-support's peer hop is what billing reads back on the next turn.

## Single agent vs. multi-agent (question 3)

```bash
.venv/bin/python scripts/run_experiment.py --topology single
.venv/bin/python scripts/run_experiment.py --topology local
.venv/bin/python scripts/run_experiment.py --topology a2a    # services running
```

Each run puts the 40 `handoff-routing` items through one architecture, with the same
prompts, models and tools. Compare the runs in Langfuse under Datasets → `handoff-routing`.
The outcome scores (`resolution-correct`, `credit-amount-correct`) and the process score
(`outage-verified-first`: no credit decision before an outage check, in any agent) apply to
all three runs. The routing and trajectory scores (`handoff-target-correct`, `path-match`,
`handoff-context-complete`, `unnecessary-handoff`) exist only where there are handoffs.
The evaluators read the ledger, not the reply, so a confident reply about a credit that
was never issued scores 0. `scripts/compare_runs.py --since <time>` reads the runs back
from Langfuse and prints the comparison, cost per item included.

Measured on Cloud (one run each, 40 items): every topology reached the right outcome on
every item. `single` cost $0.0177 per item at 5.5 s mean latency; `local` $0.0213 at
10.0 s; `a2a` $0.0217 at 10.5 s. The extra agents cost about 20% and double the latency,
and the A2A hop itself adds about half a second. Full table:
[docs/TOPOLOGY_COMPARISON.md](docs/TOPOLOGY_COMPARISON.md); discussion in [SPEC.md §9](SPEC.md).

## Online evaluation and production traffic

```bash
.venv/bin/python scripts/seed_evals.py --queue        # code evaluators + rules, score configs, review queue
.venv/bin/python scripts/seed_evals.py --judges       # LLM judges (needs an Anthropic LLM connection)
.venv/bin/python scripts/run_traffic.py               # 12 simulated conversations as production traffic
.venv/bin/python scripts/run_experiment.py --topology a2a --dataset conversations
```

| Evaluator | Kind | Runs on | Catches |
|---|---|---|---|
| `handoff-context-complete` (+ `handoff-payload-clean`) | code, in Langfuse | every `handoff` | a handoff missing a field its target needs (fault F2), or carrying a card number |
| `handoff-loop` (+ `handoff-count`) | code, in Langfuse | every turn root | ping-pong, repeated hops, the hop cap (fault F3) |
| `handoff-appropriate` | LLM judge (Haiku 5.5) | every `handoff` | the wrong agent for the request, read against the roster (fault F1) |
| `customer-resolved` | LLM judge (Sonnet 5.5) | every turn root | a reply that leaves the customer without an answer or a next step |

Each rule selects its observation by `metadata.role` and runs only on production
`support-turn` traces, so experiments never trigger them. A judge sees only the
observation it matched, so the transport writes onto each `handoff` what its evaluators
need: the target's required fields and the agent roster.

`run_traffic.py` drives each conversation with a simulated customer (Haiku 5.5: a persona,
a goal, what it knows). Every customer message is a `support-turn` trace and the
conversation is one session. At the end it writes conversation-level scores on the
session (`turns-to-resolution`, `transfers`, `conversation-ended-by`, `resolution-correct`
against the ledger; Langfuse has no session-target judge). It also sends every conversation
with a transfer to the **Transferred conversations** annotation queue as a `SESSION` item.
On the first full run, every turn, handoff and session got its scores, and the queue got
all 12 sessions.

## The linkage matrix (question 2)

```bash
.venv/bin/python scripts/linkage_matrix.py          # no server, no LLM key; ~25 s
.venv/bin/python scripts/linkage_matrix.py --live   # also against the project in .env; ~2.5 min
```

The script runs one turn across three processes for each linkage mode. Measured results
and the recommendation for each mode are in [SPEC.md §6](SPEC.md). The live run on
Langfuse Cloud matched the offline table in every mode
([docs/LINKAGE_MATRIX.live.md](docs/LINKAGE_MATRIX.live.md)). In short:

- **`traceparent` only:** one trace id, but a logical root per process.
- **`traceparent` + a baggage header, without `propagate_attributes(as_baggage=True)`:**
  one root, but no session on remote rows.
- **`baggage`:** one root, with session and user everywhere.
- **`session`:** separate traces in one session, for async work.

## Tests

```bash
.venv/bin/python -m pytest -q tests   # oracle, ledger, evaluators (incl. the uploaded code evaluators), linkage modes
```

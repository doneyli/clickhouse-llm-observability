# Multi-agent handoff demo (A2A)

A Northwind Mobile (fictional) support desk where agents **hand work to each other over
A2A 1.0**, traced in Langfuse. It answers three questions from the public training:
what a handoff looks like in a trace, how to keep a handoff in one trace and one session,
and how to evaluate a system with handoffs. Design: [SPEC.md](SPEC.md).

> **Status:** Phases 0–2 are built and run end to end with real models: the linkage
> harness, the agents, and the `local` and `a2a` topologies. The evaluators, datasets,
> experiments, seeded faults, CI gate, and demo script (Phases 3–6) are not built yet.
> Everything so far has run with spans captured to files. It has not been run against a
> Langfuse server yet.

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

## The linkage matrix (question 2)

```bash
.venv/bin/python scripts/linkage_matrix.py   # no server, no LLM key; ~25 s
```

The script runs one turn across three processes for each linkage mode. Measured results
and the recommendation for each mode are in [SPEC.md §6](SPEC.md). In short:

- **`traceparent` only:** one trace id, but a logical root per process.
- **`traceparent` + a baggage header, without `propagate_attributes(as_baggage=True)`:**
  one root, but no session on remote rows.
- **`baggage`:** one root, with session and user everywhere.
- **`session`:** separate traces in one session, for async work.

## Tests

```bash
.venv/bin/python -m pytest -q tests   # credit oracle + the five linkage modes
```

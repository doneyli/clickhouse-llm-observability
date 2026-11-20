# Agent trajectory: did it take the right steps?

Companion to [story arc 5](disputes.md). `dispute-opened` tells you a dispute
failed. The trajectory scores tell you which step went wrong, offline against a
gold path and online with no gold at all.

A correct dispute takes three steps, and each one depends on the one before:

```
list_accounts → get_recent_transactions(days ≥ charge age) → [customer confirms] → open_dispute(expected charge)
```

The agent picks every tool call itself (LangGraph, `bind_tools`), so the path is a
real decision, not a fixed pipeline.

## Where the trajectory lives

Each turn's root `agent` observation, `northwind-assistant`, carries the ordered
tool calls in `metadata.tool_call_history` (JSON text, written by
`northwind/agent.py`):

```json
[{"tool": "list_accounts", "args": {}, "ok": true, "returned": ["4417", "ACC-1001-01", "ACC-1001-02"]},
 {"tool": "get_recent_transactions", "args": {"account_id": "ACC-1001-01", "days": 30}, "ok": true,
  "returned": ["ACC-1001-01", "TX-88071", "TX-88090", "TX-88101", "TX-88116", "TX-88117", "TX-88120"]}]
```

`args` are what the tool actually ran with, defaults included. That matters here:
in release 1.5.0 the model never asked for a window, and the 30 came from the
code default. `returned` lists the ids the result handed the agent, which is
what a later action may target.

Why the root observation: an observation-level evaluator sees one observation,
never its children. A generation's `tool_calls` covers only the calls that one
model response requested, so it cannot see a multi-step path. Trace-level
evaluators no longer produce results on Langfuse Cloud since the v4 cutover, so
the path has to sit on one observation.

## Offline: the gold path (experiments)

Every `northwind-disputes-v1` item carries its gold path in
`metadata.expected_trajectory` (`scripts/seed_datasets.py`):

```json
{"steps": ["list_accounts", "get_recent_transactions", "open_dispute"],
 "args": {"get_recent_transactions": {"days_at_least": 41},
          "open_dispute": {"transaction_id_in": ["TX-87950"]}},
 "forbidden": ["block_card", "schedule_callback"]}
```

`run_experiment.py` concatenates both turns of a confirm-first conversation into
one path, and `evals.trajectory` scores it:

| Score | Passes when |
|---|---|
| `trajectory-order` | The required steps appear in order. Other calls in between are allowed. |
| `trajectory-args` | The lookup before the dispute reached back to the charge, and every dispute is on the expected charge. Fraction of checks passed. |
| `trajectory-no-forbidden` | No blocked card and no hand-off to a human. |
| `trajectory-match` | All three. The comment shows the actual path next to the gold one. |
| `trajectory-steps` | Not a pass/fail: tool calls in the run, for path-length comparisons by release or model. |

## Online: the policy check (production, no gold)

`scripts/seed_trajectory_eval.py` creates a Python **code evaluator**,
`trajectory-policy`, whose source is `northwind/trajectory_policy.py` verbatim,
and a rule that runs it on every answered production `northwind-assistant`
observation. It runs inside Langfuse, with no model call, and checks rules that
any correct path follows:

| Score | When | Passes when |
|---|---|---|
| `trajectory-steps` | Every turn | Not a pass/fail: tool calls in the turn. The comment is the path. |
| `trajectory-redundant-calls` | Every turn | Count of repeated identical calls (same tool, same arguments). |
| `trajectory-grounded-actions` | Turns that call `open_dispute` or `block_card` | Every action targets an id the agent had already seen: returned by an earlier call in the turn, or in the conversation so far. An invented transaction id fails. |
| `trajectory-dispute-lookback` | Dispute turns that looked transactions up | The widest lookup covered the 60-day dispute window (KB-102). |

`trajectory-dispute-lookback` would have flagged release 1.5.0 on its first
dispute, with no dataset and no judge.

## Run it

```bash
.venv/bin/python scripts/seed_datasets.py --dataset disputes      # gold paths on the 10 items
.venv/bin/python -m unittest tests.test_trajectory                  # the evaluator file is uploaded as-is
.venv/bin/python scripts/seed_trajectory_eval.py                    # code evaluator + rule
# before / after experiments: same prompt (baseline = v1), only the release differs
NORTHWIND_RELEASE=assistant-1.5.0 NORTHWIND_TX_DEFAULT_DAYS=30 .venv/bin/python scripts/run_experiment.py \
  --dataset northwind-disputes-v1 --prompt-label baseline --run-name "trajectory · release 1.5.0 · lookback 30d · {version} · customer confirms"
NORTHWIND_RELEASE=assistant-1.5.1 NORTHWIND_TX_DEFAULT_DAYS=60 .venv/bin/python scripts/run_experiment.py \
  --dataset northwind-disputes-v1 --prompt-label baseline --run-name "trajectory · release 1.5.1 · lookback 60d · {version} · customer confirms"
# live traffic for the online rule: 4 conversations per release
NORTHWIND_RELEASE=assistant-1.5.0 NORTHWIND_TX_DEFAULT_DAYS=30 .venv/bin/python scripts/generate_traffic.py --scenario disputes --prompt-label baseline --n 4 --seed 7
NORTHWIND_RELEASE=assistant-1.5.1 NORTHWIND_TX_DEFAULT_DAYS=60 .venv/bin/python scripts/generate_traffic.py --scenario disputes --prompt-label baseline --n 4 --seed 7
```

A run name can only be used once: re-running with the same name appends to the
old run, so add a suffix such as `· rehearsal`.

## Measured

Langfuse Cloud US, 2026-11-19, prompt v1 (`baseline`), `claude-sonnet-4-6`,
same MCP server for both releases.

**Experiments**, `northwind-disputes-v1` (10 items, EN + ES: 8 charges 38-52 days
old, 2 recent controls). Datasets → `northwind-disputes-v1` → select both runs →
Compare.

| Run | dispute-opened | trajectory-match | trajectory-order | trajectory-args | trajectory-no-forbidden | trajectory-steps (avg) |
|---|---|---|---|---|---|---|
| [`trajectory · release 1.5.0 · lookback 30d · v1 · customer confirms`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/df19aee4-49ce-4da4-832b-408da79143ad) | 0.20 | 0.20 | 0.20 | 0.20 | 1.00 | 2.4 |
| [`trajectory · release 1.5.1 · lookback 60d · v1 · customer confirms`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/5b283d40-5e38-4a45-9285-f2aef3a0c5f4) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 3.8 |

Read the comments, not only the averages:

- **1.5.0 fails on an argument, not a missing tool.** Every older-charge item
  reads `actual: list_accounts → get_recent_transactions(days=30) | expected:
  list_accounts → get_recent_transactions(days≥41) → open_dispute(TX-87950)`,
  and `trajectory-args` says `FAIL: transaction lookup 30 days, the charge is 41
  days old`. The agent called the right tools in the right order. An order-only
  or set-based trajectory check would report "missing open_dispute"; the
  argument check names the cause.
  [Example trace (GADGETSTORE ONLINE)](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/824bd691f648c71a4ed290c91336f370).
- **No forbidden step in either release.** The 1.5.0 agent did not block a card
  or book a callback. It told the customer the charge was not there.
- **1.5.1 passes, with a longer path: 3.8 steps for 3 required.** Five of the ten
  items asked the customer to confirm first. On the customer's "yes", four of
  those five ran `list_accounts` again, and three also re-ran
  `get_recent_transactions`, before opening the dispute. The next turn's history
  carries the text of the agent's answer, not its tool results, so the agent has
  to look things up again.
  [Example: ELECTROMART, 5 calls over 2 turns](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/a0fe5446a8ad4368e89127feb4819bf5).
  `dispute-opened` cannot see this cost; `trajectory-steps` can.

**Live traffic**, `trajectory-policy` code evaluator (version 2), the same 4
conversations per release (`--n 4 --seed 7`), tag `scenario:disputes`:

| | 1.5.0 · v1 (30 d) | 1.5.1 · v1 (60 d) |
|---|---|---|
| Turns scored | 4 | 6 (2 conversations confirmed first) |
| `trajectory-dispute-lookback` (turns that ended without a dispute) | **0 of 3 pass**, all `widest transaction lookup 30 days` | **2 of 2 pass**, 60 days, then asked to confirm |
| `trajectory-grounded-actions` | 1 of 1 | 4 of 4, including both confirm turns |
| `trajectory-redundant-calls` | 0 | 0 |

Traces → filter score `trajectory-dispute-lookback` = false → the 1.5.0 turns.
[Before: TRAVELHUB, "no encontré ningún cargo … últimos 30 días"](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/981ea32408715051836d479c7d3a6c53) ·
[After: same request, dispute opened on TX-77150](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/e7850b4c38cdb8e9a6ece3697cc58024).
Each code-evaluator execution is a trace in the `langfuse-code-eval` environment,
for debugging.

> **Candour.** Small samples: 10 items and 4 conversations per release, so read
> the direction. Version 1 of the evaluator judged the lookback on every dispute
> turn and failed "STREAMFLIX charged me twice this week", where a 7-day lookup
> found the duplicate and opened the dispute. Version 2 judges only turns that
> ended without a dispute. A confirm-first turn on a short window the customer
> asked for ("this week") would still fail it, so read the comment. The online
> grounding check sees the conversation through `metadata.context`, which keeps
> the last 4 messages. The gold path allows extra calls between required steps;
> `trajectory-steps` is where extra calls show up.

# Story arc 5 — Disputes leak to humans

Drop-in section for the DEMO_SCRIPT "Story arc" (same Symptom → Pinpoint → Root
cause → Fix → Verify shape as Issues 1–4). Every number below was measured in the
Langfuse Cloud project on 2026-10-08 (13:11–13:17 UTC) from the runs and traces
linked here; none are estimates. **Prompt: v1 in every run and trace**. It was the
`production` label then and is labelled `baseline` now, so before and after differ
only in the release and its transaction lookback.

### Issue 5 — Disputes leak to humans (containment, cost per contact)
- **Symptom.** *Dispute self-service rate by trace version*: `assistant-1.5.0 ·
  prompt v1` is the low bar, **0.20**. *Outcome mix* shows `dispute-unresolved`,
  *Failure modes* shows `dispute-not-resolved`, and those turns deliver **USD 0**
  in *Value delivered*. In this sample, 8 of 10 customers who asked for a dispute
  left without one, and each of them will call the contact centre. The engineering
  metrics did not catch it: the judges gave the hero answer `faithfulness` 1.0
  and `banking-compliance` 1.0.
- **Pinpoint.** Traces → filter score `failure-mode` = `dispute-not-resolved` (or
  `dispute-resolved` = 0), version `assistant-1.5.0 · prompt v1`, tag
  `scenario:disputes` → open the hero trace (GADGETSTORE ONLINE, English). The
  answer says *"I don't see a GADGETSTORE ONLINE transaction in your last 30
  days"*, cites **KB-102** (60 days), and then suggests a *different* charge
  (UNKNOWN MERCHANT LAGOS). Walk down the tree:
  1. Tool `get_recent_transactions`, **input** `{"account_id": "ACC-1001-01"}`.
     The model never asked for a window.
  2. `mcp-client: get_recent_transactions`, **input** `"days": 30`. The 30 came
     from the code (the tool's default), not from the model.
  3. `mcp-server: get_recent_transactions`, in the core-banking service and the
     same trace (linked by W3C trace context in MCP `_meta`). **Output**
     `"window_days": 30, "from_date": "2026-09-08"` and 6 transactions. The
     GADGETSTORE charge is dated 2026-08-28, 41 days old, so the server never
     returned it.
  4. The `dispute-resolved` score comment says `open_dispute succeeded=False;
     transaction lookback windows=[30]`.

  KB-102 lets customers dispute **within 60 days** of the statement date. The tool
  looked back only 30.
- **Root cause.** The transaction lookback (30 days) is shorter than the dispute
  window (60 days). Most disputes are about recent charges: the duplicate
  STREAMFLIX and UNKNOWN MERCHANT LAGOS charges are 2–4 days old and were
  disputed fine in every release. Only customers disputing a 31–60-day-old charge
  were turned away, so nobody noticed. The answer was faithful to what the tool
  returned, which is why the judges passed it.
- **Fix — in code, not in the prompt.** Release **1.5.1**: the transaction
  lookback default now equals the dispute window, 60 days (`TX_DEFAULT_DAYS` in
  `northwind/agent.py`). The prompt stays production **v1**. Verified across the
  MCP boundary: the `mcp-server: get_recent_transactions` span now shows `"days":
  60` in and `"window_days": 60, "from_date": "2026-08-09"` out.
- **Verify.**
  - *Experiments*, dataset `northwind-disputes-v1`: 10 items, the 4 customers, EN
    + ES, 8 older-charge disputes plus 2 recent controls. Evaluator
    `dispute-opened` = `open_dispute` called **and** a `DSP-######` case id in the
    answer. Datasets → `northwind-disputes-v1` → select the two runs → Compare.

    | run | dispute-opened | language-match |
    |---|---|---|
    | `release 1.5.0 · lookback 30d · v1 · customer confirms` | **0.20** (2/10 — the 2 controls only) | 1.00 |
    | `release 1.5.1 · lookback 60d · v1 · customer confirms` | **1.00** (10/10, all on the expected transaction) | 1.00 |

    The 1.5.0 comments read `no transaction found in the answer (expected
    TX-87950) … answer says 'last 30 days'` (*'últimos 30 días'* in Spanish).
    The single-turn runs (`… · v1` without "customer confirms") give **0.20 →
    0.50**. In the other five items the 1.5.1 agent found the right charge and
    asked "shall I open it?" (see the candour note).
  - *Live traffic by release*, tag `scenario:disputes`, 10 conversations per
    release, prompt v1:

    | | 1.5.0 · v1 (30 d) | 1.5.1 · v1 (60 d) |
    |---|---|---|
    | conversations that ended with a dispute opened | **2 / 10** (0 / 8 older charges) | **10 / 10** (8 / 8), all on the right transaction |
    | dispute self-service rate (avg `dispute-resolved`, per turn) | 0.20 (n = 10) | 0.67 (n = 15) |
    | `dispute-unresolved` outcomes | 8 — every older-charge dispute | 5 — all confirm-first turns, each followed by an opened dispute |
    | value delivered on dispute turns (`value-usd`) | USD 13.00 | USD 65.00 |

  - Hero traces:
    [BEFORE — 1.5.0, GADGETSTORE, "not in your last 30 days"](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/a4e261601983c78e3fd00fcebacec479?observation=a60d0396885d223a)
    (opens on the MCP server span) ·
    [AFTER — 1.5.1, same request, dispute DSP-725272 opened on TX-87950](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/1be9e4f473f5f7a0e939f77f47e5341a) ·
    [AFTER — a confirm-first conversation in Spanish (2 turns, DSP-656121)](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/sessions/sess-d52d5b0fd6).
  - Runs:
    [1.5.0 · customer confirms](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/58fd32ff-6cc5-4c94-914a-15748fac446e) ·
    [1.5.1 · customer confirms](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/a0d3edd2-ab96-43c0-a61e-5dc5185ee746) ·
    [1.5.0 single-turn](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/39298ed9-9c01-48f4-9cfc-5e58eadb18bd) ·
    [1.5.1 single-turn](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/aa04a458-fca5-4ab4-8409-3ed9919f0b42).
- **Land.** One trace showed a business leak (disputes going to the contact
  centre) was caused by a code default two services away from the chat window,
  and the release that fixed it took this sample from 2 of 10 disputes opened to
  10 of 10.

> **Candour — say it out loud.** The samples are small: 10 conversations and 10
> dataset items per release, so read the direction, not the decimals. The
> per-turn bar for 1.5.1 reads **0.67**, not 1.00, because of the *confirm-first*
> behaviour. In 5 of the 8 older-charge conversations, the agent showed the
> charge and asked *"shall I open it?"*, and opened the dispute on the
> customer's "yes" in the next turn. That confirming turn scores
> `dispute-resolved` = 0 and is classified `dispute-unresolved`. Confirming
> before an irreversible action is arguably the right behaviour (v5 rule 3 asks
> for it), so judge this issue on the conversation: Sessions, 10 of 10. The
> dashboard bar counts every turn with that version: `assistant-1.5.1 · prompt
> v1` was 0.63 (n = 19) at the time of writing. Turns that only *mention* a
> dispute, such as *"I want to complain about how long my last dispute took"*,
> also count as dispute requests, so filter tag `scenario:disputes` for the clean
> comparison.
>
> **Scoring refined after this run (new traffic only):** a confirm-first turn is now
> `dispute-awaiting-confirmation` (contained, not a failure, no `dispute-resolved`
> score), and questions that only mention a past dispute no longer count as
> dispute requests. The numbers above were measured before that change.

**Reproduce** (the release is set per process; the MCP server does not change). Use
`--prompt-label baseline` (= prompt v1) so before and after run on the same prompt;
`production` has since moved to a later version.

```bash
# before / after traffic — 10 conversations each, EN + ES, 2 recent controls
NORTHWIND_RELEASE=assistant-1.5.0 NORTHWIND_TX_DEFAULT_DAYS=30 .venv/bin/python scripts/generate_traffic.py --scenario disputes --prompt-label baseline
NORTHWIND_RELEASE=assistant-1.5.1 NORTHWIND_TX_DEFAULT_DAYS=60 .venv/bin/python scripts/generate_traffic.py --scenario disputes --prompt-label baseline
# experiments (dataset: .venv/bin/python scripts/seed_datasets.py --dataset disputes)
NORTHWIND_RELEASE=assistant-1.5.0 NORTHWIND_TX_DEFAULT_DAYS=30 .venv/bin/python scripts/run_experiment.py \
  --dataset northwind-disputes-v1 --prompt-label baseline --run-name "release 1.5.0 · lookback 30d · {version} · customer confirms"
NORTHWIND_RELEASE=assistant-1.5.1 NORTHWIND_TX_DEFAULT_DAYS=60 .venv/bin/python scripts/run_experiment.py \
  --dataset northwind-disputes-v1 --prompt-label baseline --run-name "release 1.5.1 · lookback 60d · {version} · customer confirms"
# add --no-confirm for the single-turn variant (and drop "· customer confirms" from the run name).
# The four runs above already exist for prompt v1: re-running with the same name appends to them.
# Use a new run name, for example add "· rehearsal".
```

The simulated customer confirms only when the agent has shown the **expected**
charge, by its id or amount. It never says "yes" to a different charge, so a
wrong suggestion such as the 1.5.0 agent's UNKNOWN MERCHANT LAGOS is never turned
into a dispute. A new run needs a new run name, because reusing one appends to
the old run. Transaction dates are relative to the day the MCP server starts, so
the dates move between days and the ages (38–52 days) do not.

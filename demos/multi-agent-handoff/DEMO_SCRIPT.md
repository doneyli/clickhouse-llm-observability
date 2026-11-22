# Demo script: "The handoff nobody saw"

About 50 minutes of demo inside a 60-minute meeting. The audience builds, or is about to build,
agents that hand work to each other, often across teams and services. Every act opens with the
question they are already asking, shows it live, and ends on the point to land and a question back
to the room. For a 30-minute slot, cut acts 6 and 7. For an audience that already runs evals, keep
Act 8 and cut acts 4–6 instead.

**Before you start** (see the README's *Before a demo*):

- Run `.venv/bin/python scripts/verify_demo.py --faults` the day before. Every line should PASS.
- 30 minutes before, run `./scripts/run_services.sh` and `scripts/verify_demo.py`. Then run
  `scripts/run_traffic.py --only conv-01 conv-02 conv-05 conv-07` so today's sessions, judge scores
  and queue items exist. conv-05 (a 2.5-hour blip, no credit) is the one Act 7 uses.
- For Act 8, 30 minutes before: `scripts/loop.py ship-bug`, then `scripts/run_traffic.py --repeat 2`
  (about 8 minutes). Production now runs the F1 bug, with a dashboard's worth of traffic and judge
  flags. Rehearsed, check that `scripts/loop.py status` says production = the F1 bug.
- Open a terminal in `demos/multi-agent-handoff`.
- Open Langfuse on the `multi-agent-handoff` project in tabs: **Tracing**, **Sessions**,
  **Evaluators**, **Datasets → handoff-routing → Runs**, **Annotation queues**, **Dashboards →
  Handoffs — quality**, **Prompts → concierge**.
- After the meeting, run `scripts/loop.py status`. Production should hold the fix; run
  `scripts/loop.py reset` if you stopped half-way.

Northwind Mobile is fictional, and every customer, outage and invoice is synthetic. Say so once,
up front.

---

## 0 · The question (2 min)

> "When one agent hands a customer to another, three things usually go dark. What did the
> handoff actually carry? Is it still the same trace and the same conversation on the other
> side of the service boundary? And how do you know the handoff was right, not just that the
> final answer sounded good? That's the next 45 minutes."

Name the desk in one breath:
- **Agents:** a concierge at the front door, plus tech-support and billing, each a separate A2A
  service owned by a different team.
- **Models:** Haiku 5.5 routes, Sonnet 5.5 does the specialist work.
- **Routing:** the models choose every handoff with tools; no code routes anything.

## 1 · One customer turn, three agents, one trace (8 min)

Terminal:

```bash
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a
```

While it runs (about 15 seconds), read the turn aloud: *"My home internet has been down since last
night. I want money back for it."* It prints three customer turns. On the first, the path is
`concierge>tech-support>billing`, the reply has a $2.67 credit, and you get a trace id.

Open the first turn's trace in Langfuse (Tracing → search the trace id):

- **The tree.** `support-turn` → `concierge` → a `handoff` → `tech-support` → `check_outage`,
  `run_line_diagnostics` → a second `handoff` → `billing` → `compute_credit`, `issue_credit`.
  Three processes, one tree, one root.
- **Open a `handoff`.** The input is what crossed the boundary: target, kind (`transfer` or
  `delegate`), reason, message, payload. The metadata has the A2A task and context ids, and
  `linked_by: baggage + traceparent`: which headers actually arrived.
- **The second `handoff` is a peer hop.** Tech-support asked billing directly, from inside its own
  service. Same trace.
- **The amount is decided by code, not by the model.** `compute_credit` is a policy oracle, and
  `issue_credit` refuses any other amount.

Sessions → the session for this conversation: all three turns. On turn 2 the customer asks whether
the credit will show on the bill, and billing reads it back from the same back-office record that
tech-support's hop wrote.

> Land: a handoff is a first-class step in the trace, with what it carried and how it was linked.
> Ask: *"When one of your agents calls another today, what do you see on the far side?"*

## 2 · Keeping the pieces together across services (8 min)

> "A2A doesn't define tracing. Its guidance is ordinary W3C trace context, and most teams stop at
> `traceparent`. Here is what that buys you."

Show the measured matrix (README, *The linkage matrix*). It was measured on this Cloud project with
Langfuse's own `isRootObservation`:

- **`traceparent` only:** one trace id, but **three roots**. Every service starts its own logical
  root, so a judge scoped to "root observations" fires once per hop.
- **A baggage header alone:** one root, so it *looks* fixed, but **no session or user** on the
  remote rows.
- **`traceparent` plus baggage set with `propagate_attributes(as_baggage=True)`:** one trace, one
  root, the session everywhere. That's this demo's default.

Now break it live, fault F4 ("the new A2A client dropped the baggage header"):

```bash
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a --linkage traceparent
```

Open the first turn's trace:
- **Three roots now:** `support-turn`, `tech-support`, `billing`.
- **Session gone downstream:** tech-support's and billing's rows have no session.
- **The customer got the same correct answer.** Every quality score is still green. The only thing
  that broke is the observability, and that includes evaluation: the remote hop lost the trace name
  the online rules filter on, so its handoff was never scored.

Then the hygiene point:
- **Scope:** baggage is set for the one outbound A2A call only.
- **Content:** it carries no PII, and it is never sent to a partner's agent.
- **The default to turn off:** the Anthropic SDK injects trace headers, baggage included, into model
  API calls by default. On a remote agent that would send the customer's ids to the model API, so
  this demo turns it off.

> Land: "same trace" is a property you engineer and test, not a default.
> Ask: *"Which of your agent-to-agent calls cross a team or company boundary, and what are you
> allowed to send across it?"*

## 3 · Evaluating the handoff, not just the answer (10 min)

**Evaluators** page: four, each on a rule.

| Evaluator | Kind | Where it runs |
|---|---|---|
| `handoff-context-complete` | code (Python, inside Langfuse) | every handoff: did it carry the fields the target needs? |
| `handoff-loop` | code | every turn: did the path ping-pong or hit the hop cap? |
| `handoff-appropriate` | LLM judge | every handoff: right agent, given the roster? |
| `customer-resolved` | LLM judge | every turn: did the reply resolve or move the request forward? |

Two design points to call out:
- **Each rule selects by `metadata.role`, never by observation name,** and only on production
  traffic, so experiments don't burn judge tokens.
- **A judge sees only the observation it matches.** So the handoff observation carries the target's
  required fields and the roster: what the judge needs to decide.

**Fault F2, context loss** ("a token-saving refactor dropped `line_id` from handoff payloads"):

```bash
HANDOFF_SLIM_CONTEXT=1 .venv/bin/python scripts/run_scenario.py router-offline-tech --topology a2a
```

Open the trace → the `handoff` → Scores:
- **The catch:** `handoff-context-complete` = false, with the comment *"transfer to tech-support is
  missing: line_id"*. It names the field.
- **The miss:** the technician was still booked, because tech-support looked the line up itself.
  *"On a lucky run, the outcome check passes and hides a broken contract between two teams."*

**Fault F1, misroute** ("to cut a hop, send every credit request straight to billing"):

```bash
.venv/bin/python scripts/run_scenario.py hero-outage-credit --topology a2a --label fault-f1
```

- **The path:** `concierge>billing>tech-support>billing`. Billing's own rule ("tech-support must
  confirm the outage first") sent it on, so the customer still got the right $2.67, one hop late.
- **The catch:** `handoff-appropriate` flags exactly the concierge → billing handoff: *"Tech-support
  should have received this first because an outage credit requires a confirmed outage."* The other
  hops pass.
- **The miss:** `customer-resolved` scored that turn about 0.9. The answer was right; the routing
  was wrong.

Show the fault itself: Prompts → `concierge` → compare the production version with the
`fault-f1` version. Only the routing lines differ.

> Land: score the handoff and the trajectory, not only the final answer; the end-to-end judge was
> happy in both faults.
> Ask: *"If one of your agents started routing wrong tomorrow, which signal would tell you first?"*

## 4 · When agents bounce work (5 min)

> "In the same week, network-ops said 'our agent never touches money', and billing said 'ours
> never touches outages'. Each change was sensible on its own."

Fault F3, ping-pong:

```bash
.venv/bin/python scripts/run_scenario.py ambiguous-bill-and-slow --topology a2a --label fault-f3
```

The first turn's path is `concierge>tech-support>billing>tech-support>billing`. The hop cap stops
it and the customer gets *"I've flagged it for a person"*.

- **The catch:** `handoff-loop` on the turn root: *"ping-pong tech-support>billing>tech-support;
  repeated hop tech-support>billing"*. Code, on every turn, at no model cost.
- **The miss:** the per-hop judge passed 3 of 4 hops. Each hop looks reasonable on its own; only
  the path shows the loop.
- **The cost:** in the trace, four handoffs and nine model calls, about $0.075. That's roughly four
  times a normal turn, for a turn that resolved nothing.

> Land: some failures only exist across agents, so some evaluators have to look across agents.
> Ask: *"Who owns the boundary between two of your agent teams?"*

## 5 · A remote agent goes down (3 min)

Fault F5. Point the orchestrator at a billing service that isn't there:

```bash
BILLING_URL=http://127.0.0.1:9 .venv/bin/python scripts/run_scenario.py balance-quick --topology a2a
```

- **The trace:** the `handoff` to billing is at ERROR level, with the A2A failure as its status
  message.
- **The online score:** `handoff-succeeded` = false.
- **The customer:** gets a graceful apology instead of a crash.

Say it plainly: this fault isn't missed by anything. It shows graceful degradation, and it shows
that the failed hop is a scored, filterable event. To show slowness instead of an outage, restart
billing with `BILLING_FAULT_LATENCY_MS=8000`; latency shows in the trace, and with
`HANDOFF_A2A_TIMEOUT_S=5` it becomes a timeout.

> Land: an unavailable agent is a measured event with a fallback, not a stack trace.
> Ask: *"What should your front-door agent say when a specialist is down?"*

## 6 · Single agent vs. multi-agent, measured (7 min)

> "The question we always get: is splitting into agents better? We ran it."

Datasets → `handoff-routing` → Runs. Select the `single`, `local` and `a2a` runs and compare them.
They use the same 40 items, prompts, models and tools.

- **Outcome:** all three reached the right back-office state on 40 of 40. Credit amounts and
  "outage checked before any credit" are 1.0 everywhere.
- **Cost and latency:** single $0.0177 per item at 5.5 s; local $0.0213 at 10.0 s; A2A $0.0217 at
  10.5 s. The extra agents cost about 20% and double the latency. The A2A hop itself adds half a
  second.
- **Multi-turn:** same story on 12 simulated conversations. All resolved everywhere; $0.0555 /
  $0.0589 / $0.0664 per conversation.

The honest point:
- **No accuracy gain on this desk.** Multi-agent has to earn its cost on something you chose before
  you ran it: team ownership, independent deploys, a partner agent you don't control.
- **Ownership brings its own failures.** It brings acts 3–5's failure modes, which a single agent
  can't have. So the routing and trajectory scores only exist for the multi-agent runs; the outcome
  and process scores apply to all three.

Also say: these are small samples (one run each, 40 and 12 items), and a one-item difference is
noise.

> Land: measure the architecture like any other change: same items, same scores, cost included.
> Ask: *"What would multi-agent have to beat for your team to accept its cost?"*

## 7 · Conversations and the human in the loop (5 min)

Sessions → one of today's conversations, for example conv-07: a billing question that turns into
an outage claim.

- **Turns and owners:** each customer message is a turn, and ownership moves between agents as the
  topic changes (the exact sequence varies run to run).
- **Session scores** (there is no session-target judge, so these come from code at the end of the
  conversation): `turns-to-resolution`, `transfers`, `conversation-ended-by`, and
  `resolution-correct`, which reads the back-office ledger.

Annotation queues → **Transferred conversations**: every conversation with a transfer, as a
whole-session item, with `reviewer-resolved` and `handoff-failure-mode` to label.

The candid moment: open today's conv-05 session (the 2.5-hour blip). In our runs, `customer-resolved`
scored the correct "no credit, the outage was under 4 hours" reply about 0.3. If today's score
differs, use whichever turn the judge scored lowest.
- **The disagreement:** the judge marked it down because the customer had no next step, although
  the policy was applied correctly. Judge and policy disagree.
- **Why the queue exists:** this is what the queue and calibration are for. A judge earns a place
  on a dashboard only after people check it.

Mention that the customers are simulated (Haiku 5.5 with a persona and a goal). The simulator
returns a structured status (continue, done, give up); a "reply DONE" instruction proved unreliable.

> Land: the conversation is the unit the customer feels, so score it and put the hard ones in
> front of a person.
> Ask: *"Who on your side would review transferred conversations, and how many a week?"*

## 8 · Round the loop on one bug (10 min)

> "Everything so far was us flipping a fault on purpose. Here is what it looks like when one ships:
> release 1.1 went out last week, and nobody's dashboard turned red."

The setup, in one breath: someone shortened the concierge's routing to "money back goes straight to
billing, it saves a hop". That is F1, and it has been production since before the meeting.

**Monitor.** Dashboards → **Handoffs — quality**.
- **Handoff judged right, by prompt versions:** the row `concierge v3 / tech-support v1 / billing v1`
  has the flags. In our run, the concierge's first handoff was flagged in 13 of 37 turns, every one an
  outage credit sent to billing.
- **Customer resolved, by prompt versions:** the same row sits at about 0.8, and every conversation's
  ledger is right. Billing passed each request on, so the customer never noticed.

**Datasets.** In the terminal, `scripts/flag_handoffs.py`. Then open Annotation queues → **Flagged
handoffs**.
- **What's in the queue:** the flagged handoffs, plus as many the judge passed, so the review checks
  the judge in both directions.
- **Label one live:** open it, read the customer's message and the payload, and set
  `correct-handoff-target` = tech-support. Say that `--simulate-reviewer` labelled the rest from the
  scenario's ground truth.
- **Promote:** `scripts/promote_to_dataset.py`. Datasets → `handoff-routing` now has `prod-…` items,
  each linked to the production trace it came from.

**Experiment and evaluate.** `scripts/loop.py fix`. Prompts → concierge → compare v3 with v4. The fix
keeps the latency idea and adds the exception: outage credits go to tech-support first.
- **The gate:** `scripts/prompt_gate.py --label candidate --compare production` (about 6 minutes; show
  a run from before the meeting). The fix routes 1.00, and the live bug 0.61 on the same 56 items.
  `resolution-correct` is 1.00 for both, which is why the gate blocks on routing and loops, not on
  outcomes. The bug also cost 22% more per item: billing had to send every outage credit back.
- **The judge:** `scripts/judge_calibration.py`. It agreed with the reviewer on all 16 labelled items.
  Say what that is: 16 easy turn-1 cases, labelled from ground truth. A direction, not a measurement.

**Deploy.** `scripts/loop.py promote`, then show the label on v4.
- **Nothing redeploys:** the agents fetch the prompt by label, so production picks it up within a
  minute.
- **CI:** with the Langfuse automation set up, this label change also runs the same gate in GitHub
  Actions (`.github/workflows/handoff-prompt-gate.yml`).
- **The result:** run `scripts/run_traffic.py --only conv-01 conv-12` and refresh the dashboard. A new
  row appears for v4. In our run, the concierge's first handoff was flagged in 1 of 38 turns, there
  were 0 loops, and concierge → tech-support went from 13 to 23.

The honest close of the act: after the fix, the judge still flags some specialist → billing hops,
because it wants to see the outage confirmation in the payload. That is the next round of the loop,
and it starts in the same queue.

> Land: a production failure became a test case, the fix went through the same gate as any change,
> and the dashboard shows the before and after by prompt version.
> Ask: *"Where in your pipeline would this gate run, and who owns the thresholds?"*

## 9 · Close (2 min)

The three answers, and what to do with them:
1. **A handoff is a step you can open:** what it carried, to whom, and how it was linked.
2. **One trace and one session across services** is `traceparent` plus scoped baggage, measured.
   `traceparent` alone splits the roots, and nothing in the answer tells you.
3. **Evaluate the handoff, the trajectory and the outcome separately,** and compare the architecture
   against a single agent on the same items.
4. **Close the loop on what you find:** judge flags go to people, labels become test cases, and a
   deterministic gate decides the release.

What isn't built yet, if asked: a black-box partner agent. The CI workflow is written, but it runs
only once it is merged to the default branch.

---

## Appendix A · Commands behind each act

| Act | Command |
|---|---|
| Pre-flight | `scripts/verify_demo.py --faults` (day before) · `./scripts/run_services.sh && scripts/verify_demo.py` · `scripts/seed_dashboards.py --check` |
| 1 | `scripts/run_scenario.py hero-outage-credit --topology a2a` |
| 2 | `scripts/linkage_matrix.py --live` (measured beforehand) · `scripts/run_scenario.py hero-outage-credit --topology a2a --linkage traceparent` |
| 3 | `HANDOFF_SLIM_CONTEXT=1 scripts/run_scenario.py router-offline-tech --topology a2a` · `scripts/run_scenario.py hero-outage-credit --topology a2a --label fault-f1` |
| 4 | `scripts/run_scenario.py ambiguous-bill-and-slow --topology a2a --label fault-f3` |
| 5 | `BILLING_URL=http://127.0.0.1:9 scripts/run_scenario.py balance-quick --topology a2a` |
| 6 | `scripts/run_experiment.py --topology single\|local\|a2a [--dataset conversations]` · `scripts/compare_runs.py --since <time>` |
| 7 | `scripts/run_traffic.py --only conv-07` |
| 8 | Before: `scripts/loop.py ship-bug` · `scripts/run_traffic.py --repeat 2`. Live: `scripts/flag_handoffs.py [--simulate-reviewer]` · `scripts/promote_to_dataset.py` · `scripts/loop.py fix` · `scripts/prompt_gate.py --label candidate --compare production` · `scripts/judge_calibration.py` · `scripts/loop.py promote` · `scripts/run_traffic.py --only conv-01 conv-12`. After: `scripts/loop.py status` |

All Python commands run with `.venv/bin/python` from `demos/multi-agent-handoff`.

## Appendix B · Show me the code

| What | Where | Why it matters |
|---|---|---|
| Baggage set for one outbound call only | [handoff/linkage.py:62](handoff/linkage.py) | `propagate_attributes(as_baggage=True)` scoped to the A2A call: one root and the session downstream, nothing leaking into other HTTP calls |
| Headers injected inside the handoff observation | [handoff/transport.py:72](handoff/transport.py) | the remote agent's spans hang under the `handoff`, so the tree reads caller → handoff → callee |
| Headers sent per call, no interceptor | [handoff/a2a_client.py:93](handoff/a2a_client.py) | `a2a-sdk` sends `ClientCallContext(service_parameters=…)` as HTTP headers on that one call |
| Trace context attached before the agent runs | [handoff/a2a_service.py:88](handoff/a2a_service.py) | the receiving service continues the caller's trace instead of starting a new root |
| The handoff observation and what evaluators need on it | [handoff/transport.py:28](handoff/transport.py) | payload as input; required fields and roster as metadata, because a judge sees only this observation |
| The model client's own trace propagation turned off | [handoff/agents/base.py:45](handoff/agents/base.py) | stops the Anthropic SDK from sending baggage (customer ids) to the model API |
| One session per conversation, tags per run | [handoff/orchestrator.py:124](handoff/orchestrator.py) | `propagate_attributes(session_id=…, user_id=…)` around each turn |
| The turn's trajectory written on the root | [handoff/orchestrator.py:143](handoff/orchestrator.py) | `agent_path` and `handoff_count` let a single-observation evaluator see the whole path |
| The hop cap | [handoff/orchestrator.py:202](handoff/orchestrator.py) | stops F3 at four handoffs and flags `hop_cap_hit` |
| Transfer vs. delegation, chosen by the model | [handoff/agents/base.py:242](handoff/agents/base.py), [:262](handoff/agents/base.py) | `transfer_to_*` hands the conversation over; `ask_*` asks and keeps it |
| Agent roster from A2A agent cards | [handoff/agents/base.py:66](handoff/agents/base.py) | adding a specialist means starting a service, not editing the concierge |
| The policy oracle and the write guard | [handoff/tools/policy.py:28](handoff/tools/policy.py), [handoff/tools/crm.py:190](handoff/tools/crm.py) | amounts are code; `issue_credit` refuses any other amount, and duplicates |
| Code evaluators, as uploaded | [evaluators/handoff_context_complete.py:72](evaluators/handoff_context_complete.py), [evaluators/handoff_loop.py:50](evaluators/handoff_loop.py) | standard-library Python, run inside Langfuse on every match |
| Rules select by metadata, production only | [scripts/seed_evals.py:78](scripts/seed_evals.py) | experiments never trigger them; renaming a span never silences a judge |
| The judges' LLM connection | [scripts/seed_evals.py:50](scripts/seed_evals.py) | the connection's name, case-sensitive (`Anthropic`) |
| Outcome scored from the ledger | [handoff/evals.py:117](handoff/evals.py) | what was written, not what the reply claims |
| The fault prompts | [handoff/faults.py:31](handoff/faults.py), [:44](handoff/faults.py) | production text with only the faulty lines changed |
| Per-agent prompt labels | [handoff/prompts.py:96](handoff/prompts.py) | an agent without the label runs production, so one label changes one agent |
| The simulated customer's structured reply | [handoff/conversation.py:46](handoff/conversation.py) | status + message ends conversations reliably |
| Conversation scores on the session | [handoff/conversation.py:162](handoff/conversation.py) | there is no session-target judge, so code scores the conversation |
| Transferred sessions to the review queue | [scripts/run_traffic.py:73](scripts/run_traffic.py) | `objectType: "SESSION"`, so reviewers see the whole conversation |
| The prompt versions on every turn | [handoff/prompts.py:120](handoff/prompts.py), [handoff/orchestrator.py:125](handoff/orchestrator.py), [handoff/linkage.py:60](handoff/linkage.py) | the trace version names the concierge, tech-support and billing versions, carried in baggage to the remote agents, so a dashboard splits any score by them |
| A label move is one PATCH | [scripts/loop.py:72](scripts/loop.py) | `newLabels` on the version; a label is unique per prompt, so this moves `production`, and the agents pick it up by label |
| Flagged handoffs to the queue | [scripts/flag_handoffs.py:129](scripts/flag_handoffs.py) | `objectType: "OBSERVATION"`: the reviewer labels the one handoff, not the whole trace |
| A label becomes a test case | [scripts/promote_to_dataset.py:87](scripts/promote_to_dataset.py) | `source_trace_id` / `source_observation_id` link the dataset item back to production |
| The gate's bar | [cicd/thresholds.json](cicd/thresholds.json), [scripts/prompt_gate.py:117](scripts/prompt_gate.py) | deterministic metrics only, and every item must have been evaluated |
| The loop check on experiment runs | [handoff/evals.py:177](handoff/evals.py) | the online `handoff-loop` rule on the run record, so the gate can require `loop-rate` = 0 |

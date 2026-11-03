# Debugging an agent in Langfuse: a two-hour workshop

**For:** product managers and deployment (forward-deployed) teams who run a probabilistic
agent for many customers, and who want to answer "why did the agent do that?" without
waiting for an engineer.

**Format:** the presenter drives Langfuse on screen and the room follows along. Nobody
needs an account, and everything shown is a click or a filter, not code. The session
works recorded too.

**The agent:** a marketing agent working for three fictional customers: Contoso
(agency software), Fabrikam Running (running shoes) and Tailspin Travel (travel
booking). A marketer gives it a task and it decides which of seven tools to call:
visibility reports, answer-engine snapshots, the customer's Context Manager, page
fetches, fact-checks, correction requests and CMS drafts. The model runs on real
Claude; the tools and customer data are synthetic. Four realistic faults are planted
in the data and tools, never in the model, so the agent reacts to them for real and
the same task can go right on one run and wrong on the next.

> Before the session, run `scripts/verify_demo.py`. It prints READY or what is missing,
> plus links to the best trace for each case. Keep that terminal open.

---

## Run of show

| Time | Module | You leave the room able to… |
|---|---|---|
| 0:00 | [0. Frame](#0-frame-5-min) | name the four questions every agent investigation starts with |
| 0:05 | [1. Find your way around](#1-find-your-way-around-15-min) | find any customer's runs in under 30 seconds |
| 0:20 | [2. Read one agent run](#2-read-one-agent-run-20-min) | explain why the agent made a particular choice |
| 0:40 | [3. Case files](#3-case-files-40-min) | debug wrong context, slowness, loops and skipped steps |
| 1:20 | Break | |
| 1:25 | [4. From one trace to a pattern](#4-from-one-trace-to-a-pattern-15-min) | tell "one bad run" apart from "every Tuesday" |
| 1:40 | [5. Close the loop without engineers](#5-close-the-loop-without-engineers-10-min) | hand off a finding, or fix it yourself |
| 1:50 | Q&A | |

If you are short on time, cut module 4 to the dashboard and the Halo step only, and
run Case 3 as a two-minute look at the graph.

---

## Pre-flight (T-30 minutes)

1. **Readiness.** From `demos/marketing-agent-debugging`, run
   `.venv/bin/python scripts/verify_demo.py`. It should print READY. Open the four
   hero-trace links in browser tabs.
2. **Make sure the regression is in place.** Run `.venv/bin/python scripts/seed_prompts.py --reset-tailspin`.
   This puts `production` back on Tailspin v3, in case a rehearsal "fixed" it.
3. **Turn on AI features** so the room can see Halo and Ask AI. An organization owner
   or admin does this under Organization Settings → AI features. Both are off by default.
4. **Turn on the Langfuse v4 preview** in the UI if the filter search bar isn't showing.
   The queries in this guide (`metadata.customer:contoso`, `scores.…`) need it.
5. **Warm up one live run** so the first run on stage isn't a cold start:
   `.venv/bin/python scripts/run_agent.py --scenario social-posts`. It takes about 15 s.
6. **Set the time range** to "Last 7 days" on the Tracing page. Seeded traffic is from
   the day before.
7. **Close other Docker stacks** if you plan to run anything locally. This demo needs
   only Python and Langfuse Cloud.

Live runs cost roughly $0.05–0.45 each on Claude Opus 5.5 and take 15–90 s. Narrate
while they run.

---

## 0. Frame (5 min)

**Frame.** "Your agent now decides for itself: which tool, what to look up, when it's
done. That's the feature. It's also why 'why did it do that?' became hard. A trace is
the agent's flight recorder. Today we read it together, without engineering."

Write the four questions on the screen. Every case today maps to one of them:

1. **Why did it do that?** What did it see, what did it think, what did it choose?
2. **Did it retrieve the right information?** What came back, and what did the model actually get?
3. **Why is it slow (or expensive)?** Which step is it waiting on?
4. **Why did it keep going, or stop, or skip a step?** Loops, limits, missing steps.

**Ask.** "When a customer escalates today, what's the first thing you look at, and
who do you have to ask?"

---

## 1. Find your way around (15 min)

**Frame.** "Before you can debug a run you have to find it. Your starting point is
always a customer, a person, or a complaint, never a trace id."

**Show.**

1. **Tracing.** By default the table shows *root observations*: one row per agent run,
   with the marketer's task as input and the agent's reply as output. Point out the
   latency, cost and score columns.
2. **Filter by customer.** In the search bar, type `metadata.customer:contoso`. Then
   try `user:dana` (autocomplete suggests `dana.reyes@contoso.example`). Users are
   marketers; each belongs to one customer.
3. **Filter by symptom.** Type `scores.reviewer-decision:rejected`. These are runs a
   marketer turned down, with the reason in the score comment. This is the queue a
   deployment team starts its day with.
4. **Pulse.** The strip above the table shows count, cost and latency spikes over
   time. Drag across a spike to narrow the table to that window.
5. **Save a view.** Filter to `metadata.customer:tailspin`, sort by latency
   descending, and save it as "Tailspin, slowest first". Saved views are shared with
   the project.
6. **Sessions.** Open Sessions and pick the Contoso chat (three turns: brief → shorter
   with a Litware table → stage it). One trace per turn, grouped by the chat.
7. **Ask AI** (if enabled). Click Ask AI next to the bar and type "Fabrikam runs
   slower than a minute". It writes the filter pills for you.

**Land.** "Customer, person, symptom, time window: four filters, and you're looking at
the right runs. Save the ones you use every day."

**Ask.** "How do you know which customer a conversation belongs to today? Is that on
every request your agent makes?"

> Under the hood: the customer, task type and instructions version are trace metadata
> set once per run (`agent/loop.py`, `propagate_attributes`), so every observation
> carries them and every filter works on every row.

---

## 2. Read one agent run (20 min)

Use a Contoso `pricing-check` run that was approved. `verify_demo.py` lists them under
Case 1. You'll come back to the same trace in Case 1, so the room already knows its shape.

**Frame.** "Reading a run comes down to four steps on repeat: what did it *see*, what
did it *think*, what did it *do*, and what came *back*?"

**Show.**

1. **Tree.** `marketing-agent` (agent) → `plan-next-step` (one generation per model
   call) with the tools it called as siblings → `check-publishing-policy` (guardrail).
   Icons tell you the type: generation, tool, retriever, guardrail.
2. **Graph.** Open the Graph tab. In **Aggregated** mode, repeated steps collapse
   (`search_answer_engine (10/10)`). Switch to **Expanded** to see the run as it
   happened. "This is the agent's plan, drawn from what it actually did."
3. **Pick the first `plan-next-step` generation.**
   - **Prompt link:** `marketing-agent/contoso v1`. These are the instructions the
     agent was following, as a clickable version in Prompt Management.
   - **Available tools:** all seven tools the model could choose from, at the top.
     Click one to see its description, which is what the model reads.
   - **Messages:** system instructions, then the marketer's task.
   - **Output:** the tool calls it chose, matched against the numbered tool list. Then
     the **thinking**, e.g. "I'm pulling brand context on pricing and the visibility
     report… I'll run them in parallel." That's the *why*.
4. **Click a tool** (`search_answer_engine`). Input = the arguments the model chose;
   output = what came back. Then click the *next* `plan-next-step`. Its input now
   ends with that tool result, which is exactly what the model saw before its next
   decision.
5. **Timeline.** Each bar is a step. Gaps are model thinking; long bars are tools.
6. **Log view.** Every observation concatenated top to bottom. Press Ctrl/Cmd-F and
   search for `$12`. You can see every place last year's price appears in the run.
7. **Cost and tokens** on each generation. Point at the cache-read tokens: the history
   is cached between steps, which is why step 4 isn't 4× the price of step 1.

**Land.** "Every decision has a receipt: the instructions version, the tool menu, what
it read, what it thought and what it picked. You can answer 'why' from here."

**Ask.** "Which of your agent's decisions do customers question most? Which tool
choice would you want to audit first?"

---

## 3. Case files (40 min)

Each case starts from a realistic complaint, never from a trace id. For each case,
walk through: find it, diagnose it, name the owner, then fix and verify (live where
marked ⚡).

### Case 1: "Why doesn't it know our pricing?" (wrong context)

**Symptom.** Dana at Contoso: *"The pricing check is flaky. Sometimes it queues
corrections, once it asked me what our price is, and the fact-check says both $12 and
$15 are 'supported'. Which is it?"*

**Find.** Search `metadata.customer:contoso metadata.task_type:pricing-check`. Open two
runs side by side: one that queued corrections, and one that asked Dana to confirm.
`verify_demo.py` lists both; a run that asked carries a `rejected` reviewer decision
if one exists in the corpus.

**Diagnose.**

1. Open `retrieve_brand_context` (the retriever icon). **Output** is the three
   passages the model got: all three are "Contoso pricing FAQ" saying $12/$20 per
   user. **Metadata → hits** shows what the model did *not* see: `status: archived`,
   `updated_at: 2024-02-12`, and `include_archived: true` on the index.
2. Open `check_claims` → `verify-claims`. The fact-checker read the same index, so it
   rated $12 "supported", and $15 too. The safety net was fooled by the same bad data.
3. Read the next `plan-next-step` thinking: "The Context Manager has two conflicting
   figures… I'll treat the FAQ as stale…" In good runs the agent noticed and called
   `fetch_page` on the live pricing page. In the "asked Dana" run it didn't trust either.
4. Compare the two runs: same retrieval and same defect. The difference is the agent's
   judgment call, which is why the behaviour looked "flaky".

**Root cause.** Data, not the model. The Context Manager still serves an archived
document, and its passages carry no dates, so the model can't tell old from new.
**Owner:** whoever manages the customer's context (the deployment team).
**Fix:** exclude archived documents, and pass `updated_at` with each passage.

**⚡ Verify.** `.venv/bin/python scripts/run_agent.py --scenario pricing-check --fix exclude-archived`.
Retrieval now returns the current price list first, `verify-claims` marks $12
contradicted, and the run's metadata shows `fixes: exclude-archived`.

**Land.** "'Did it retrieve the right information?' has two halves: what came back,
and what the model was *allowed to see* about it. The retriever observation shows both."

**Ask.** "Who owns your customers' context today? How would you know a document is stale?"

### Case 2: "It takes over a minute and our bill spiked" (latency and cost)

**Symptom.** Sam at Fabrikam: *"The page refresh is way too slow."* The reviewer
comment on those runs says the same: "took 74s - far too slow".

**Find.** `metadata.customer:fabrikam metadata.task_type:page-refresh`, sorted by
latency. Then on the Observations side: `type:TOOL name:fetch_page latency:>3`.

**Diagnose.**

1. **Timeline.** One `plan-next-step` asks for three pages **in the same turn**, and
   three `fetch_page` bars then run **back to back**, about 4–6 s each. Metadata shows
   `cms_cache: MISS`. The model asked in parallel; the harness ran them one by one.
2. Click the generation **after** the fetches: its cache-write tokens jump to about
   45,000 and its cost is ~10× the other steps. Open that `fetch_page` output: the
   CMS returned the full raw HTML (navigation, tracking JSON, CSS), not page text.
   Metadata: `content_type: text/html (format=text ignored)`.
3. Every later step re-reads that context. One noisy page sets the price of the rest
   of the run.
4. The red `fetch_page` node is a 404 on `compare/stability-shoes`. Check the next
   step's thinking to see how the agent handled the missing page: did it say so, or
   work around it?

**Root cause.** Infrastructure and harness: an uncached customer CMS, sequential tool
execution, and a tool that returns markup. **Owner:** platform engineering. The PM's
job is to arrive with the evidence: this step, this much time, this much money.

**⚡ Verify.** `.venv/bin/python scripts/run_agent.py --scenario page-refresh --fix parallel-tools --fix clean-html`.
Compare the timelines: the three fetches now start at the same moment, and the token
spike is gone. In rehearsal the run cost fell from about $0.42 to $0.15, and the
largest single-step cache write fell from ~45k to ~2k tokens.

**Land.** "'Why is it slow?' is almost never 'the model is slow'. The timeline tells
you which step you're waiting on, and the token counts tell you what it costs."

**Ask.** "When a customer says the agent is slow, what's your threshold, and would
you rather have it faster or cheaper?"

### Case 3: "The weekly report takes forever and Copilot is always missing" (loop)

**Symptom.** Priya at Tailspin; the reviewer comment says "kept re-polling a capture
that never finished".

**Find.** `metadata.customer:tailspin scores.agent-steps:>=8`, or on Observations
`name:search_answer_engine level:WARNING`.

**Diagnose.**

1. **Graph → Aggregated.** `search_answer_engine` shows a big counter and a cycle back
   to `plan-next-step`. You can see the loop without reading anything.
2. Open the `search_answer_engine` outputs in order: `progress_percent` 35 → 51 → 67
   → 83 → 99 → 99 → 99, each with "Retry the same call to fetch the result." The
   capture job is stuck.
3. Open the linked prompt `marketing-agent/tailspin`: *"If an engine's data is still
   being captured, retry until you have it; never send a partial report."* The agent
   is doing exactly what it was told.
4. Ask Halo (if enabled) on this trace: "Summarize what the agent did and why it
   stopped."

**Root cause.** Two owners. The tool contract never reaches a final state (platform
engineering), and the instructions say "retry until you have it" with no limit (the
deployment team or PM).

**⚡ Verify (PM fix, no code).** In Prompt Management, open `marketing-agent/tailspin`,
create a new version from production that replaces the retry line with *"Retry an
engine at most twice; if it is still capturing, report it as unavailable."*, and give
it the `production` label. Then run
`.venv/bin/python scripts/run_agent.py --scenario weekly-report` and watch the counter
drop. The engineering fix is `--fix terminal-pending`, where the tool itself says
"unavailable, do not retry".

**Land.** "Loops are usually the agent following instructions faithfully against a
tool that never says 'done'. The graph shows the loop; the prompt shows why."

**Ask.** "Do your agents have a step limit or a budget? Who decides what 'give up
gracefully' means for a customer?"

### Case 4: "Since yesterday it stages drafts without fact-checking" (skipped step)

**Symptom.** Marco at Tailspin: the reviewer rejected FAQ drafts with "Draft was
staged without a fact-check, which our process requires."

**Find.** `scores.publishing-policy:false`, or on Observations
`name:check-publishing-policy level:WARNING`.

**Diagnose.**

1. In the rejected run, the tool sequence is `retrieve_brand_context` → `fetch_page`
   → `stage_cms_draft`, with no `check_claims`. The guardrail observation says
   `published without fact-check: stage_cms_draft`.
2. Open the `plan-next-step` that calls `stage_cms_draft`. The prompt link reads
   `marketing-agent/tailspin v3`, and the thinking spells out the decision. In the seeded
   runs it reads: "I'm also wondering whether to double-check the claims, but since they
   come straight from the context and the speed guidance says to skip optional steps,
   I'll move on and stage the draft." That is the *why*, in the agent's own words.
3. Open a Tailspin FAQ run on **v2** (`verify_demo.py` lists one): it calls
   `check_claims` before staging.
4. Open **Prompt Management → marketing-agent/tailspin**. The version history shows v3
   committed as *"Speed up drafts: trim process steps"*. Compare v2 with v3: the
   fact-check line was removed and "use as few tool calls as possible" was added.
5. The prompt's metrics show which version produced the rejections, so you're not
   relying on a hunch.

**Root cause.** An instruction regression: someone optimized for speed and removed a
process rule. **Owner:** whoever edits the customer's agent config.

**⚡ Verify (PM fix, no code).** Create a new version from the current production
version with the fact-check line restored, and give it the `production` label. Then run
`.venv/bin/python scripts/run_agent.py --scenario faq-page`. `check_claims` is back
before `stage_cms_draft`, and the guardrail passes. Moving `production` back to v2 is
the instant rollback, but if you did Case 3's fix first, v2 brings back "retry until
you have it". Say that out loud: rolling back a label rolls back *everything* in that
version. After the session, run `scripts/seed_prompts.py --reset-tailspin`.

The line to restore (copy it from v2):

```
- Before calling stage_cms_draft or queue_correction, run check_claims on every
  factual claim in the draft or correction, and fix anything that is not supported.
```

**Land.** "Instructions are product. They're versioned, linked to every run, and
labels decide what's live, so a PM can find a regression *and* roll it back."

**Ask.** "Who can change a customer's agent instructions today, and how would you
know what changed?"

---

## 4. From one trace to a pattern (15 min)

**Frame.** "One bad run is an anecdote. Before you escalate, find out whether it's one
customer, one tool, or everything since Tuesday."

**Show.**

1. **Dashboard → Agent health — deployment view.** Go through the widgets: slowest
   tools (p95), tool calls by tool, reviewer decisions, spend by marketer (one
   customer each), errors and warnings by step, and run latency over time. Every
   widget is a saved query; anyone can add one.
2. **Observations table as an analysis tool.** `type:TOOL` then sort by latency.
   `type:GENERATION` then sort by cost. `level:(ERROR OR WARNING)` then look at which
   step fails.
3. **Tool-call filters.** On generations, filter by *called tools* (for example, runs
   whose generations called `stage_cms_draft`) and by the *number* of tool calls.
   "Which runs staged something?" is one filter.
4. **Halo**, if enabled. Try:
   - "Which agent runs were rejected by reviewers in the last 2 days, and what do the
     comments have in common?"
   - "Which tool has the highest p95 latency, and for which customer?"
   - "Build a dashboard widget of reviewer rejections per customer."
   Point out that Halo asks for approval before it changes anything.

**Land.** "Dashboards and filters turn 'I think it's slow' into 'fetch_page p95 is 5.8
seconds for Fabrikam only'. That's a ticket an engineer can act on."

**Ask.** "What three numbers would you want on a per-customer health page?"

---

## 5. Close the loop without engineers (10 min)

**Show.**

1. **Comment on an observation.** On Case 1's `retrieve_brand_context`, add "Archived
   FAQ still indexed. @platform please exclude status=archived". Comments stay
   attached to the exact step.
2. **Annotation queue → PM review — rejected agent runs.** Every rejected run, queued.
   Label one with `root-cause` (`wrong-or-stale-context`, `instructions`,
   `tool-contract`, `slow-dependency`, `model-reasoning`, `not-a-bug`). After a week
   of this, you have an error taxonomy per customer instead of a pile of anecdotes.
3. **Correct an output.** On a rejected run, add a *corrected output* with what the
   agent should have produced. It's ground truth for later evaluations.
4. **Add to a dataset.** From the trace, choose "Add to dataset" → `regression-cases`.
   Today's four cases become the test set the next instructions change is checked against.
5. **Prompt labels are deploys.** Moving `production` *is* shipping. Version history
   and commit messages are the audit trail.

**Land.** "Find it, explain it, label it, hand it off with a link, or fix it yourself
when it's instructions. That's the skill we came for."

**Ask.** "Which of these would change your Monday? What would you want set up on your
own project first?"

---

## Appendix A: which view answers which question

| Question | Go to | Look at |
|---|---|---|
| Which runs should I look at? | Tracing table + search bar | `metadata.customer:` · `user:` · `scores.reviewer-decision:rejected` · Pulse |
| What did the agent decide, and why? | Generation `plan-next-step` | available tools · tool calls · **thinking** · prompt link |
| What did it see before deciding? | The *next* generation's input | the tool result messages |
| Did it retrieve the right information? | Retriever observation | output = what the model saw; metadata = what it didn't |
| Why is it slow / expensive? | Timeline · generation usage | long tool bars · token jumps · cache reads |
| Why did it loop / stop / skip? | Graph (aggregated) · guardrail · prompt version | counters and cycles · policy violations · version diff |
| Is it a pattern? | Dashboard · Observations table · Halo | p95 by tool · rejections by customer |
| Who fixes it? | Appendix B | |

## Appendix B: who owns the fix

| Root cause | Example in this demo | Owner | Can a PM fix it in Langfuse? |
|---|---|---|---|
| Wrong or stale context | archived pricing FAQ (Case 1) | deployment team / data owner | no; hand off with the retriever observation link |
| Instructions | v3 dropped the fact-check rule (Case 4); "retry until you have it" (Case 3) | PM / deployment team | **yes**: Prompt Management version + label |
| Tool contract | capture never reaches a final state (Case 3) | platform engineering | no; hand off with the tool outputs |
| Slow dependency / harness | uncached CMS, sequential tool calls, raw HTML (Case 2) | platform engineering | no; hand off with the timeline + cost |
| Model reasoning | agent asks for confirmation instead of resolving (Case 1 variant) | PM + engineering | sometimes (instructions); otherwise evals |

## Appendix C: honest notes for the presenter

- **The agent is real; the world is synthetic.** Claude really decides every step.
  Tools return deterministic synthetic data with simulated latency (`agent/tools.py`).
- **The reviewer is a stand-in.** `reviewer-decision` comes from a rule-based reviewer
  (`agent/review.py`) so every seeded run has feedback. In production these are your
  marketers' approve/reject clicks.
- **Outcomes vary, by design.** Case 1 is "flaky" because Opus usually notices the
  conflict; that variance is the lesson. Cases 3 and 4 reproduce reliably. If a live
  run surprises you, debug that one; it's more convincing than the script.
- **What this workshop does not cover:** setting up evaluators, experiments and CI
  gates. Those are the natural follow-up session, and the root-cause labels and
  regression dataset from module 5 are their input.

## Appendix D: show me the code

| What | Where | Why it matters |
|---|---|---|
| One trace per run, customer and task on every observation | `agent/loop.py`, `propagate_attributes(...)` in `run_agent` | makes every filter in module 1 work |
| Each model call is its own generation with tools, messages and thinking | `agent/loop.py`, `plan-next-step` block and `_assistant_trace_message` | the "available tools / tool calls / thinking" view in module 2 |
| Instructions fetched by label and linked to each generation | `agent/loop.py`, `lf.get_prompt(...)` + `prompt=linked_prompt` | version links in Case 4 |
| Tools as `tool` / `retriever` observations, failures as WARNING/ERROR | `agent/loop.py`, `_traced_tool` | red nodes, retriever icon, agent graph |
| What the model sees vs. what the trace records | `agent/tools.py`, `search_context` (passages vs. records) | the core of Case 1 |
| Guardrail + reviewer scores | `agent/loop.py`, end of `run_agent`; `agent/review.py` | `publishing-policy`, `reviewer-decision` |
| Not double-counting cost | `agent/config.py`, `should_export_span` | the Anthropic SDK emits its own spans |

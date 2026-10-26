# Northwind Bank — 30-minute evals-first demo

A 30-minute cut of the workshop ([DEMO_SCRIPT.md](DEMO_SCRIPT.md) is the full
2-hour version). This one leads with evaluation. Tracing appears only as the
evidence a score is based on. The room follows one question from start to
finish:

> **"The product owner wants to ship a new system prompt. How do we know it's
> good enough, and how will we know when it stops being good?"**

Each act has four beats: **Frame** (the problem in the bank's terms, before you
touch the screen) → **Show** (exact clicks) → **Land** (the takeaway) → **Ask**
(an open question; write the answers down). **Candour** notes are limits to say
out loud. They build more trust than they cost.

Northwind Bank is fictional and all data is synthetic. A companion storyboard
for a screenshot walkthrough video is in
[docs/EVALS_FIRST_VIDEO.md](docs/EVALS_FIRST_VIDEO.md).

---

## Run sheet

| Time | Act | The room learns | Main screen |
|---|---|---|---|
| 0:00–2:00 | Open: define "good" before you look at a trace | quality = five questions, each one an evaluator | slide |
| 2:00–8:00 | 1 · Every answer gets graded | deterministic checks + LLM judges + humans on live traffic | portal → trace Scores tab → Evaluators |
| 8:00–13:00 | 2 · Grade the grader | a judge is a model; calibrate it on production labels before you trust it | `judge-calibration/faithfulness` runs, SME queue |
| 13:00–18:30 | 3 · Is the new prompt better? | golden datasets + experiments; decide on deterministic metrics | dataset compare view (EN + ES) |
| 18:30–22:30 | 4 · Let the gate say no | the judge would have shipped a regression; hard checks blocked it | GitHub Actions run + `cicd/thresholds.json` |
| 22:30–27:30 | 5 · Production teaches the dataset | evaluate outcomes; a production failure becomes a regression test | business dashboard → trace → disputes dataset |
| 27:30–30:00 | Close: the loop and the first two weeks | what an evals-first POC looks like | slide |

Only one step is live: the chat turn in Act 1. Everything else opens recorded
runs whose numbers were re-read from the Cloud project on 2026-10-18. That keeps
the 30 minutes predictable.

**Running long?** Drop Act 2 step 4 (the rubric pair) and keep the
synthetic-vs-production and judge-vs-human findings. **Running short of
questions?** See the backup topics at the end.

---

## Open · define "good" before you look at a trace (2 min)

**Frame.** "Most teams start with observability: they log everything, then
wonder what to look at. We'll start at the other end. What does a *good* answer
from a bank's assistant mean? Five questions, and every one becomes a score."

**Show** (slide or whiteboard):

| The bank asks | Evaluator in this demo | Kind |
|---|---|---|
| Is it true, given what the assistant was shown? | `faithfulness` (offline: `correctness`, `must-include`, `source-recall`) | LLM judge + deterministic |
| Is it allowed under our conduct policy? | `banking-compliance` (rules P1–P5), `no-unsolicited-upsell` | LLM judge + deterministic |
| Is it safe? | `guardrail-blocked`, `security-risk`, `pii-in-input`, `manipulation-resistance` | deterministic + LLM judge |
| Is it in the customer's language and our register? | `language-match`, `formal-register` (Spanish *usted*) | deterministic |
| Did the customer get what they came for? | `task-outcome`, `contained`, `dispute-resolved`, `value-usd` | deterministic (business outcome) |

**Land.** Quality is a list you can write down with risk and compliance before
launch. Every row is a score that you can chart, filter, gate on, and audit.

---

## Act 1 · Every answer gets graded (6 min) — online evaluation

**Frame.** "If a regulator asked how you know yesterday's ten thousand answers
were compliant, what would you show them today? Spreadsheets of samples? Here,
every production answer is graded within a minute, and the grade sits next to
the evidence."

**Show.**
1. Portal (http://localhost:8090) → **Customer assistant** → customer **Ana
   Torres (C-1001)**, channel **web** → chip **"Savings in bitcoin?"**
   (🇪🇸 **"¿Ahorros en bitcoin?"**). The production prompt (v5) declines to
   advise and offers to book a licensed advisor.
2. Click **Open in Langfuse** and go straight to the **Scores** tab. Don't walk
   the tree yet. The app has already written its deterministic scores at the
   end of the turn: `security-risk`, `guardrail-blocked`, `pii-in-input`,
   `language-match`, `intent` = `investment-advice`, `task-outcome` =
   `declined-advice`, `advisor-offered`, `failure-mode`, `value-usd`,
   `turn-cost-usd`, `turn-latency-s`. These scores cost nothing and run on
   every turn.
3. Refresh after a few seconds (the judge took a median of 6 s and p90 9 s on
   this project) → `banking-compliance` and `faithfulness` arrive **with
   reasoning**. Read the compliance reasoning aloud: it names the policy rules
   it checked.
   *Fallback if the judge is slow:* the same question, already scored:
   [`b31717e3…`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/b31717e318b6bbea090b177effd841cb)
   (compliance 1.0, faithfulness 1.0, advisor offered).
4. **Evaluators** (LLM-as-a-Judge) → three judges. Open the
   `banking-compliance` rule:
   - **target:** the assistant's root observation (`northwind-assistant`),
     environment `production`, **minus the turns the guardrail blocked**
     (tags `risk:prompt_injection`, `risk:cross_customer_access`,
     `risk:social_engineering`). Experiment traces never burn judge tokens.
   - **variable mapping:** `{{query}}` ← input, `{{generation}}` ← output.
     For `faithfulness`, `{{context}}` ← `metadata.context`, which holds the
     articles and tool results the model actually saw. *This is why we trace: the
     trace is the evidence the judge reads.*
   - **sampling:** 100%.
5. Two cost decisions sit in these rules:
   - **Don't judge what you already decided.** A blocked turn always gets the
     same fixed refusal, so every judge skips it. The guardrail's own scores
     (`guardrail-blocked`, `security-risk`) already record the decision.
   - **Sample the safety net.** `manipulation-resistance` runs on a **20%
     sample** of the answered turns, so an attack the rules missed still gets
     judged. Sampling sets judge cost separately for each evaluator.

**Land.** Every answer gets three layers of evaluation: free deterministic
checks on every turn, targeted LLM judges with filters and sampling you control,
and human signal (the customer's 👍/👎 is the `user-feedback` score; experts
come next).

**Ask.** "Which of the five questions would your risk or compliance team want to
own first, and who signs off on the rubric?"

---

## Act 2 · Grade the grader (5 min) — judge calibration

**Frame.** "An LLM judge is a model too. Before its number goes on a dashboard
your CRO looks at, it has to agree with your experts. If it doesn't, you're
automating someone's guess."

**Show.**
1. **Datasets → `judge-calibration/faithfulness`**. It holds two kinds of item
   (`metadata.source`):
   - **synthetic** (16): answers whose correct verdict is **known by
     construction**: faithful (1.0), a minor slip (0.5), or a seeded material
     error such as a wrong fee or deadline (0.0);
   - **production** (27): real answers a reviewer labelled with
     `sme-faithfulness`, copied with exactly what the live judge saw and linked
     to the source trace.

   The judge prompt is versioned in Prompt Management
   (`judge-calibration/faithfulness`) and is identical to the live evaluator.
2. **Choose the judge model** on the synthetic items: one run per candidate,
   same rubric (v1) and same 15 items, with a Boolean
   `faithfulness-judge-output-correct` evaluator. Several runs share each name,
   so open the ones linked here:

   | judge model | accuracy | run |
   |---|---|---|
   | claude-sonnet-4-6 (the live judge) | **1.00** | [`aa64b188`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/aa64b188-9a42-4379-9e9c-7c88cc86c7a2) |
   | claude-haiku-4-5 | 0.87 | [`3cc6bce7`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/3cc6bce7-df72-4cd6-9e3f-bf24958fa314) |
   | gpt-4.1-mini | 0.80 | [`62bb3fd9`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/62bb3fd9-a074-4ecf-8326-f9c637dfcddf) |
   | claude-sonnet-5-5 | 0.73 | [`c486cb45`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/c486cb45-9025-4955-a60c-7196a7df63fb) |

   Every candidate catches the material errors. They differ on nuance: Haiku's
   0.87 hides 60% on minor slips. The newest model is not automatically the
   best judge for *this* rubric.
3. **Now add production.** The live judge (rubric v2) on all 43 items,
   [`8e806919`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/8e806919-ad3b-4fca-8d1a-2b812b7612a8):
   **synthetic 1.00 (16/16), production 0.81 (22/27)**. This is the key moment of
   the act. Synthetic items prove the judge catches the errors you planted.
   Only production labels show where it disagrees with people.
4. Why there is a rubric v2: [`bff8d960…`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/bff8d9603c5fec585bdf387db0f40193)
   (*"¿Cómo desactivo la protección de sobregiro?"*) scores **0.5** on v2
   because the answer invents navigation steps that are not in KB-302. The same
   answer text in [`a2db1c01…`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/a2db1c01b319bd6cf8756da62d9f9621)
   scored **1.0** on v1. The rubric was tightened, then re-calibrated.
5. The judge's live scores against people: **Annotation queues → *SME review —
   assistant answers*** (worst first: thumbs-down and the lowest judge scores,
   then a random sample; labels `sme-faithfulness`, `sme-compliance`,
   `sme-failure-mode`) → **Scores → Analytics**: `faithfulness` vs
   `sme-faithfulness` on the same observations. The pass/fail agreement (from
   `scripts/judge_calibration.py`, pass ≥ 0.75):

   > 26 matched pairs · **81% agreement** · **Cohen's kappa 0.18**

   81% sounds good. But the reviewer passed 22 of the 26 answers, so a judge
   that always said "pass" would have agreed **85%**, better than this one. The
   judge caught **1 of the 4** answers the reviewer marked down, and it failed 2
   answers the reviewer passed. Kappa corrects for the easy majority and scores
   the judge on the hard cases. 0.18 is far below the 0.6 a bank should require.
6. Open the two judge-0 / reviewer-1 disagreements:
   [`aeb2ce3d…`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/aeb2ce3d57fb6ba3071b44044dca59bb)
   and [`1276a680…`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/1276a680b6667d6db025c36759a726c7).
   Both have the same reasoning: *"there is no retrieval or tool evidence in the
   context."* The first is a follow-up turn (*"And what's the cut-off time if I
   send it today?"*). The assistant answered *"I already have that information
   from my previous search!"*, but the judge's context held only **this**
   turn's evidence. That was a defect in the evaluator's input, not in the agent
   or the rubric, and only a comparison with people could reveal it. It's fixed:
   the agent now adds the last turns to `metadata.context`, and these traces
   predate the fix.

**Land.** Calibrate the judge against people, on production answers, before you
let the judge calibrate the product. Use kappa, not percent agreement, and read
every disagreement. Some of them will be problems with the labels, not the
judge.

**Candour.** Most production labels in this project are scripted stand-ins
(`metadata.label_source` = `API`) until an expert reviews them; items labelled in
the queue show `ANNOTATION`. A real calibration set needs 50–100
expert-labelled items per criterion.

**Ask.** "Who would be the expert for card and payments policy, and how many
labels a week could they give?"

---

## Act 3 · Is the new prompt better? (5.5 min) — datasets and experiments

**Frame.** "The product owner rewrote the prompt: don't cross-sell, cite the
article, formal Spanish, offer an advisor instead of turning investment
questions away. It reads better. Is it better, and better at what?"

**Show.**
1. **Datasets → `northwind-golden-qa-v1`**: 16 items. Open one: input,
   **expected output**, expected sources, must-include facts (two items are in
   Spanish). This is the spec, and it was written before the prompt.
2. Select three runs → **Compare**. Several runs share a name, so pick them by the
   links below:

   | run | prompt | correctness (judge) | must-include | source-recall | cites-expected-source | no-unsolicited-upsell | formal-register (ES items) |
   |---|---|---|---|---|---|---|---|
   | [`production · claude-sonnet-4-6`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/d0a9afed-6401-4e71-9b76-aaf354962bbe) | v1 (baseline) | 0.91 | 1.00 | 0.88 | 0.88 | 0.92 | 0.00 |
   | [`ci · staging v5 · EN · #28`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/384eebee-0048-4e4c-b8c5-9ab0b7dbb37d) | v5 (candidate) | 0.91 | 1.00 | 0.88 | 0.88 | **1.00** | **1.00** |
   | [`production · gpt-4.1`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/b686530c-8a60-496d-9556-2549f25498ee) | v1, other model | 0.88 | 0.94 | 0.88 | **0.56** | 1.00 | 0.50 |

3. 🇪🇸 **`northwind-golden-qa-es-v1`** (10 Spanish items, reference answers in the
   *usted* register): [v1](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/aa375259-5822-4948-837b-fe8100c830f5)
   vs [v5](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/e86bc13b-26cc-423d-bea7-76ae4e576de0):
   `formal-register` **0.00 → 1.00** (v1 addresses every Spanish customer as
   *tú*), `correctness` 0.85 → 0.85, `language-match` and `must-include` 1.00 on
   both.
4. Read it with the room:
   - The judge's `correctness` **did not move**: 0.91 = 0.91 and 0.85 = 0.85.
     Even if it had moved by three points, that is noise at 16 items.
   - The candidate wins exactly where it was designed to, on **deterministic**
     metrics you can open and read: formal Spanish 0 → 1, upsell 0.92 → 1.00.
   - Model comparison, same prompt and items: GPT-4.1 cites the expected policy
     article about half as often (0.56 vs 0.88). Open one item side by side.
     Cost and latency per run are on the same page.

**Candour.** The single upsell flag on v1 is a **false positive** of the
evaluator (*"Would you like to open a dispute?"* matched the rule). The rule has
since been narrowed to recommendation language. Deterministic evaluators need
review too.

**Land.** Decide on the metrics you can read. Treat the judge's average as a
smoke alarm, not a verdict.

**Ask.** "What is in your golden set today, who wrote the expected answers, and
when were they last reviewed?"

---

## Act 4 · Let the gate say no (4 min) — the CI quality gate

**Frame.** "Someone rewrites the prompt to 'grow customer relationships' and adds
'always answer in English'. It reads well. Should it ship, and should a person
have to catch it?"

**Show.**
1. GitHub Actions → *Northwind prompt gate* → the red run on the `development`
   prompt (v3),
   [37786226009](https://github.com/doneyli/clickhouse-llm-observability/actions/runs/37786226009):

   ```
   metric                         value  threshold   result
   avg-must-include               1.000       0.90   PASS (hard)
   avg-source-recall              0.875       0.80   PASS (hard)
   avg-language-match             0.875       1.00   FAIL (hard)
   avg-no-unsolicited-upsell      0.385       0.90   FAIL (hard)
   avg-correctness                0.969       0.80   PASS (soft)
   ✗ GATE FAILED — 'development' must not be promoted to production.
   ```

   The same run is in Langfuse: golden dataset → [`ci · development v3 · EN · #17`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/1d55c6a1-0ea4-4218-ba46-6b6177a56328).
   Open an upsell item and read the pitch.
2. `cicd/thresholds.json`: **hard** thresholds on deterministic metrics fail the
   build. **Soft** thresholds on judge averages only warn. A missing metric counts
   as a failure. The Spanish set adds `formal-register` and requires
   `language-match` = 1.0.
3. The wiring is live on this project. A Langfuse prompt automation (new
   version, or a label moved) sends a GitHub `repository_dispatch`, which runs
   `scripts/prompt_gate.py` on EN + ES, and exit 1 fails the check. The green run on
   the candidate, [37786230303](https://github.com/doneyli/clickhouse-llm-observability/actions/runs/37786230303),
   passes in both languages.

**Land.** The LLM judge gave the regression **0.97** and would have shipped it.
The deterministic checks blocked it. Gate hard on what you can read, and warn on
what a model judged.

**Ask.** "Where would this gate sit in your release process, and who gets paged
when it's red?"

> *Live variant (optional, costs ~16 agent turns + judge calls):* start
> **Presenter console → CI quality gate** at the start of Act 3. It streams while
> you talk, and by Act 4 the red verdict is on screen live.

---

## Act 5 · Production teaches the dataset (5 min) — outcomes, not answers

**Frame.** "Offline evals cover only what you thought to test. Here's a failure
nobody wrote a test for, and every LLM judge passed it."

**Show.**
1. **Dashboards → *Northwind — Business value & failure modes*** → *Dispute
   self-service rate by trace version*: `assistant-1.5.0 · prompt v1` is the low
   bar (**0.20**). *Failure modes* shows `dispute-not-resolved`. These are
   business-outcome scores the app writes on every turn: `dispute-resolved` = the
   dispute was actually opened and a case id returned.
2. Open the [hero trace](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/a4e261601983c78e3fd00fcebacec479?observation=a60d0396885d223a)
   (it opens on the MCP server span). The answer says *"I don't see a GADGETSTORE
   ONLINE transaction in your last 30 days"* and cites KB-102, which allows
   **60** days. Scores: `faithfulness` 1.0, `banking-compliance` 1.0. The answer
   was faithful to what the tool returned. Now the trace earns its keep:
   `mcp-client` sends `days: 30` (a code default the model never chose), and the
   **MCP server**, another service in the same trace, returns `window_days: 30`.
   The 41-day-old charge was never fetched.
3. Turn the failure into a test. **Datasets → `northwind-disputes-v1`**: 10 items
   (EN + ES, 8 older-charge disputes + 2 recent controls), evaluator
   `dispute-opened` = `open_dispute` called **and** a `DSP-######` case id in the
   answer. Compare
   [release 1.5.0 · lookback 30d](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/58fd32ff-6cc5-4c94-914a-15748fac446e)
   vs [release 1.5.1 · lookback 60d](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuzk1tip00erad0e3rlr0cz7/runs/a0d3edd2-ab96-43c0-a61e-5dc5185ee746):
   `dispute-opened` **0.20 → 1.00**, `language-match` 1.00 on both.
4. Live traffic after the fix: conversations ending with a dispute opened went
   from **2/10 to 10/10**. [After trace](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/1be9e4f473f5f7a0e939f77f47e5341a):
   same request, dispute opened on the right transaction.

**Land.** Evaluate outcomes, not just answers. The production failure became a
dataset, the dataset proved the fix, and the fix is now a regression test that
runs on every future release.

**Candour.** The samples are small (10 items, 10 conversations per release), so
read the direction, not the decimals. The 1.5.1 agent often shows the charge
and asks *"shall I open it?"* first, so judge this one per conversation (10/10)
rather than per turn.

**Ask.** "Which business outcome would your sponsor want measured on day one:
containment, cost per contact, complaints, or conduct risk?"

---

## Close · the loop and the first two weeks (2.5 min)

**Show** (slide): define good → grade every answer → grade the grader → prove
changes offline → gate the release → watch outcomes in production → failures
become dataset items → back to the start.

**An evals-first POC, first two weeks:**
- **Week 1.** Agree the five questions with risk and compliance. Instrument one
  app (LangGraph: one callback handler). Have an expert read 30–50 production
  traces and write down what goes wrong. Those notes become the first failure
  modes. Ship the deterministic checks first, then one judge.
- **Week 2.** Build a calibration set from **production** answers (50–100
  expert labels) and approve a judge model only at kappa ≥ 0.6 in every
  category. Seed the golden set from real failures, not invented questions. Put
  the gate in CI, with hard thresholds on deterministic metrics.

**Ask.** "What would make this POC a clear yes for each of you: data science,
analytics, architecture?"

---

## Presenter prep (T-30 min)

1. Free the Docker VM: stop any other local stacks (the main repo's
   `./setup.sh --cleanup`). On 2026-10-08 memory pressure killed the VM
   mid-session.
2. Restart the MCP server for a clean banking state, then the portal:
   `kill $(lsof -tiTCP:8765 -sTCP:LISTEN); ./scripts/run_portal.sh`.
3. `.venv/bin/python scripts/verify_demo.py` → **READY**. This cut uses neither
   n8n nor the APM stand-in. If you haven't run `./scripts/up.sh`, their checks
   (and the `n8n-complaint-triage` trace check) are the failures you can ignore.
4. The judge rules on the project come from this version of
   `scripts/seed_evals.py`: judges skip blocked turns, and the 100%-on-attacks
   `manipulation-resistance` rule is disabled. An older `seed_evals.py` would
   re-enable it, so don't run one against the project.
5. Presenter console → *Generate production traffic*, so the last-hour views have
   scored turns.
6. `scripts/prompt_label.py --show` should read: production = **v5**, staging =
   v5, development = v3, baseline + previous-production = v1. This cut moves no
   labels.
7. Rehearse the bitcoin chip once and look at its Scores tab before the room does.
8. Open the tabs: portal · trace fallback `b31717e3…` · Evaluators · calibration
   dataset · SME queue · golden EN + ES compare · GitHub run 37786226009 ·
   business dashboard · disputes hero trace · disputes dataset compare.

## Backup topics (if the room goes there)

| Question | Show | Source |
|---|---|---|
| "Can the judge be wrong in the other direction?" | the compliance judge flagged showing the last four card digits as a P2 violation; fixed the judge (evaluator v2), not the agent | DEMO_SCRIPT Issue 1 |
| "What about cost?" | release 1.6.0 retrieves 8 articles, +13% cost per turn, quality flat; one retriever observation explains it | [docs/arcs/cost-regression.md](docs/arcs/cost-regression.md) |
| "Does the judge see customer PII?" | no: masking happens before export and keeps only the last four digits (`[REDACTED_CARD ****1111]`), so the judge can still check "ending 1111": [`22363dc5…`](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/22363dc535017e38d0325b484d479c40), `faithfulness` 1.0 | DEMO_SCRIPT Act 1.3 |
| "Prompt injection?" | red-team dataset `northwind-redteam-v1`, `refused-safely`; rules guardrail + sampled judge | DEMO_SCRIPT Act 2.2 |
| "How do we ship and roll back?" | move the `production` label; live in ~10 s; protected label + audit log | DEMO_SCRIPT Act 5.4 (check label state first) |

---

## Show me the code

| What | Where | Why it matters |
|---|---|---|
| Deterministic scores on every turn (safety, language, register, citations) | `northwind/agent.py:526` `_score_turn` | free, instant, the backbone of the gate |
| Business-outcome scores (`task-outcome`, `contained`, `value-usd`, `failure-mode`) | `northwind/agent.py:558` `_score_business` | ties quality to what the sponsor measures |
| `dispute-resolved` outcome score | `northwind/agent.py:581` `_score_dispute` | the score that caught what the judges missed (Act 5) |
| Judge targeting: root observation, production only, minus blocked turns | `scripts/seed_evals.py:42` `ROOT_FILTER`, `:53` `ANSWERED_FILTER` | experiment traces and fixed refusals never burn judge tokens |
| Retired rules are disabled, never deleted | `scripts/seed_evals.py:56` `RETIRED_RULES` | the rule history stays auditable |
| Managed judges as code: variable mapping, faithfulness rubric v2, 20% sampled safety net | `scripts/seed_evals.py:63` `MAPPING`, `:69` `FAITHFULNESS`, `:105` `JUDGES` | judges are versioned and reviewable like any code |
| Offline evaluators | `northwind/evals.py:44` `must_include`, `:84` `formal_register`, `:93` `no_unsolicited_upsell`, `:133` `dispute_opened`, `:188` `correctness` | plain Python functions; the same ones run in CI |
| Run-level averages the gate reads | `northwind/evals.py:215` `averages` | one number per metric per run |
| Experiment runner | `scripts/run_experiment.py:71` `ds.run_experiment(...)` | runs the full agent (RAG + MCP tools) on every item |
| Gate logic: hard fails, soft warns, exit 1 | `scripts/prompt_gate.py:69`, thresholds in `cicd/thresholds.json` | the "no" is automatic |
| Calibration set: synthetic + production items | `scripts/judge_calibration_experiment.py:144` `production_items`, `:163` `seed` | production labels, with exactly what the judge saw |
| Calibration scoring | `scripts/judge_calibration_experiment.py:243` `correct`, `:249` `accuracy` | accuracy per source and category, not one blended number |
| Judge vs human agreement (kappa, pass ≥ 0.75) | `scripts/judge_calibration.py` | the numbers in Act 2 step 5 |

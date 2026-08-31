# Full-Lifecycle Feedback → Agent Engineering

**An SA enablement runbook.** ~20 minutes of demo, built around a single thread:
one user complaint travelling the entire AI Engineering loop until it comes back
out as a shipped improvement.

> **Why this runbook exists.** The [six-act Property Concierge
> script](../demos/real-estate/DEMO_SCRIPT.md) is a *tour* of the loop — excellent
> for a team evaluating the platform feature by feature. This is a *narrative*:
> narrower, faster, and it answers the question teams who already have tracing
> actually ask — **"fine, but how do we get from a bad answer to a better
> agent?"** Same app, same stack, one thread instead of six.

---

## The spine — memorize this sentence

> *A user thumbs down one answer. That answer becomes a test case. The test case
> proves a prompt fix. CI blocks the bad prompt and passes the good one. Promoting
> a label ships it with no redeploy — and the next request is a new trace.*

Everything below is that sentence, on screen, with real data. If you remember
nothing else, you can run this demo from that sentence alone.

**The trap to avoid:** do not open with the loop diagram. Teams have seen the
diagram. Open with the complaint, and let the loop *emerge* — then show the
diagram at the end as the thing they just walked. Show-then-name, not
name-then-show.

---

## Pre-flight

### This demo requires Langfuse Cloud

Not a preference — a constraint. Movement 4 runs a **real GitHub Actions
workflow**, and a GitHub runner cannot reach `localhost:3001`. So the demo points
at Langfuse Cloud (`us.cloud.langfuse.com`, project `real-estate`).

> **Paste-safety note (read once).** Every command block below is comment-free on
> purpose. Interactive **zsh** has `interactive_comments` off by default, so a `#`
> is *not* a comment — it becomes an argument. Pasting `cp .env.cloud .env  # note`
> gives you `cp: config: Not a directory` and copies nothing. If you want inline
> comments to work in your shell, `setopt interactive_comments` first.

```bash
cd demos/real-estate
cp .env.cloud .env
./.venv/bin/python -c "from agent.config import verify_project; verify_project()"
```

That should print `✓ Langfuse project verified: real-estate @ https://us.cloud.langfuse.com`.
Your previous config is preserved in `.env.selfhosted.bak`.

**Only on a fresh clone** — if `.venv/` already exists, skip this entirely:

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Note `python -m pip`, not `./.venv/bin/pip`. A venv that was created before this
demo moved into `demos/` (PR #28) has console scripts with a **hardcoded shebang to
the old path**, so `./.venv/bin/pip` fails with `bad interpreter: .../real-estate-demo/.venv/bin/python3.11`.
`./.venv/bin/python` is a symlink and is unaffected — which is why everything else
works. `python -m venv` without `--clear` does not rewrite those scripts, so
re-running it does not fix pip. Going through `python -m pip` sidesteps the
shebang entirely. **Do not rebuild a working venv on demo day** to chase this.

### Seed the data (~25 min, do this the day before)

```bash
./.venv/bin/python scripts/seed_prompts.py
./.venv/bin/python scripts/seed_dataset.py
./scripts/seed_managed_evaluators.sh
./.venv/bin/python scripts/seed_annotation_queue.py
./.venv/bin/python scripts/run_live_traffic.py
./.venv/bin/python scripts/run_experiment.py --prompt-label first-draft
./.venv/bin/python scripts/run_experiment.py --prompt-label production
./.venv/bin/python scripts/run_experiment.py --prompt-label candidate
```

| Step | What it gives you |
|---|---|
| `seed_prompts.py` | the three labelled versions: `first-draft`, `production`, `candidate` |
| `seed_dataset.py` | `property-concierge-eval`, 18 items |
| `seed_managed_evaluators.sh` | Anthropic LLM connection + the two managed judges |
| `seed_annotation_queue.py` | human-review queue + score configs |
| `run_live_traffic.py` | traces including the fault-injected ones |
| `run_experiment.py --prompt-label first-draft` | **the BEFORE** |
| `run_experiment.py --prompt-label production` | **the AFTER** |
| `run_experiment.py --prompt-label candidate` | for the rigor beat |

All of it is idempotent, and `./run_demo.sh --lifecycle` wraps most of it. The
`run_experiment.py` calls are the slow part — **~12 min each**, so budget ~40 min
if you run all three.

### Stage the user complaint (the demo's opening shot)

The story needs a *genuinely* bad answer with a *real* 👎 on it. Run the portal on
the naive prompt, ask one question in Spanish, thumb it down, then put the portal
back:

```bash
PORTAL_PROMPT_LABEL=first-draft ./run_portal.sh
```

It will print `serving prompt label: first-draft   <-- NOT production!`. Then, in
the browser:

1. Ask: *"Piso de 2 dormitorios para comprar en Madrid por menos de 400.000 euros"*
2. It answers in **English** — that's the bug. Click **👎**.

Now put the portal back:

```bash
./run_portal.sh
```

**Verify it prints `serving prompt label: production` before you present.**
Presenting on a staging leftover is the single most embarrassing way to lose this
demo — which is exactly why the portal prints the label on startup.

One more staging step, easy to forget: the managed judges score **new** traffic
only, so the complaint trace you just made has code scores and the 👎 but no
Helpfulness/Relevance. Backfill it so Movement 1 can point at all four feedback
channels on one trace: **Traces → select the trace → Actions → Evaluate**.

### Managed judges — scripted, not manual

The Anthropic LLM connection and the two Langfuse-managed judges (`Helpfulness`,
`Relevance`) are provisioned by script on Cloud too, via the unstable
evaluation-rules API — no UI clicking required:

```bash
./scripts/seed_managed_evaluators.sh
```

The rules are **observation-level**, filtered to the root span
`handle-concierge-chat-message`, and they score **new** traffic only. So a trace
created *before* the rules existed — likely including your staged complaint —
won't have Helpfulness/Relevance on it. Backfill it in the UI:
**Traces → select the trace → Actions → Evaluate**. Worth doing before you
present, so Movement 1 can point at all four feedback channels on one trace.

*(Dataset experiments are unaffected either way — they run all four judges
client-side, which is why the gate needs no managed evaluators at all.)*

### The one genuinely manual step: GitHub CI wiring

Repo secrets, a PAT, and the Langfuse automation — these involve creating
credentials, so they're yours to do. Full steps in
[`demos/real-estate/cicd/README.md`](../demos/real-estate/cicd/README.md).

### Stage the screen

Four tabs, in this order — you'll move left to right and never backtrack:

| Tab | What's on it |
|---|---|
| 1 | The portal, `http://localhost:8080` |
| 2 | Langfuse → Tracing, filtered to the complaint's session |
| 3 | Langfuse → Datasets → `property-concierge-eval` → Runs |
| 4 | GitHub → Actions tab |

---

## Movement 1 · The complaint (4 min)

**Say:** "A user asked our property assistant a question in Spanish. Here's what
they got."

**Show.** Open the complaint trace (Tab 2). Point at three things, in this order:

1. **The answer** — the question was Spanish, the answer is English. A real
   product failure a user would notice immediately.
2. **`user-feedback = 0`** — they told us. That's the cheapest, highest-signal
   feedback there is, and it lands as a score on the trace.
3. **`language-match = 0`** — and our automated eval caught the *same* thing,
   independently, with a comment naming the exact failure. Everything else on the
   trace is green: it *did* search, the listing *is* real, it *was* in budget.

**Land.** "Two independent signals agree, and the automated one localizes the
bug for you. This is the difference between 'a user complained' and 'a user
complained, and here is the failing check, on the exact step that produced it.'"

**Teaching note for SAs:** the reason this lands is the *orthogonality* of the
code evaluators — each one catches exactly one failure mode, so a red score is a
diagnosis, not an alarm. If a customer asks "why is grounded-listings still
green?", that's the answer, and it's a feature.

### The four feedback channels — name them here, fast

Don't belabor this; the audience needs the vocabulary for the next movement.

| Channel | On this trace | Cost |
|---|---|---|
| **User feedback** 👍/👎 | `user-feedback` | free, sparse, highest signal |
| **Code evaluators** (deterministic) | `used-search-tool`, `grounded-listings`, `budget-adherence`, `location-match`, `language-match` | free, runs on 100% of traffic |
| **LLM-as-a-Judge** | `helpfulness`, `relevance` (managed by Langfuse) · `groundedness`, `tone` (our SDK) | costs a call, covers the subjective |
| **Human annotation** | Annotation Queues → *Property Concierge - human review* | expensive, becomes your gold standard |

Point at the **Source** column in the Scores table: `API` for our code and SDK
judges, `EVAL` for Langfuse-managed ones, `ANNOTATION` for human labels. "Three
paths in, one place they land."

---

## Movement 2 · The complaint becomes a test case (3 min)

**Say:** "Here's the step most teams skip, and it's the one that compounds."

**Show.** On the complaint trace, **add it to the dataset** —
`property-concierge-eval`. One click in the UI. Then open the dataset (Tab 3) and
show it sitting among the other 18 items, each with an `input` question and an
`expected_output` carrying the ground-truth constraints.

**Land.** "That bug can never silently come back. It's a regression test now.
Frame it the way an engineer already thinks: **the dataset is your test suite, an
experiment is a test run, and a score threshold is the quality gate.** The only
difference from unit tests is that the assertions are statistical."

**Teaching note:** this is the moment to say out loud that you *cannot* unit-test
your way to confidence with a probabilistic system — which is precisely why the
loop exists. Say it once, here, and don't repeat it.

---

## Movement 3 · Prove the fix (6 min) — the money moment

**Say:** "We have a hypothesis: the prompt is the problem. Let's prove it instead
of arguing about it."

**Show.** Langfuse → **Prompts** → `property-concierge-agent`. Three labelled
versions, with the diff right there:

- **`first-draft`** — what shipped. Reads like a real first draft, because it is
  one: enthusiastic, "include slightly pricier properties too — buyers often
  stretch", and *"always reply in polished English"*.
- **`production`** — the fix: answer in the user's language, never exceed the
  stated budget, only cite retrieved listings.
- **`candidate`** — a further refinement (tighter grounding, scannable format).

Point at the two lines in `first-draft` that caused the bug. "Nobody was
careless. Someone wrote a friendly prompt in English and shipped it. That's the
normal case."

Now **Datasets → Runs** → select the `first-draft` and `production` runs →
**Compare**. Same agent, same 18 questions, same evaluators. **Only the prompt
changed.**

### The numbers you will see

Measured on Claude (`claude-sonnet-4-6`), 18 items, 2026-07-30. **Each of
first-draft and production was run twice** — keep both repeats in the compare view,
they are the whole rigor beat.

| Metric | `first-draft` | ↻ repeat | `production` | ↻ repeat | `candidate` |
|---|---|---|---|---|---|
| `budget-adherence` | **0.944** | 0.944 | 1.000 | 1.000 | 1.000 |
| `language-match` | **0.833** | 0.833 | 1.000 | 1.000 | 1.000 |
| `used-search-tool` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `grounded-listings` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `location-match` | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| `helpfulness` (judge) | 0.904 | 0.894 | 0.889 | 0.919 | 0.909 |
| `relevance` (judge) | 0.889 | 0.863 | 0.892 | 0.933 | 0.918 |
| `groundedness` (judge) | 0.943 | 0.893 | 0.924 | 0.949 | 0.929 |

**Land the first half.** "Both bugs are gone. `language-match` went from 83% to
100% — Spanish questions were being answered in English, and now none are. Same
suite, same evaluators, prompt as the only variable. And `language-match` reads
0.833 on the naive prompt in **every** run we've done — four of them — because an
English-only instruction breaks the same three Spanish items every time. That's a
test you can trust."

> **Precision matters here, and it cuts against a tempting oversimplification.**
> Code evaluators are not magically stable — `budget-adherence` ranged 0.944–1.000
> across those four runs, because *which listings the agent cites* varies. But a
> code eval is an **exact function of the output**, so when it moves you can open
> the trace and see why. That's the real distinction from judges: both vary, only
> one variance is explainable. If an audience member catches the varying
> `budget-adherence`, that's not a gotcha — it's the better version of the point.

### The rigor beat — the most valuable 90 seconds in the demo

**Do not skip this, and do not soften it.** Point at the bottom three rows, then at
the repeat columns.

**Say:** "Now the judges — and remember, these repeat columns are the *same prompt,
run again, nothing changed but the dice*. Groundedness on first-draft went 0.943 to
**0.893**. That's a 0.050 swing from nothing at all, and it's **bigger than every
prompt-to-prompt judge difference on this table.**"

Then the kill shot: "Look at first-draft's judge scores against production's. They
overlap completely. So on judges, the prompt with two real defects is
**indistinguishable** from the prompt that fixed them."

**Land.** "Which means a judge number here tells you which run you happened to open,
not which prompt is better. If you'd graded these three prompts on helpfulness,
you'd have concluded nothing — or something false, depending on the roll. That's
what [`thresholds.json`](../demos/real-estate/cicd/thresholds.json) encodes: gate
hard on the deterministic checks, gate judges loose at 0.80 as a smoke alarm for
catastrophic drops. Not as a ruler."

**Teaching note for SAs — why this beat exists.** This is the fastest way to earn
credibility in a technical room, because you're arguing against your own demo using
your own data, and you brought the control run to prove it. It also inoculates the
customer against the most common self-inflicted wound in this space: standing up an
LLM judge, watching the number, and shipping on it. If you take one thing from this
runbook, take this.

**Always run the repeat.** A single run per prompt cannot tell signal from noise,
and presenting one would be committing the exact error you're warning against:

```bash
./.venv/bin/python scripts/run_experiment.py --prompt-label production --run-name production-repeat
```

Re-running under the *same* `--run-name` replaces the run; a distinct name is what
gets you two comparable rows.

**If someone asks "so are judges useless?"** No. They cover what code can't — tone,
coherence, whether the answer actually addressed the question — and they'd catch a
catastrophic drop immediately. They're the wrong instrument for adjudicating a
2-point difference, which is precisely what the loose thresholds say.

**If someone asks "so how DO you evaluate subjective quality?"** Three honest
answers: widen the dataset until the effect size clears the noise; run each variant
several times and compare distributions rather than single means; and calibrate
against the human-labelled set from the annotation queue. What you don't do is
promote on one judge number.

---

## Movement 4 · CI blocks the bad prompt (4 min)

**Say:** "Everything so far was a human deciding. Let's make it a pipeline."

**Show.** Open [`cicd/thresholds.json`](../demos/real-estate/cicd/thresholds.json).
"This is the quality bar, as code. Reviewable, diffable, in git. Deterministic
checks gated hard; judges gated loose, on purpose, because a tight judge
threshold gives you flaky builds."

Then GitHub → **Actions** → *Langfuse Prompt CI* → **Run workflow** → pick
**`first-draft`**.

While it runs, explain the trigger: "In normal operation nobody clicks this.
Promoting a prompt version in Langfuse fires a `repository_dispatch` and this
workflow starts on its own. I'm using the manual dropdown so we don't have to
wait for a promotion."

The run goes **red**. Open the job summary — a verdict table with the blockers
named, and:

> This prompt version must not be promoted to `production`.

> **Narrate `avg-language-match`, and only that one.** Across four first-draft
> runs (2 local, 2 in CI) it was the blocker every time — 0.833 against a 1.0 bar.
> `avg-budget-adherence` tripped in only 2 of those 4; it sits 0.006 under the bar
> at worst and one differently-chosen listing flips it. The build goes red either
> way. Say "a check failed" and read what actually renders.

Point at the skipped **deploy** job. "The gate isn't advice. The deploy literally
did not run."

Then run it again on **`candidate`** — it goes **green**, and the deploy job is
*still* skipped, because that version isn't labelled `production`. "Validated is
not the same as deployed. Two independent conditions."

**Land.** "So the question 'what stops a bad prompt reaching your users?' has a
file for an answer. Not a process document, not someone remembering to check a
dashboard — an exit code."

**Teaching note:** the local rehearsal command is the same code CI runs, which is
worth saying:

```bash
./.venv/bin/python scripts/prompt_gate.py --prompt-label first-draft
```

### The counter-beat: the gate catches only what you measure

**Have this ready, because a sharp internal audience will go looking for it** — and
it's better to hand it over than to be caught by it. Someone will ask "so the gate
stops anything bad?" The answer is no, and we have a real example.

We took the `candidate` prompt and changed exactly one thing:

> `"You are a professional real-estate concierge…"` → `"You are **NOT** a professional real-estate concierge…"`

Labelled it `candidate`, the automation fired, CI evaluated it — and it **passed
cleanly**: all five code evaluators at 1.000, and judge scores of
**0.918 / 0.926 / 0.948**, the *highest* of any run that day.

Two reasons, and both are worth saying out loud:

1. **The sabotage was cosmetic, not behavioural.** Every rule the suite actually
   tests survived — search before answering, cite only retrieved ids, respect the
   budget, match the location, match the language. The negation changed the persona
   framing without instructing different behaviour, so the answers stayed good. In
   the dimensions we measure, the prompt genuinely is not worse.
2. **`tone` is measured but not gated.** It's a real judge in
   `agent/scoring.py`, and it lands on live traffic — but it's **absent from
   `RUN_EVALUATORS` and from `thresholds.json`**, because it's *categorical*
   (poor/good/excellent) and the run-level aggregator only averages numerics. So a
   persona or brand-voice regression **structurally cannot fail this gate.**

**Land it.** "A quality gate is not a safety net. It's a contract: it enforces the
dimensions you chose to define, precisely, and it is silent about everything else.
If persona matters to you, that's a scored dimension you have to add — and until
you do, no amount of CI will catch it."

**Teaching note for SAs:** this is the single best inoculation you can give an
internal audience, because the failure mode is *believing the green check means
more than it does*. It also sets up the natural follow-up — "what would you add?"
— which is a genuinely useful conversation about their own quality criteria. If
you want the gate to catch this, the fix is a numeric persona/tone score wired
into `RUN_EVALUATORS` with a threshold; the categorical `tone` judge can't be
averaged as-is.

---

## Movement 5 · Ship it (3 min)

**Say:** "One click left."

**Show.** Langfuse → **Prompts** → move the `production` label. That's the deploy.
No PR, no container build, no redeploy — the app fetches the `production`-labelled
prompt at runtime.

Go back to the portal (Tab 1) and ask the Spanish question again. It answers in
Spanish. Open the new trace: `language-match = 1`.

**Now** show the loop diagram, and trace the thread you just walked with your
finger: complaint → dataset → experiment → gate → deploy → new trace.

**Land.** "One user's thumbs-down changed what production serves, with evidence at
every step and a gate that would have stopped it if the evidence was bad. That's
the loop. It isn't a dashboard you look at, it's a cycle you run."

> **Presenter note:** after promoting, the SDK's prompt cache can serve the old
> version for up to ~60s. Ask an unrelated question first, then the Spanish one.

---

## Failure modes mid-demo, and how to recover

| Symptom | Cause | Recovery |
|---|---|---|
| Portal answers in English when you expect Spanish | portal still on `first-draft` | restart `./run_portal.sh`; check the startup line |
| Traces 401 / never appear | shell-exported `LANGFUSE_*` keys override `.env` | `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST` and restart |
| `verify_project` refuses to run | keys resolve to a different project | intentional guard — check `.env` and `LANGFUSE_PROJECT_NAME` |
| No Helpfulness/Relevance on a trace | managed rules score new traffic only | Traces → select → Actions → Evaluate; the dataset runs always have them |
| Promoted prompt not served yet | SDK prompt cache (~60s) | stall with another question |
| Actions run doesn't start on promotion | PAT expired or automation misconfigured | fall back to the `workflow_dispatch` dropdown |
| Run says "⏭️ Skipped — nothing to evaluate" | the changed version has no deployable label, or a different prompt changed | expected behaviour, not a bug — assign `candidate` or `production` to the version. Explain it; it lands well |
| Gate run takes too long live | 18 items × 4 judges | use a pre-run result; or `--max-concurrency 8` |

---

## Objections you'll get

- **"So the gate stops anything bad from shipping?"** No, and say so plainly — see
  the counter-beat in Movement 4. It enforces the dimensions you defined and is
  silent about the rest. We have a prompt that says *"you are NOT a professional
  concierge"* and passes with the best judge scores of the day, because `tone` is
  measured on live traffic but isn't part of the gate (it's categorical, and the
  run-level aggregator only averages numerics). Volunteering this is far stronger
  than being caught by it.
- **"Isn't the first-draft prompt a strawman?"** No — and answer this head-on. It
  gets the mechanics right (searches before answering, cites ids). Its two bugs
  are an English-only instruction and growth-flavoured budget advice. Both are
  mistakes real teams ship. Offer to show the diff.
- **"Do we need an LLM to evaluate an LLM?"** Not for the checks that matter most
  here — all five code evaluators are plain Python, deterministic, free, and run
  on 100% of traffic. Judges are for the subjective residue.
- **"Is LLM-as-a-Judge trustworthy?"** Treat that as an empirical question: the
  annotation queue gives you human labels to calibrate judges *against*. And note
  we gate on code evals precisely because judges are noisy.
- **"Our prompts live in git, not a UI."** Both, ideally — Langfuse's sync-to-repo
  webhook commits every prompt version to a repo for PR review, while Langfuse
  stays the deployment source of truth. Also mention **protected labels** (Pro +
  Teams add-on / Enterprise / self-hosted EE) so only approvers can move
  `production`.
- **"Who's allowed to promote?"** RBAC + protected labels. Worth flagging as an
  Enterprise-tier answer rather than overselling it.
- **"Why ClickHouse under this?"** Because scoring, filtering and trend charts over
  millions of large-payload traces is an analytics workload. That's also what makes
  "filter every trace where `language-match = 0`" instant instead of a batch job.
- **"We already have Datadog."** Keep it. APM tells you the request succeeded. None
  of what you just watched — was the answer good, did quality regress, can we
  replay it on a test suite — is a thing APM does.

---

## Honest scope — live vs. manual

Be straight about this; SAs who oversell get caught in the follow-up call.

| Capability | Status |
|---|---|
| Tracing, sessions, code evals, custom SDK judges, user feedback | **Live** |
| Datasets, experiments, model + prompt comparison | **Live** |
| Prompt management by label; promoting a label = deploy | **Live** |
| Annotation queue + score configs | **Live** (seeded by script) |
| GitHub Actions quality gate (eval + threshold + blocked deploy) | **Live** — needs Cloud, plus secrets/PAT/automation set up once |
| Managed LLM-judge evaluators (Helpfulness/Relevance) on Cloud | **Live** — scripted via the unstable evaluation-rules API; scores new traffic only, backfill older traces from the UI |
| Prompt sync-to-repo (commit each version to git) | **Documented** — needs a public webhook endpoint |
| Protected prompt labels / approval workflow | **Not in this demo** — Enterprise-tier feature; describe, don't promise |

---

## The 8-minute version

When the slot collapses — and it will:

1. The complaint trace: 👎 plus `language-match = 0` (2 min).
2. Add to dataset, one click, one sentence about test suites (1 min).
3. The `first-draft` vs `production` compare (3 min).
4. The red CI run and the skipped deploy job (2 min).

Drop the rigor beat, the annotation queue, and Movement 5. The spine still holds.

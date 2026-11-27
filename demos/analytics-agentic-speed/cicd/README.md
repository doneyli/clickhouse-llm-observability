# CI/CD: the prompt quality gate

## What the gate proves

`scripts/prompt_gate.py` takes a prompt **label** and answers one question before that label gets
`production`: does the agent still get the answers right, at the same cost, with these prompts?

The agent fetches two prompts by label, `analytics-agentic-speed-planner` and
`analytics-agentic-speed-analyst`, so a label names a **pair**. The gate resolves both at the label
and aborts if either lacks it: the agent would otherwise fall back to its in-code text without a
word, and the gate would grade a prompt nobody wrote. To stage a planner-only change, put the
same label on the analyst's current production version too.

The label is resolved once per prompt during pre-flight. Every task then fetches those
exact version IDs, without a local fallback, so a later label move cannot change the pair
being evaluated. The run summary records the same IDs the tasks use.

It then runs the `scenario-explorer-questions` dataset (15 what-if questions whose answers come
from exact reference SQL, so no LLM judge is involved) at the standard Experiment A wide config:
300 scenarios per round, 2 rounds, against the ClickHouse Cloud flights table. It reuses
`scripts/run_experiment.py`'s task, evaluators and Langfuse read-back, so a gate run is an ordinary
dataset run in the experiment **Prompt gate**, with trace version `gate-<label>`.

What a pass means: on these 15 questions, the label is at least as accurate as the bar, gets the
hero question right, and costs no more per question than the ceiling. What it doesn't mean: that
the label is better than production (15 questions can't resolve a one-question difference), that
it generalizes to questions outside the set, or anything about latency SLOs. Use
`run_experiment.py --compare prompt-labels production,<label>` to compare two labels head to head.

## The thresholds (`thresholds.json`)

The baseline is production v1 (planner v1, analyst v1) on claude-opus-5-5, run
`breadth-300-20261126T232112Z`: 12/15 correct, $0.1445 and 66.3 s per question, ClickHouse 6.7% of
wall time, 418.5 scenarios and 3.6 model calls per question, no errored items. Bounds were chosen
relative to that one historical run. Their tolerance to run-to-run noise has not been established.
`thresholds.json` therefore marks calibration **provisional**; see the
[evaluator audit](../docs/EVALUATOR_AUDIT.md). Numeric policy bounds are unchanged by the audit.

| Metric | Bound | Kind | Baseline | Why this value |
|---|---|---|---|---|
| `items_evaluated` | = all items | hard | 15 | Added by the gate. An item whose task died would otherwise drop out of the averages and flatter them. |
| `items_in_langfuse` | = all items | hard | 15 | Added by the gate. Every item must be read back from Langfuse with an `answer_correct` score: the evidence the summary links to has to exist. |
| `accuracy` | ≥ 0.70 | hard | 0.80 | One question is 6.7 points. 0.70 passes 11/15 and blocks 10/15: it absorbs one question of noise (production v1 has scored 12/15 at 2 rounds and 14/15 at 4) and blocks a two-question regression. |
| `must_pass:q-padding` | correct | hard | correct | The hero question. Its answer (LAX, 9 minutes) beats the runner-up (ATL, 10) by one minute, so it's the question most sensitive to how finely the agent sweeps. It was right in both 300-per-round runs and wrong at 10 per round. |
| `mean_run_cost_usd` | ≤ $0.22 | hard | $0.1445 | About 1.5× the baseline (the other wide run measured $0.149). "Breadth is affordable" is half the demo's claim, so a prompt that makes every question 50% more expensive (rejected plans, extra lookups, longer outputs) is a decision to make on purpose. The agent's $3/question budget is the runtime stop, not this. |
| `mean_wall_s` | ≤ 120 s | soft | 66.3 s | About 1.8× the baseline. Wall time is mostly model latency, which moves day to day, so it warns instead of blocking. |
| `mean_db_time_share` | ≤ 15% | soft | 6.7% | The demo's first claim: ClickHouse is a few percent of the run (9.5% in the other wide run). A prompt that stops filtering on the sort key pushes it up (a missing `Year` filter read ~4.4M rows per scenario). Soft, because an idle or cold Cloud service moves it too. |
| `mean_scenarios_evaluated` | ≥ 250 | soft | 418.5 | The demo's second claim: hundreds of scenarios per question. Experiment A showed breadth buys accuracy (19 per question scored 40%). Breadth isn't the outcome, so it warns; accuracy and the hero carry the block. |
| `mean_llm_calls` | ≤ 5 | soft | 3.6 | At 2 rounds the agent makes 2 to 4 calls. More than 5 on average means plans are being rejected and retried. |
| `errors` | ≤ 0 | soft | 0 | An errored question already counts as wrong. The warning names the cause (timeout, rate limit, invalid plan), so infrastructure trouble isn't mistaken for prompt quality. |

A **missing** metric fails a hard rule. The historical reference used all 15 questions: with
`--limit` the verdict is a smoke test and the summary says so (per-question costs range from $0.056
to $0.285, so a subset's mean isn't comparable).

One missing or unknown item cost makes the run's cost metric missing; it cannot be
averaged away. An unpriced requested model is rejected before any model call. If the
provider serves an unpriced fallback, the task fails, records its token usage, and reports
unknown cost rather than zero. Add verified model prices to `explorer/config.py` before
evaluating a new model. A task that errors after producing an answer receives zero full
and partial credit.

## How it's triggered

```
Langfuse: save a planner or analyst version, or move a label
  └─► Prompts → Automations → GitHub Repository Dispatch (event analytics-agentic-speed-prompt-update)
        └─► .github/workflows/analytics-agentic-speed-prompt-gate.yml   (same template as claims-prompt-gate.yml)
              1. Decide: prompt name, version, labels from the payload; skip other prompts or
                 versions without a deployable label (production, staging, candidate, candidate-coarse)
              2. Gate the label: both prompts at that label, 15 questions, unique run name per run
                 attempt → job summary with the verdict, per-question answers and the run link
              3. "Ship it" only if the gate passed AND the version carries `production`
```

Labelling the pair sends two events (one per prompt). The workflow's concurrency group is the
label set, so the second cancels the first and the pair is gated once. `workflow_dispatch`
(Actions → Run workflow) gates any label from the dropdown without touching Langfuse.

Release flow: new planner and analyst versions → label `staging` (or `candidate`) on both → **the
gate (this is the check that blocks)** → move `production` to that pair in the Langfuse UI. A
`production` label event is already live when the gate runs (the agent's SDK caches prompts for
60 s), so on that path the gate is a post-deploy confirmation: a failure there means move the label
back. Every label move fires the automation and costs a full run.

Exit codes: 0 passed (or `--warn-only`); 1 blocked, or nothing evaluated. Before any model call the
gate checks that the Langfuse keys map to the `analytics-agentic-speed` project, that both prompts
carry the label, and that the read-only ClickHouse user can read the table. A pre-flight failure
writes a "not evaluated" summary and exits 1, even with `--warn-only`.

Cost and time of a full gate: about 15 × $0.145 ≈ $2.20 of model spend and 6 to 8 minutes at
concurrency 3, plus up to 5 minutes waiting for Langfuse to show the run.

## Setup (once)

Repo secrets (Settings → Secrets and variables → Actions):

| Secret | What |
|---|---|
| `AGENTIC_SPEED_CH_HOST` | The ClickHouse Cloud service host (no scheme, no port) |
| `AGENTIC_SPEED_CH_USER` | The **read-only** agent user created by `scripts/load_data.py` (`scenario_agent`), never the admin |
| `AGENTIC_SPEED_CH_PASSWORD` | That user's password |
| `AGENTIC_SPEED_LANGFUSE_BASE_URL` | e.g. `https://us.cloud.langfuse.com` |
| `AGENTIC_SPEED_LANGFUSE_PUBLIC_KEY` | Keys of the `analytics-agentic-speed` project (the gate refuses any other) |
| `AGENTIC_SPEED_LANGFUSE_SECRET_KEY` | |
| `ANTHROPIC_API_KEY` | Shared with the other demos' gates |

Optional repo variables: `AGENTIC_SPEED_CH_DATABASE` (default `flights`), `AGENTIC_SPEED_CH_TABLE`
(`ontime`), `AGENTIC_SPEED_CH_PORT` (`8443`). The workflow writes no `.env`: the step's environment
is the configuration, and `EXPLORER_TARGET` is pinned to `cloud`.

**ClickHouse access.** The Cloud service's IP access list must admit the runner, and GitHub-hosted
runners have no fixed IP. Pick one: a self-hosted runner (or a GitHub larger runner with static
IPs) whose address is on the list; or open the service to anywhere and rely on the read-only user,
its SQL allow-list and per-query limits as the fence. If the runner is refused, the pre-flight
fails in seconds with "ClickHouse is not reachable", before any model spend.

Then in the Langfuse project: Prompts → Automations → Create → events *Created* and *Updated*,
filter the two `analytics-agentic-speed-*` prompts (or no filter: the workflow skips every other
prompt), action GitHub Repository Dispatch (URL
`https://api.github.com/repos/<owner>/<repo>/dispatches`, event type
`analytics-agentic-speed-prompt-update`, a token with Actions + Contents read/write). The automation
has no public API: it's a UI step. The workflow runs only once it is on the default branch.

## Run it locally

```bash
cd demos/analytics-agentic-speed
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL   # .env wins anyway
.venv/bin/python scripts/prompt_gate.py --prompt-label production --limit 3     # smoke: ~$0.30, ~2 min
.venv/bin/python scripts/prompt_gate.py --prompt-label production               # the full gate: ~$2.20
.venv/bin/python scripts/prompt_gate.py --prompt-label staging --warn-only      # report, exit 0
```

`--limit N` runs the first N questions in `data/questions.yaml` order, so the hero is always in.
`--model` checks a model change against the same bar before you switch. `--run-name` takes a
template (`{label}`, `{version}`); a UTC stamp is always appended, so a re-run never appends to an
earlier dataset run.

## Demo it blocking a bad prompt

`candidate-coarse` is a well-meant policy edit: "to save time, and because schedulers plan in
5-minute increments", the planner sweeps minutes and thresholds in steps of 5, never finer, and the
analyst reports the smallest multiple of 5 that works. The hero question goes wrong: its answer is
LAX at 9 minutes, one minute ahead of ATL at 10, and a 5-minute grid lands on 10.

It has to be a rule. A first version only said a 10-minute step "is precise enough", and Claude
Opus 5.5 overrode it: its plan noted the step couldn't give the smallest whole minute the question
asks for, swept 1-minute steps anyway, and the gate passed it. That's worth saying out loud in a
demo, too: the model resisted a soft bad instruction, and the gate is what catches the firm one.

```bash
.venv/bin/python scripts/seed_candidate_prompt.py --dry-run   # what it would create
.venv/bin/python scripts/seed_candidate_prompt.py             # both prompts, label candidate-coarse only
.venv/bin/python scripts/seed_candidate_prompt.py --show      # production hasn't moved
.venv/bin/python scripts/prompt_gate.py --prompt-label candidate-coarse --limit 3   # ~2 min → GATE FAILED, exit 1
```

Or in GitHub: Actions → Analytics at Agentic Speed prompt gate → Run workflow → `candidate-coarse`.
The job summary is the beat: the hero row reads `WRONG`, the per-question table shows
`buffer_minutes=10` against `buffer_minutes=9`, and the cost row is green. It was cheaper, and it
was wrong. Then run `production` to show the same bar passing.

The seeder never assigns `production` (it refuses `production`, `latest` and
`previous-production`), checks that each prompt's production version is the same before and after,
and stops if the production text changed under it rather than seeding a no-op variant.

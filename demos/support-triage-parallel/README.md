# Support Triage Parallel — Pattern #3: Parallelization (sectioning + voting)

A ClickHouse support ticket arrives. The app **fans out four independent
analysis branches concurrently** (summary, sentiment/urgency, technical
category, and a policy/PII guardrail — *sectioning*) and synthesizes a triage
brief; then, for the data question embedded in the ticket, it **samples N=5 SQL
candidates at high temperature, validates each with `EXPLAIN` against the live
ClickHouse public playground, executes the valid ones, and majority-votes on the
result-set signature** (*voting*) — with an LLM tie-break judge only when the
vote splits. The ten tickets in `tickets.py` are synthetic; the data questions
run against the public `sql.clickhouse.com` datasets.

Every branch is a sibling observation under **one Langfuse trace**; the
aggregators carry the merge inputs and the full vote tally in metadata; and
consensus confidence is a first-class Langfuse score.

> Both Anthropic sub-variants of parallelization
> ([Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents))
> in one trace: **sectioning** (`analyze-sections`) and **voting** (`vote-sql`).

**The question it answers:** when one model call isn't trustworthy enough, how do you buy
confidence with parallel calls and still see what it cost and where the samples disagreed?

<!-- demo-card:start -->
| | |
|---|---|
| **Covers** | Trace · Monitor · Experiment · Evaluate |
| **Stack** | Python · Anthropic SDK · asyncio · ClickHouse · Docker |
| **Runs as** | Container in the root stack (`demo` profile) |
| **Langfuse** | Self-hosted · project `LLM Observability Demo` |
| **Length** | 15–20 min · short path 8 min |
| **Present it** | [DEMO_SCRIPT.md](DEMO_SCRIPT.md) |
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md), the talk track act by act (15–20 minutes, 8-minute short path)

## What it demonstrates

| Capability | How it shows up |
|---|---|
| **Sectioning fan-out** | 4 concurrent Haiku branches (`asyncio.gather`) → sibling observations that **overlap in the Timeline** (wall-clock ≈ slowest branch, not the sum). Run `--sequential` for the before/after. |
| **Guardrail split** | The policy/PII screen is its own typed `guardrail` branch (not folded into analysis), with a `policy_flagged` span score. |
| **Voting / self-consistency** | The same data question answered N=5× at `temperature=0.9`; identical-named `vote-candidate` generations with `sample_index` in metadata. |
| **ClickHouse is the vote counter** | Candidates validated with `EXPLAIN` and executed against `sql.clickhouse.com`; the winner is decided by hashing **result sets**, not by string comparison. |
| **Vote tally as metadata** | The `tally-votes` aggregator's metadata is a literal tally (`{votes, invalid, winner, margin, tie_break_used}`); its input holds every candidate. |
| **Tie-break judge** | An Opus judge fires **only** when the vote splits (`tie_break_used: true`). |
| **Consensus as a score** | `consensus_confidence = winner_votes / valid_candidates` — filterable, chartable, monitorable. |
| **Partial-failure tolerance** | `FAULT=slow-branch` drops one branch (`level=WARNING`), the aggregator proceeds with N-1 and records `failed_branches`. |
| **Cost fan-out, made visible** | Parallelization multiplies spend ~N×; a cost-per-trace Monitor bounds it. |
| **Three aggregation strategies** | `result-signature` (default) / `majority-exact` / `judge-consensus`, compared head-to-head in an Experiment. |
| **Deploy node** | 7 managed prompts fetched by `label=production` with local fallbacks; the SQL voter ships v1 + v2. |

## Who it's for

- **SAs** showing that *disagreement between model samples becomes a queryable
  number in ClickHouse*, and that the N× cost of confidence is visible and
  boundable — not a surprise on the invoice.
- **Engineers** who want a clean, framework-light reference for concurrent LLM
  fan-out (raw Anthropic async API + `asyncio.gather`) instrumented as sibling
  Langfuse observations, plus a programmatic majority-vote arbitrated by a database.

Natural pivot from **text-to-sql** (single-shot NL→SQL): *"what if one SQL sample
isn't trustworthy — buy confidence with N samples and let ClickHouse arbitrate."*

## What runs where

```
 you ── docker compose --profile demo run --rm support-triage-parallel python main.py
          │  container `support-triage-parallel` (python:3.11-slim, demo folder mounted at /app)
          │  a CLI batch: it serves nothing, although compose maps ${SUPPORT_TRIAGE_PORT:-8009}
          ├──▶ Anthropic API         Haiku branches · Sonnet synthesis + 5 voters · Opus tie-break
          ├──▶ sql-clickhouse.clickhouse.com:443   EXPLAIN + read-only execution as user `demo`
          └──▶ Langfuse web          langfuse-web:3000 inside compose, http://localhost:3001 on the host
                 project `LLM Observability Demo` (root .env keys)
```

What lands in Langfuse, per ticket:

- **Trace** `triage-support-ticket`; the ticket goes in the root span's input, never in the name.
- **Session** `triage-<8 hex>`: one per `main.py` invocation, shared by every ticket in that batch or interactive run.
- **Tags** `support-triage-parallel`, `demo`, plus `fault:<name>` when `FAULT` is set.

```
TRACE triage-support-ticket            session_id: triage-<uuid8>   tags: [support-triage-parallel, demo]
├─ SPAN analyze-sections               metadata: {branch_count: 4, mode: "parallel", failed_branches: 0}
│  ├─ GENERATION branch-summary            (Haiku)  metadata: {branch: "summary"}
│  ├─ GENERATION branch-sentiment-urgency  (Haiku)  metadata: {branch: "sentiment"}
│  ├─ GENERATION branch-category           (Haiku)  metadata: {branch: "category"}
│  ├─ GUARDRAIL  branch-policy-guard       (Haiku)  → span score: policy_flagged
│  └─ SPAN synthesize-triage-brief
│     └─ GENERATION synthesis-llm          (Sonnet; prompt: support-triage-synthesis@production)
├─ SPAN vote-sql                       metadata: {n_samples: 5, vote_temperature: 0.9, strategy: "result-signature"}
│  ├─ GENERATION vote-candidate ×5         (Sonnet, T=0.9)  metadata: {sample_index: 0..4}
│  ├─ SPAN validate-candidates             → span score: sql_validity_rate
│  │  └─ TOOL explain-candidate ×5         (EXPLAIN vs sql.clickhouse.com)
│  └─ SPAN tally-votes                     metadata: {votes, invalid, winner, margin, tie_break_used}
│     └─ GENERATION tie-break-judge        (Opus) — present ONLY when the vote ties
└─ trace score: consensus_confidence
   span scores:  policy_flagged, sql_validity_rate
```

After ingestion, two evaluators score `tally-votes`: the code evaluator
`consensus-margin-guard` and the managed judge `correlated-vote-risk` (see Evaluation).

## Set up from scratch

Prerequisites: Docker, an Anthropic API key, outbound HTTPS to
`sql-clickhouse.clickhouse.com`, and Python 3.10+ on the host for the seed and experiment
scripts (the readiness check needs only the standard library). From the repo root:

```bash
ANTHROPIC_API_KEY=sk-ant-... ./setup.sh     # root stack + .env; also seeds this demo (below)
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
pip install -r demos/support-triage-parallel/requirements.txt   # host deps for the seed scripts
python demos/support-triage-parallel/scripts/seed_all.py        # 7 prompts + 2 datasets (idempotent)
./scripts/seed-support-triage-evaluators.sh                     # managed judge: correlated-vote-risk
./scripts/seed-code-evaluators.sh                               # code evaluator: consensus-margin-guard
docker compose --profile demo build support-triage-parallel
docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-001   # one fresh trace
python demos/support-triage-parallel/scripts/verify_demo.py     # should print READY
```

`./setup.sh` already runs all three seeders: `seed_all.py` inside the container, then
`seed-support-triage-evaluators.sh`, and `seed-code-evaluators.sh` with the other code
evaluators. Run them by hand after a change. `seed_all.py` reconciles: a prompt gets a new
version only when its type, text or config drifted, an item is written only when it is
missing, archived or changed, and an unchanged project gets no writes. It prints created /
updated / unchanged and exits 1 if anything failed. It pins the `sql-voting` dataset's
expected result signatures by executing each reference query against the playground, so it
needs that network access too; an outage keeps the signatures pinned earlier.

The root `.env` is the only source of `LANGFUSE_*`. Compose resolves `${...}` from the
shell first, so unset shell-exported keys before running. `verify_demo.py` and a host run
of `seed_all.py` clear them and read only the root `.env`, and stop before any Langfuse
call when it or a key is missing. Inside the container (`/app` plus Docker's `/.dockerenv`)
`seed_all.py` uses the compose environment instead. The demo itself runs with **no
Langfuse keys set** too: instrumentation degrades to a no-op.

## Before a demo

| When | Do | Why |
|---|---|---|
| The day before | `python demos/support-triage-parallel/scripts/seed_all.py`, then `docker compose --profile demo build support-triage-parallel` | Prompts and datasets match the code, and the image is built |
| The day before | `docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-002` until the step log shows `tie_break=True`; pin that trace | Act 3 needs a split vote; never hunt for one live |
| The day before (full script) | `set -a && source .env && set +a`, then `python demos/support-triage-parallel/scripts/run_experiment.py --strategy all` | Act 6 opens these runs; the full-script check requires all three. Calls models: 3 strategies × 10 items × 5 samples |
| The day before | Create the two Monitors and the dashboard by hand (settings in the script's Act 5) | Nothing seeds them |
| 30 minutes before | `python demos/support-triage-parallel/scripts/verify_demo.py --links` | READY for the full script, and the link each act opens. Add `--short` for the short path (Acts 1–3), where Act 6 and the evaluator scores only warn |
| 5 minutes before | Open the links it printed, plus a terminal | Langfuse login: `demo@example.com` / `demodemo1!` |

## Run it

```bash
docker compose --profile demo run --rm support-triage-parallel python main.py                  # the 10-ticket batch
docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-002 # one ticket
docker compose --profile demo run --rm support-triage-parallel python main.py --interactive    # type your own ticket
docker compose --profile demo run --rm support-triage-parallel python main.py --sequential --ticket TCK-001  # latency baseline
FAULT=slow-branch docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-005  # dropped branch
```

Each ticket makes at least 10 model calls (4 Haiku branches, 1 Sonnet synthesis, 5 Sonnet
samples) plus an Opus call on a tie, so the batch makes 100 or more. Cost and duration are
not measured here; the step log prints wall-clock per ticket.

**What you see in Langfuse:** one `triage-support-ticket` trace per ticket with the tree
above. The four `branch-*` observations overlap in the Timeline (a staircase with
`--sequential`); `tally-votes` carries the vote tally in metadata and every candidate in
its input; the trace has a `consensus_confidence` score, `validate-candidates` a
`sql_validity_rate` score and `branch-policy-guard` a `policy_flagged` score. Under
`FAULT=slow-branch`, `branch-sentiment-urgency` is `level=WARNING`, the brief says
"insufficient data" for sentiment and the trace is tagged `fault:slow-branch`.

## Evaluation

| What | Command | Notes |
|---|---|---|
| In-app scores | written by every run | `consensus_confidence` (trace), `sql_validity_rate` and `policy_flagged` (spans) |
| Code evaluator `consensus-margin-guard` | `./scripts/seed-code-evaluators.sh` | `evaluators/consensus-margin-guard.ts` on `tally-votes`: margin ≥ 2 is `consensus_margin_ok` |
| Managed judge `correlated-vote-risk` | `./scripts/seed-support-triage-evaluators.sh` | Scores NEW traces only: run again after seeding. Self-hosted only; prints UI steps in cloud mode |
| Datasets | `python demos/support-triage-parallel/scripts/seed_all.py` | `support-triage/category-branch` (12 items), `support-triage/sql-voting` (10 items, pinned result signatures) |
| Aggregator experiment | `python demos/support-triage-parallel/scripts/run_experiment.py --strategy all` | Runs `aggregator-<strategy>` on `sql-voting`: per-item `item_accuracy`, run-level `voting_accuracy_rate` and `mean_consensus_confidence` |
| CI gate | `python demos/support-triage-parallel/scripts/run_experiment.py --strategy result-signature --ci` | Exits 1 when `voting_accuracy_rate` < 0.8. No workflow runs it yet |
| N sweep | `python demos/support-triage-parallel/scripts/run_experiment.py --vary-n-samples 1,3,5` | Diminishing returns of more samples |
| Aggregator logic only | `python demos/support-triage-parallel/scripts/run_experiment.py --local-aggregator` | Pure code: no model, network or Langfuse |

`run_experiment.py` reads `LANGFUSE_*` from the environment, not from a file: export the
root `.env` first with `set -a && source .env && set +a`.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | from the root `.env` | Keys for the `LLM Observability Demo` project; without them tracing is a no-op |
| `LANGFUSE_BASE_URL` | `http://localhost:3001` | Where the host-side scripts read and seed; the container uses `LANGFUSE_INTERNAL_URL` (default `http://langfuse-web:3000`) |
| `ANTHROPIC_API_KEY` | blank | Every branch, sample and judge |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Synthesis and vote-candidate model |
| `BRANCH_MODEL` | `claude-haiku-4-5` | Cheap sectioning branches |
| `JUDGE_MODEL` | `claude-opus-4-7` | Tie-break judge |
| `SYNTH_TEMPERATURE` | `0.5` | Synthesis temperature |
| `VOTE_SAMPLES` | `5` | N SQL candidates per question |
| `VOTE_TEMPERATURE` | `0.9` | Sampling temperature for diversity |
| `VOTE_STRATEGY` | `result-signature` | `result-signature` \| `majority-exact` \| `judge-consensus` |
| `BRANCH_TIMEOUT_S` | `30` | Per-branch timeout (lower it, e.g. `8`, for a snappier fault demo) |
| `FAULT` | unset | `slow-branch` drops the sentiment branch to demo degradation |
| `PUBLIC_CH_HOST`, `PUBLIC_CH_PORT` | `sql-clickhouse.clickhouse.com`, `443` | Read-only playground for EXPLAIN and execution |
| `PUBLIC_CH_USER`, `PUBLIC_CH_PASSWORD` | `demo`, blank | Playground credentials |
| `CH_QUERY_TIMEOUT`, `CH_MAX_ROWS` | `10`, `100` | Per-query timeout in seconds, and the LIMIT appended when a candidate has none |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` | Project name `verify_demo.py` expects the keys to map to |

## Layout

```
demos/support-triage-parallel/
├── demo.yaml                manifest: profile, coverage, commands (feeds the index)
├── README.md                this file
├── DEMO_SCRIPT.md           the talk track, act by act
├── Dockerfile               python:3.11-slim image for the root compose `demo` profile
├── requirements.txt         anthropic, langfuse>=4.7,<5, httpx, sqlglot, clickhouse-connect
├── pytest.ini
├── main.py                  batch + --interactive + --sequential + --ticket; one trace per ticket
├── langfuse_config.py       wiring: is_langfuse_enabled(), observe() typed ctx mgr, scores, prompts, flush()
├── llm.py                   raw Anthropic async/sync call layer (updates the generation with usage/cost)
├── triage_pipeline.py       Stage 1: sectioning fan-out + synthesis aggregator + fault injection
├── sql_voting.py            Stage 2: voting fan-out + tally + tie-break; 3 pluggable strategies
├── ch_validator.py          EXPLAIN validation + read-only LIMIT-enforced execution vs the playground
├── tickets.py               deterministic synthetic ticket corpus (10 tickets)
├── scripts/
│   ├── seed_all.py          the one seed entry point: prompts + datasets (idempotent)
│   ├── seed_prompts.py      7 managed prompts; a new version only when the text drifts
│   ├── seed_datasets.py     2 hosted datasets + the local aggregator cases
│   ├── run_experiment.py    aggregator-strategy experiment, --ci gate, N sweep
│   └── verify_demo.py       readiness check: READY or NOT READY, --links
└── tests/                   no services, no model calls
```

## Tests

```bash
cd demos/support-triage-parallel
pip install -r requirements.txt pytest
python -m pytest -q          # no services, no model calls, a few seconds
```

They cover the vote tally and the synthesis aggregator (`compute_tally`,
`build_synthesis_input`). `verify_demo.py` and `seed_all.py` run against a fake Langfuse
at the urllib level (`tests/fake_langfuse.py`), which rejects unknown query parameters,
honours field projection (a Scores v3 `comment` only with `fields=details`) and paginates
with empty cursor pages: every READY / NOT READY state, the v2 → v1 fallback, archived
items, missing tie and experiment evidence, a missing, partial, unreadable or malformed
`.env` (including a bad `LANGFUSE_BASE_URL`) with hostile shell keys, an unexpected
response shape, unchanged reseeding, config-only drift, and partial and total write
failures.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| No traces, or no scores | The keys did not reach the container, or a shell `LANGFUSE_*` export overrides `.env`. `unset` them and run again; `verify_demo.py` names the project the keys map to |
| `LANGFUSE_BASE_URL is an http(s) URL` fails, or `cannot read …/.env` | The root `.env` has a malformed URL (for example a bad IPv6 literal or port) or cannot be read as UTF-8 text. Fix it; `verify_demo.py` makes no request until it parses |
| `keys map to project '…' (want 'LLM Observability Demo')` | The root `.env` holds another project's keys. Re-run `./setup.sh` or fix the keys |
| `Observations v2 answered 404 … read through v1` | Expected on the self-hosted stack: compose pins a v3 Langfuse server, which has no Observations v2 |
| All candidates invalid (`sql_validity_rate = 0`) | No outbound access to `sql-clickhouse.clickhouse.com`; the validator fails candidates closed on any connection error |
| No `correlated-vote-risk` score (`evaluator score … on 0 demo run(s)`) | The judge scores NEW traces only: run again after `./scripts/seed-support-triage-evaluators.sh`, and make sure `./setup.sh` provisioned the Anthropic LLM connection (the default eval model) |
| `Act 3 tie trace` fails | No demo run with a split `result-signature` vote yet; the split is random at temperature 0.9. Re-run `--ticket TCK-002` until the step log shows `tie_break=True` |
| `aggregator runs … missing` fails | Act 6 needs `aggregator-result-signature`, `-majority-exact` and `-judge-consensus`: run `run_experiment.py --strategy all`, or check with `--short` |
| `expected items ACTIVE and usable` fails | An item is missing, archived, edited or has no pinned signature: `seed_all.py` re-activates and re-pins it |
| Fault demo feels slow | The branch timeout defaults to 30 s; set `BRANCH_TIMEOUT_S=8` |

## Known limits

- The vote split is random at `VOTE_TEMPERATURE=0.9`. TCK-002 is written to be ambiguous,
  but a given run can still come back 5–0, so the tie trace is pre-run, not live.
- The voter and the tie-break depend on the public playground. Without outbound access
  every candidate fails closed and `consensus_confidence` is 0.
- Cost and latency figures are not measured in this repo. The script's cost Monitor
  threshold is a placeholder you set from your own budget.
- Nothing seeds the two Monitors or the "Parallelization economics" dashboard that Act 5
  shows; you create them in the Langfuse UI, and `verify_demo.py` cannot check them.
- The `--ci` gate exists, but no GitHub workflow runs it.
- The managed judge is seeded by a Postgres upsert, so it works only against the
  self-hosted stack; on Langfuse Cloud the script prints the UI steps instead.
- The expected order in Act 6 (`result-signature` ≥ `judge-consensus` > `majority-exact`)
  is the design intent, not a recorded measurement.

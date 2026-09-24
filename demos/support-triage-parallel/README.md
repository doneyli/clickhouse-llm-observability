# Support Triage Parallel — Pattern #3: Parallelization (sectioning + voting)

A ClickHouse support ticket arrives. The app **fans out four independent
analysis branches concurrently** (summary, sentiment/urgency, technical
category, and a policy/PII guardrail — *sectioning*) and synthesizes a triage
brief; then, for the data question embedded in the ticket, it **samples N=5 SQL
candidates at high temperature, validates each with `EXPLAIN` against the live
ClickHouse public playground, executes the valid ones, and majority-votes on the
result-set signature** (*voting*) — with an LLM tie-break judge only when the
vote splits.

Every branch is a sibling observation under **one Langfuse trace**; the
aggregators carry the merge inputs and the full vote tally in metadata; and
consensus confidence is a first-class Langfuse score.

> Both Anthropic sub-variants of parallelization
> ([Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents))
> in one trace: **sectioning** (`analyze-sections`) and **voting** (`vote-sql`).

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

## Quick start

```bash
# From the repo root, with Langfuse up (docker compose --profile langfuse up -d)
# and ANTHROPIC_API_KEY set in .env.

# Seed the 7 managed prompts + 2 datasets, then the independent judge:
python demos/support-triage-parallel/scripts/seed_all.py
./scripts/seed-support-triage-evaluators.sh
./scripts/seed-code-evaluators.sh          # (re)provisions consensus-margin-guard too

# Run the batch of demo tickets:
docker compose --profile demo run --rm support-triage-parallel python main.py

# Single ticket / interactive / sequential baseline:
docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-002
docker compose --profile demo run --rm support-triage-parallel python main.py --interactive
docker compose --profile demo run --rm support-triage-parallel python main.py --sequential --ticket TCK-001

# Fault injection (dropped branch):
FAULT=slow-branch docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-005

# Aggregation-strategy experiment (branches fixed, aggregator varied):
python demos/support-triage-parallel/scripts/run_experiment.py --strategy all
```

The demo runs green **with no Langfuse keys set** — instrumentation degrades to a
no-op (repo convention). It needs `ANTHROPIC_API_KEY` and outbound access to
`sql-clickhouse.clickhouse.com` (read-only `demo` user) to actually run.

For the presenter walkthrough (acts, talk track, fallbacks) see
[`DEMO_SCRIPT.md`](DEMO_SCRIPT.md).

## Trace shape

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

## Configuration (env)

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | synthesis + vote-candidate model |
| `BRANCH_MODEL` | `claude-haiku-4-5` | cheap sectioning branches |
| `JUDGE_MODEL` | `claude-opus-4-7` | tie-break judge |
| `VOTE_SAMPLES` | `5` | N SQL candidates per question |
| `VOTE_TEMPERATURE` | `0.9` | sampling temperature for diversity |
| `VOTE_STRATEGY` | `result-signature` | `result-signature` \| `majority-exact` \| `judge-consensus` |
| `BRANCH_TIMEOUT_S` | `30` | per-branch timeout (lower it, e.g. `8`, for a snappier fault demo) |
| `PUBLIC_CH_HOST` | `sql-clickhouse.clickhouse.com` | read-only playground for EXPLAIN + execution |
| `FAULT` | (unset) | `slow-branch` drops the sentiment branch to demo degradation |

## Files

```
demos/support-triage-parallel/
├── README.md                # this file
├── DEMO_SCRIPT.md           # presenter runbook (Frame/Show/Land/Ask acts)
├── Dockerfile               # python:3.11-slim
├── requirements.txt         # anthropic, langfuse>=3, httpx, sqlglot, clickhouse-connect
├── main.py                  # batch + --interactive + --sequential + --ticket; one trace per ticket
├── langfuse_config.py       # v3 wiring: is_langfuse_enabled(), observe() typed ctx mgr, scores, prompts, flush()
├── llm.py                   # raw Anthropic async/sync call layer (updates the generation with usage/cost)
├── triage_pipeline.py       # Stage 1: sectioning fan-out + synthesis aggregator + fault injection
├── sql_voting.py            # Stage 2: voting fan-out + tally + tie-break; 3 pluggable strategies
├── ch_validator.py          # EXPLAIN validation + read-only LIMIT-enforced execution vs the playground
├── tickets.py               # deterministic synthetic ticket corpus
├── scripts/                 # seed_prompts.py, seed_datasets.py, run_experiment.py, seed_all.py
└── tests/                   # test_vote_tally.py, test_aggregator.py (pure code, no LLM/network)
```

Root-level companions: `scripts/seed-support-triage-evaluators.sh` (managed
`correlated-vote-risk` judge) and `evaluators/consensus-margin-guard.ts` (the 6th
deterministic code evaluator).

## Tests

```bash
cd demos/support-triage-parallel && python -m pytest    # pure-code tally + aggregator; no services
```

## Troubleshooting

- **No scores / no traces** — confirm `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` reach the
  container (they degrade to a no-op if absent), and that a leaked shell
  `LANGFUSE_*` isn't overriding `.env` (`unset` them before the demo).
- **All candidates invalid (`sql_validity_rate = 0`)** — check outbound access to
  `sql-clickhouse.clickhouse.com`; the validator fails candidates closed on any
  connection error.
- **No `correlated-vote-risk` score** — the judge is `NEW`-scoped; regenerate
  traffic after running `scripts/seed-support-triage-evaluators.sh`, and ensure
  `./setup.sh` provisioned the Anthropic LLM connection (default eval model).
- **Fault demo feels slow** — the default branch timeout is 30s; set
  `BRANCH_TIMEOUT_S=8` for a snappier dropped-branch moment.

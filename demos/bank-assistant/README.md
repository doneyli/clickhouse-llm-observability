# Northwind Bank Assistant

Built and instrumented with Langfuse, this banking assistant uses retrieval,
MCP tools, and guardrails. Inspect conversations, evaluate answers, compare
prompts, and test release candidates. The portal supports chat and voice in
English and Spanish; an n8n workflow demonstrates complaint triage.

Northwind Bank is fictional. All customer data and policies are synthetic.
This demo has its own environment and services; use the setup below.

## Start Here

| Task | Guide |
|---|---|
| Run the assistant | [Quick start](#quick-start) |
| Conduct compliance and single-vs-split judges | [Policy evaluation and comparison](docs/COMPLIANCE_EVALUATION.md) |
| Present the 2-hour workshop | [Demo script](DEMO_SCRIPT.md), [attendee labs](LABS.md), [presenter kit](PRESENTER_KIT.html) (open locally in a browser) |
| Present a 30-minute evals-first demo | [Evals-first script](DEMO_SCRIPT_EVALS_FIRST.md), [walkthrough video storyboard](docs/EVALS_FIRST_VIDEO.md) |
| Check coverage and evidence | [Capability matrix](CAPABILITY_MATRIX.md), [requirements tracker](REQUIREMENTS_TRACKER.md) |

## Prerequisites

- Git and [uv](https://docs.astral.sh/uv/getting-started/installation/) for Python 3.12.
- Docker with Compose, plus `curl`, `openssl`, and `lsof`.
- An Anthropic API key for the assistant and judges.
- A Langfuse Cloud project with project API keys, or the local stack below.
- Optional: an OpenAI API key for voice, model comparison, and judge-model comparison.
- Optional: a Langfuse Enterprise license for local governance demonstrations.

Model calls, traffic generation, and evaluations consume API credits.

## Quick Start

Run all commands from `demos/bank-assistant`. From the repository root:

```bash
cd demos/bank-assistant
```

### 1. Install and Configure

On first setup:

```bash
uv venv --python 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env
```

Set `ANTHROPIC_API_KEY` in `.env`; add `OPENAI_API_KEY` for optional features. Model keys can also come from the
repository-root `.env`. Keep existing environment files when repeating setup.

### 2. Choose a Langfuse Target

Choose one target before running the seed scripts.

**Cloud:** add `NORTHWIND_PROFILE=cloud` to `.env`. Create `.env.cloud` with your
project's URL and keys:

```dotenv
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=<project-public-key>
LANGFUSE_SECRET_KEY=<project-secret-key>
```

Use the URL for your project's region. Start the local n8n and Jaeger services:

```bash
./scripts/up.sh
```

**Self-hosted:** this demo has no Langfuse containers of its own. It uses the ONE
local Langfuse shared by all the standalone demos:

```bash
./scripts/up.sh --selfhosted
python3 ../../scripts/langfuse_local.py status
```

`--selfhosted` runs `scripts/langfuse_local.py provision bank-assistant`: it reuses a
healthy local Langfuse v4 instance, or starts the shared one on
[localhost:3300](http://localhost:3300) (`infra/langfuse-local`), and writes
`.env.selfhosted` with this demo's `Northwind Bank` org, `retail-assistant` project and
keys. `status` shows the instance and checks the keys. Add `NORTHWIND_PROFILE=selfhosted`
to `.env` to target only that instance. The instance's own admin can see the Northwind org;
`./scripts/bootstrap_selfhosted.sh` also creates `admin@northwind.example` (password
`LANGFUSE_INIT_USER_PASSWORD` in `.env`) and the RBAC users. A newly started shared stack
reads `LANGFUSE_EE_LICENSE_KEY` from the repository-root `.env`; without one it runs as OSS.

**Both at once — `NORTHWIND_PROFILE=dual`:** Cloud stays the primary (prompts, datasets,
experiments are read from it) and every span, score and media item (voice audio) is also
sent to the local instance with the same trace, observation and score ids and the same PII
masking. n8n exports to one endpoint, so its traces reach Cloud only. If the local
instance is down or stops mid-run, the demo prints one warning and Cloud ingest carries
on untouched; the copy resumes when the instance is back. Seed the local project with the
same scripts under `NORTHWIND_PROFILE=selfhosted` to get its prompts and judges too. See
[docs/LANGFUSE_DUAL_INGEST.md](../../docs/LANGFUSE_DUAL_INGEST.md).

Python scripts read Langfuse credentials from these files; exported `LANGFUSE_*`
keys are ignored. An exported `NORTHWIND_PROFILE` overrides the file setting.
Without an explicit profile: `dual` when both `.env.cloud` and `.env.selfhosted`
exist, `cloud` when only `.env.cloud` does, otherwise self-hosted.

### 3. Seed Prompts and Start the Portal

```bash
.venv/bin/python scripts/seed_prompts.py
./scripts/run_portal.sh
.venv/bin/python portal/preflight.py
```

The launcher starts the portal and core-banking MCP server. Pre-flight must end
with `READY`; review warnings for features you plan to show. Dataset and OpenAI
warnings are expected before optional setup.

### 4. Verify a Conversation

1. Open [localhost:8090](http://localhost:8090).
2. In **Customer assistant**, ask: "What is the late payment fee on the Classic card?"
3. Use **Open in Langfuse** on the reply. Allow a few seconds for ingestion.
4. Confirm the trace contains the customer input, answer, retrieval, guardrails,
   and model usage. Send a follow-up to check that both turns share a session.

## Prepare Evaluations

After the quick start:

```bash
.venv/bin/python scripts/seed_evals.py
.venv/bin/python scripts/seed_datasets.py
.venv/bin/python scripts/seed_dashboard.py
.venv/bin/python scripts/seed_business_dashboard.py
.venv/bin/python scripts/seed_compliance_dashboard.py
.venv/bin/python scripts/generate_traffic.py --scenario all
```

Check **Evaluators**, **Datasets**, and the Northwind dashboards in Langfuse.
Conduct compliance uses five policy judges and a derived overall status/score,
with explicit coverage and unresolved checks. See the [conduct evaluation guide](docs/COMPLIANCE_EVALUATION.md).
Managed judge scores arrive asynchronously.

No judge rule runs on a turn the input guardrail blocked. Those answers are the
fixed refusal text, so the deterministic `guardrail-blocked` score is the verdict
and a judge call would grade a constant; such turns get no derived compliance
score either. `faithfulness` and the five policy judges run on every other
production turn; `manipulation-resistance-sampled` judges 20% of them, to catch
attacks the rules missed. Re-running `seed_evals.py` disables two retired rules,
keeping their past scores: `manipulation-resistance`, which judged every blocked
turn, and the original single `banking-compliance` judge, once all five policy
judges are active.

Once scores are visible, run:

```bash
.venv/bin/python scripts/fill_annotation_queue.py 20
```

The count sets the desired pending backlog. Re-running preserves completed
reviews and human scores. Candidates are the last 24 hours of production
answers: thumbs-down answers, the lowest judge scores and P1-P5 violations or
unknowns are queued first, however deep in the listing. If there are too few
unqueued answers, the command exits non-zero; generate fresh traffic and rerun.

Label and complete 8-10 answers in **SME review**, then run
`.venv/bin/python scripts/judge_calibration.py` to compare judge and human scores.
Leave at least one item pending for live annotation. Refill after rehearsals;
`scripts/verify_demo.py` fails readiness when there are no pending items.

`scripts/judge_calibration_experiment.py` then copies every SME-labelled answer
into `judge-calibration/faithfulness`, with the judge's exact inputs and a link
to the source trace, next to the 16 synthetic items. It reports accuracy per
source. The synthetic items have obvious planted errors, so a judge can score
100% on them and still miss what SMEs flag in production.

## Optional Workflows

| Workflow | Command or guide |
|---|---|
| Prompt comparison (EN + ES), model comparison, and red-team experiments | `./scripts/run_all_experiments.sh` (requires OpenAI for model comparison; inspect both experiment logs in `logs/` for failures) |
| Conduct judge comparison: original single, matched single, five policy judges | `.venv/bin/python scripts/compare_compliance.py` (or `--preview` without keys) |
| Judge-rubric calibration experiment | `.venv/bin/python scripts/judge_calibration_experiment.py` |
| Voice calls | `.venv/bin/python scripts/run_voice_calls.py --lang en --verify` (requires OpenAI; use `--lang es` for Spanish) |
| Complaint triage | [n8n setup and verification](n8n/README.md) |
| Prompt gate and GitHub Actions | [CI instructions](cicd/README.md) |
| Local Enterprise roles and audit activity | `./scripts/bootstrap_selfhosted.sh` (provisions the shared local instance, then seeds it) |
| Dispute-resolution regression | [Disputes walkthrough](docs/arcs/disputes.md) |
| Cost regression | [Cost walkthrough](docs/arcs/cost-regression.md) |

`run_all_experiments.sh --fresh` deletes existing experiment traces and scores
on the English, Spanish, and red-team datasets. The Enterprise bootstrap moves
prompt labels to generate audit activity. Voice samples are already committed.

For the full workshop, complete voice, n8n, calibration, and experiment setup,
then run `.venv/bin/python scripts/verify_demo.py`. It checks seeded resources,
recent channel traces, scores, and data hygiene; `READY` requires all mandatory
checks to pass. See [presenter preparation](DEMO_SCRIPT.md#presenter-preparation)
for label state and rehearsal.

## Services and Troubleshooting

| Service | Default URL | Notes |
|---|---|---|
| Portal | [localhost:8090](http://localhost:8090) | Chat, voice, presenter console |
| Core-banking MCP | `http://localhost:8765/mcp` | Started by the portal launcher |
| Langfuse | Cloud URL and/or the local instance in `.env.selfhosted` | Selected by the profile |
| Jaeger | [localhost:16686](http://localhost:16686) | Local infrastructure observability |
| n8n | [localhost:5678](http://localhost:5678) | Credentials in `.env` after n8n setup |

| Symptom | Check or fix |
|---|---|
| `.env.cloud` missing | Create it for Cloud, or select `selfhosted` in `.env`. |
| Langfuse authentication fails | Check profile, regional URL, and project key pair; rerun pre-flight. |
| Portal or MCP fails to start | Run `./scripts/run_portal.sh --status`; inspect `logs/portal.log` and `logs/mcp.log`. |
| Wrong target after changing profiles | Restart the portal and MCP; see [portal instructions](portal/README.md#restart-banking-state). |
| Voice or model comparison fails | Set `OPENAI_API_KEY` and confirm model access. |
| n8n webhook is missing | Rerun `./scripts/setup_n8n.sh`; see the [n8n guide](n8n/README.md). |

Stop the portal with `./scripts/run_portal.sh --stop` (MCP stays running).
Stop this demo's containers (n8n, Jaeger) with `docker compose stop`. The local Langfuse
is shared with the other demos, so it keeps running.

## Reference

| Topic | Guide |
|---|---|
| Portal controls and API | [Portal README](portal/README.md) |
| Conduct score aggregation, dashboard and comparison walkthrough | [Conduct evaluation](docs/COMPLIANCE_EVALUATION.md) |
| Data model and deployment architecture | [Architecture](docs/ARCHITECTURE.md) |
| Langfuse v4 reads, filters, and pagination | [API reference](docs/API_REFERENCE.md) |
| SSO, RBAC, audit, retention, and export | [Enterprise security](docs/ENTERPRISE_SECURITY.md) |
| Capacity, availability, backups, and upgrades | [Operations](docs/OPERATIONS.md) |
| Production gates, cost, and adoption | [Path to production](docs/PATH_TO_PRODUCTION.md) |
| Infrastructure observability | [APM integration example](docs/DYNATRACE.md) |
| Java instrumentation | [Spring AI](docs/SPRING_AI.md) |

## Limits

- Regex masking misses names, addresses, and identifiers without a nearby keyword.
  Card numbers keep their last four digits (`[REDACTED_CARD ****1111]`), so the
  judges can check "your card ending 1111". The n8n workflow has separate
  [masking limits](n8n/README.md#pii-masking).
- Runtime guardrails and judges do not cover every attack. Review the red-team results.
- Golden datasets are small (16 English-set items, 10 Spanish-set items); small score
  differences do not establish a reliable improvement.
- Banking state is held in MCP memory. Restart it before rehearsing actions that
  depend on unblocked cards or unopened disputes.

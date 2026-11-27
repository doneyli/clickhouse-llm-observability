# Bank Assistant Workshop — Nine-module hands-on workshop that adds tracing, prompts, monitoring and experiments to a synthetic bank assistant

**DRAFT.** Learners take an untraced Python/LangGraph assistant for Northwind Bank, a
fictional retail bank, and add the Langfuse engineering loop to it one module at a time:
tracing, managed prompts, monitoring signals, a dataset, experiments and an evaluated
prompt change. Customers, accounts, cards and policies are all synthetic. The module order
and the split between learner and instructor material follow the
[official Langfuse workshop](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/README.md).
This adaptation is not an official Langfuse release.

The root folder is the finished reference solution. Each module's starting state is
generated as its own folder by `scripts/checkpoint.py`. The presenter-led banking demo
this copy was taken from is preserved in [original-demo/](original-demo/README.md).

**The question it answers:** how does a team get from an agent it cannot see inside to a
prompt change it can defend with evidence?

<!-- demo-card:start -->
| | |
|---|---|
| **Status** | Draft |
| **Covers** | Trace · Monitor · Datasets · Experiment · Evaluate · Deploy |
| **Stack** | Python · LangGraph · MCP · FastAPI · Anthropic SDK · OpenAI SDK |
| **Runs as** | Workshop kit: facilitator-led, own toolchain and `.env` |
| **Langfuse** | Cloud · project `banking-workshop` |
| **Length** | 180 min |
| **Present it** | [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md) |
<!-- demo-card:end -->

- **Present it:** [WORKSHOP_GUIDE.md](WORKSHOP_GUIDE.md), the facilitator's run of show (180 minutes, nine modules)
- **Design record:** [SPEC.md](SPEC.md), with the transfer decision in [ASSESSMENT.md](ASSESSMENT.md)
- **Measured numbers:** [the 2026-10-12 live rehearsal](docs/2026-10-12-live-rehearsal.md) and [VALIDATION.md](VALIDATION.md)

## What it demonstrates

| Capability | Where it shows up |
|---|---|
| Tracing with Langfuse SDK v4 | Module 02: `src/northwind/tracing.py` turns on the LangChain `CallbackHandler` and `propagate_attributes`. One `northwind-assistant` agent root per turn, with generations, retrieval, guardrails and the MCP server's spans in the same trace |
| Export-stage PII masking | `mask_otel_spans=masking.mask_otel_spans` on the client (`src/northwind/config.py`); a synthetic card number is redacted before export |
| Prompt management | Module 03: `bank-workshop/<WORKSHOP_ID>/assistant` served by label `workshop-baseline`, linked to every generation, with a visible local fallback |
| Monitoring signals | Module 04: three BOOLEAN scores on the root observation (`src/northwind/monitoring.py`), plus an observation-level LLM judge the learner configures in the UI |
| Datasets | Module 05: eight synthetic items in `data/seed-dataset.json`, upserted to `bank-workshop/<WORKSHOP_ID>/regression-v1` |
| Experiments | Module 06: `dataset.run_experiment` runs the same `agent.run_turn` the portal uses, with four deterministic evaluators and one judge (`src/northwind/experiment_hooks.py`) |
| Evaluated prompt change | Module 07: label `workshop-candidate` adds one citation instruction; both runs on the same dataset version, then a rollback by label |

## Who it's for

Engineering teams who will instrument their own agent and want to practise the loop
hands-on, with a facilitator, over about three hours. Learners need Python and their own
model key. Pick the presenter-led [bank-assistant](../bank-assistant/) demo instead when
the audience wants a capability tour rather than a build: it starts fully instrumented and
seeded, and covers voice, n8n, APM and governance, which this workshop leaves out.

## What runs where

```
 browser ── portal  127.0.0.1:8090  (FastAPI, src/northwind/web.py)
               │  agent.run_turn(): LangGraph input-guardrail → assistant ⇄ tools → output-guardrail
               │  RAG: in-process TF-IDF over the fictional help centre (knowledge.py)
               ▼  traceparent + baggage in MCP _meta
            banking MCP server  127.0.0.1:8765/mcp  (streamable HTTP, mcp_server.py)
               │
               ▼  one Langfuse client per process; the portal flushes after every turn
 Langfuse Cloud project (one per attendee)
   trace `northwind-assistant` · session = one per portal conversation (browser UUID)
   tags northwind-assistant, channel:web, lang:<xx>, team:retail-digital, risk:<rule>
   environment `workshop` · version `bank-workshop-1.0 · prompt v<N>`
   scores guardrail-blocked, pii-in-input, all-caps-signal (module 04)
   experiments: trace `bank-workshop-experiment`, tags bank-workshop + experiment
```

`workshop.py serve` starts and stops both processes together; both bind to loopback only.

## Set up from scratch

Prerequisites: Python 3.12, `uv`, an OpenAI or Anthropic model key, and a dedicated
Langfuse Cloud project with its API keys. No Docker.

```bash
cd demos/bank-assistant-workshop
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env              # then set WORKSHOP_ID, the model key and the project's keys
.venv/bin/python scripts/seed_all.py --allow-draft   # facilitator's project only; see below
.venv/bin/python workshop.py serve                    # ask one question at http://127.0.0.1:8090
.venv/bin/python scripts/verify_demo.py               # should print READY
```

The `.env` is the only source of `LANGFUSE_*`: shell-exported keys are dropped, both by the
app (`config.py`) and by the seed and verify scripts. `./setup.sh` does not run this demo's
seed.

`seed_all.py` publishes both prompt labels and uploads the dataset, each only when it is
missing or has drifted from the code. Without a flag it uploads only reviewed references
(`--reviewed`), which the helper refuses while `reference_review_required` is still `true`.
`--allow-draft` is the owner-authorized rehearsal path: synthetic items only, review markers
kept. Learners do these steps by hand in modules 03, 05 and 07.

## Before a session

| When | Do | Why |
|---|---|---|
| A week before | Follow the [delivery guide](docs/preparation_and_materials/README.md): attendee projects and keys, spend limit, a second facilitator | Setup and keys are the usual blockers |
| The day before | `.venv/bin/python scripts/seed_all.py`, `workshop.py serve`, one question per module 02 path, then `scripts/checkpoint.py --build-all` | Seeded artifacts match the code, a fresh trace exists, starting states are current |
| 30 minutes before | `.venv/bin/python scripts/verify_demo.py --links` | Prints READY and the link each module opens |
| 5 minutes before | Open the links it printed | The trace, prompt and dataset you will show |

## Run it

```bash
.venv/bin/python workshop.py serve                         # reference app: portal + MCP server, Ctrl-C stops both
.venv/bin/python workshop.py chat "How much does an outgoing international wire cost?"   # one turn, prints JSON
.venv/bin/python workshop.py preflight                     # packages, model key present, Langfuse auth; no model call
```

**What you see in Langfuse:** one `northwind-assistant` trace per turn. Its agent root
carries the question and conversation as input and the answer, sources and tools as
output. Under it are the LangGraph nodes, the generations (model, tokens, the linked
prompt version), a `kb-retrieval` retriever observation with article bodies, the guardrails, and the
MCP client and server spans. The three monitoring scores sit on the root observation.

### Learner exercises

Each module starts from a generated folder that stops before that module's exercise:

```bash
.venv/bin/python scripts/checkpoint.py 02-tracing --output work/02-tracing
cd work/02-tracing
../../.venv/bin/python workshop.py --env-file ../../.env serve
```

00, 01 and 02 share the same untraced core; 03 starts with tracing done, 04 with prompts,
05 with monitoring, 06 with an unfinished experiment task, 07 and 08 with the experiment
solution. Existing folders are never overwritten, so learner edits survive. See
[maintainer notes](docs/MAINTAINER.md) for packaging.

| Module | Learner lesson | Instructor notes | Outcome |
| --- | --- | --- | --- |
| 00 | [Setup](docs/learner/00-setup.md) | [Notes](docs/instructor/00-setup.md) | Install, configure one dedicated Cloud project, start both services |
| 01 | [Base App](docs/learner/01-base-app.md) | [Notes](docs/instructor/01-base-app.md) | Tour the untraced bank assistant and its tools |
| 02 | [Tracing](docs/learner/02-tracing.md) | [Notes](docs/instructor/02-tracing.md) | Add callback tracing and one agent root; inspect MCP children |
| 03 | [Prompt Management](docs/learner/03-prompt-management.md) | [Notes](docs/instructor/03-prompt-management.md) | Retrieve, compile and link a labeled bank prompt |
| 04 | [Monitoring](docs/learner/04-monitoring.md) | [Notes](docs/instructor/04-monitoring.md) | Capture review signals and configure a semantic monitor |
| 05 | [Dataset](docs/learner/05-dataset.md) | [Notes](docs/instructor/05-dataset.md) | Review and upload eight reusable examples |
| 06 | [Experiments](docs/learner/06-experiments.md) | [Notes](docs/instructor/06-experiments.md) | Run the same agent against the dataset with callback evaluators |
| 07 | [Evaluation](docs/learner/07-evaluation.md) | [Notes](docs/instructor/07-evaluation.md) | Change citation instructions, rerun, compare evidence |
| 08 | [Wrap-up](docs/learner/08-wrap-up.md) | [Notes](docs/instructor/08-wrap-up.md) | Apply the engineering loop to another app |

## Evaluation

| What | Command | Notes |
|---|---|---|
| Publish a prompt label | `.venv/bin/python workshop.py prompt --variant baseline --label workshop-baseline` | Only `workshop-*` labels; every run creates a new version (`seed_all.py` does not) |
| Upload the dataset | `.venv/bin/python workshop.py dataset --reviewed` | Upserts eight items by stable id; refuses unreviewed references. `--allow-draft` for an authorized synthetic rehearsal |
| Run an experiment | `.venv/bin/python workshop.py experiment --prompt-label workshop-baseline` | Eight agent turns (each one or more model calls) plus eight judge calls. Cost depends on the model and was not measured |
| Deterministic-only run | `... experiment --prompt-label workshop-candidate --no-judge` | Drops the `correctness` judge |
| Compare on one dataset version | `... experiment --prompt-label <label> --dataset-version <timestamp>` | Same model, judge and concurrency for both runs |

Item scores: `must-include`, `cites-expected-source`, `language-match`, `refused-safely`
(blocked items only) and `correctness` (judge). The runner resolves the label once, pins
that version, runs only ACTIVE items and exits non-zero when a task fails, a run link is
missing or an applicable score is missing. In the 2026-10-12 rehearsal, the candidate
cited the expected source on 6 of 6 policy items against 5 of 6 for the baseline; the
[report](docs/2026-10-12-live-rehearsal.md) has the runs. Eight items do not establish a
reliable improvement rate.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `WORKSHOP_ID` | `bank-workshop` (`.env.example`: `don-bank`) | Namespaces the prompt and dataset: `bank-workshop/<WORKSHOP_ID>/...`. Lowercase letters, digits, hyphens |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | blank | Keys for the attendee's dedicated project |
| `LANGFUSE_BASE_URL` | `https://us.cloud.langfuse.com` | Langfuse Cloud region |
| `LANGFUSE_PROJECT_ID` | blank | Optional; turns the portal's trace link into a direct link |
| `LANGFUSE_TRACING_ENVIRONMENT` | `workshop` | Kept for the SDK; the client also sets `environment="workshop"` itself |
| `AGENT_MODEL` | `gpt-4.1-mini` | Agent model. `gpt-*`, `o1`, `o3`, `o4` use OpenAI; anything else uses Anthropic |
| `JUDGE_MODEL` | `AGENT_MODEL` | Model for the `correctness` judge |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | blank | Whichever provider the models need; preflight checks it is set |
| `NORTHWIND_PROMPT_LABEL` | `workshop-baseline` | Label the portal serves; module 07's rollback rehearsal changes it |
| `WORKSHOP_PORT`, `NORTHWIND_MCP_PORT` | `8090`, `8765` | Portal and MCP ports; `serve` refuses to start if either is taken |
| `DEMO_LANGFUSE_PROJECT` | `langfuse.project` in `demo.yaml` (`banking-workshop`) | Project name `verify_demo.py` expects the keys to map to; attendees set their own |
| `WORKSHOP_ENV_FILE` | the folder's `.env` | Set by `workshop.py --env-file`, so checkpoints share one private file |

## Layout

```
demos/bank-assistant-workshop/
├── demo.yaml               manifest: profile, coverage, commands (feeds the index)
├── README.md               this file
├── WORKSHOP_GUIDE.md       the facilitator's run of show
├── SPEC.md                 design record: what it proves, decisions, links to the evidence
├── ASSESSMENT.md           the 2026-10-11 transfer assessment
├── VALIDATION.md           local checks and the live acceptance checklist
├── .env.example            copy to .env
├── workshop.py             one entry point: preflight, serve, chat, prompt, dataset, experiment
├── src/northwind/          bank agent, LangGraph, RAG, MCP server, masking; four lesson seams
│                           (tracing.py, prompt_source.py, monitoring.py, experiment_hooks.py)
├── src/static/             the chat portal
├── data/seed-dataset.json  eight synthetic regression examples, pending human review
├── scripts/
│   ├── seed_all.py         the one seed entry point (idempotent, reconciles drift)
│   ├── verify_demo.py      readiness check: READY or NOT READY
│   ├── checkpoint.py       builds the nine starting states
│   ├── helpers.py          prompt, dataset and experiment helpers behind workshop.py
│   └── validate.py         package validator: source hashes, links, checkpoints
├── docs/                   learner/, instructor/, preparation_and_materials/, rehearsal and audit reports
├── tests/                  no services, no model calls
├── original-demo/          the unchanged source demo (voice, n8n, APM, governance arcs)
└── SOURCE_MANIFEST.json, NOTICE, LICENSE   revisions, copy hashes and attribution
```

## Tests

```bash
cd demos/bank-assistant-workshop
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m unittest discover -s tests -t .     # no services, no model calls, under a second
.venv/bin/python scripts/validate.py                    # package check; builds all nine checkpoints offline
```

They cover the copied guardrails, masking and business logic; the real LangGraph with fake
model and MCP responses; a real SDK export to an in-memory exporter (one nested trace,
masking, prompt linkage); experiment linkage and score coverage; and `verify_demo.py` and
`seed_all.py` against a fake Langfuse.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `keys map to project '…' (want 'banking-workshop')` | Another project's keys, or an attendee's own project. Put the right keys in `.env`, or set `DEMO_LANGFUSE_PROJECT` to your project's name |
| `Langfuse authentication failed` in preflight | Wrong key pair, or `LANGFUSE_BASE_URL` is another region |
| `Set OPENAI_API_KEY in the workshop .env.` | `AGENT_MODEL` picks the provider. A key can be present but lack access to the model; the first question proves it works |
| `Workshop ports must be distinct and free` | Another workshop is running. Stop it, or change `WORKSHOP_PORT` and `NORTHWIND_MCP_PORT` |
| `Complete lesson 02 tracing before using Cloud helper commands.` | The prompt, dataset and experiment helpers need tracing on; checkpoints 00 to 02 have it off |
| `dataset` refuses to upload | References are still marked for review. Review them and pass `--reviewed`, or `--allow-draft` for an authorized rehearsal |
| `Refusing to experiment on a fallback prompt.` | The label does not exist in this project: publish it first (module 03 or 07) |
| A card stays blocked | Card state lives in the MCP process. Stop and restart `serve` |
| `latest turn's scores: … missing …` | Scores land after the trace (wait and re-run), or the folder predates module 04 |

## Known limits

- Teaching app only. Customer selection simulates identity; the MCP server trusts a
  caller-supplied customer id and does not authenticate a real session.
- Regex and Luhn masking does not catch every kind of PII, names and addresses included.
- Retrieval is deterministic TF-IDF, not a production vector service.
- The eight dataset references still need instructor review, and eight items cannot
  establish release readiness.
- The platform LLM judge from module 04 was accepted by Cloud as a disabled draft but has
  not been executed in a preview; installing the archive on a second machine is also
  pending ([VALIDATION.md](VALIDATION.md)).
- `checkpoint.py` copies neither `WORKSHOP_GUIDE.md` nor `SPEC.md` into the starting
  states, so those two links are dead inside a `work/` folder.
- The resolved requirements pin Langfuse Python 4.17.0, LangGraph 1.2.14 and MCP 1.30.0.
  `propagate_attributes(prompt=...)` needs SDK 4.14.0 or later. The agent's `_nest` helper
  reads private callback state, so re-run the trace rehearsal after any upgrade.
- The original demo's historical evidence links are not evidence for this copy. See
  [the audit](docs/2026-10-11-adversarial-review.md) for findings and corrections.

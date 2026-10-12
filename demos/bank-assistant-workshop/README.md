# Bank Assistant Workshop

**DRAFT.** A separate Python/LangGraph banking workshop following the official Langfuse workshop's nine-module engineering loop. The existing banking demo is preserved in [original-demo/](original-demo/README.md). The transfer decision and constraints are in [ASSESSMENT.md](ASSESSMENT.md).

Learners build tracing, managed prompts, monitoring signals and dataset experiments on Northwind Bank's synthetic assistant. Instructors get separate notes and a [delivery guide](docs/preparation_and_materials/README.md). The root app is the complete reference solution. Exercise starting states are generated as independent folders; no remote Git checkpoint tags are assumed.

## Modules

| Step | Learner lesson | Instructor notes | Starting state | Outcome |
| --- | --- | --- | --- | --- |
| 00 | [Setup](docs/learner/00-setup.md) | [Notes](docs/instructor/00-setup.md) | `00-setup` | Install, configure one dedicated Cloud project, start both services. |
| 01 | [Base App](docs/learner/01-base-app.md) | [Notes](docs/instructor/01-base-app.md) | `01-base-app` | Tour the untraced bank assistant and its tools. |
| 02 | [Tracing](docs/learner/02-tracing.md) | [Notes](docs/instructor/02-tracing.md) | `02-tracing` | Add callback tracing and one agent root; inspect MCP children. |
| 03 | [Prompt Management](docs/learner/03-prompt-management.md) | [Notes](docs/instructor/03-prompt-management.md) | `03-prompt-management` | Retrieve, compile and link a labeled bank prompt. |
| 04 | [Monitoring](docs/learner/04-monitoring.md) | [Notes](docs/instructor/04-monitoring.md) | `04-monitoring` | Capture review signals and configure a semantic monitor. |
| 05 | [Dataset](docs/learner/05-dataset.md) | [Notes](docs/instructor/05-dataset.md) | `05-dataset` | Review and upload eight reusable examples. |
| 06 | [Experiments](docs/learner/06-experiments.md) | [Notes](docs/instructor/06-experiments.md) | `06-experiments` | Run the same agent against the dataset with callback evaluators. |
| 07 | [Evaluation](docs/learner/07-evaluation.md) | [Notes](docs/instructor/07-evaluation.md) | `07-evaluation` | Change citation instructions, rerun, compare evidence. |
| 08 | [Wrap-up](docs/learner/08-wrap-up.md) | [Notes](docs/instructor/08-wrap-up.md) | `08-wrap-up` | Apply the engineering loop to another app. |

The [live rehearsal report](docs/2026-10-12-live-rehearsal.md) records actual Cloud traces and experiments. The [handoff prompt](docs/CONTINUE.md) identifies the remaining platform-preview, human-review and second-machine checks.

The module order, split learner/instructor material and before-exercise states follow the [official workshop](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/README.md). Source: Web, official repository, HIGH confidence. Banking behavior comes from the [user-owned banking source](https://github.com/doneyli/clickhouse-llm-observability/tree/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant). This adaptation is not an official Langfuse release.

## Start the complete reference app

Python 3.12, `uv`, an OpenAI or Anthropic model key, and a dedicated Langfuse Cloud project are required. Core setup needs no Docker. Use Cloud for the current feature lessons, per the [official preparation guide](https://github.com/langfuse/langfuse-workshop/blob/fe01d263dd1dcda268f5e461e637394d9ec87676/docs/preparation_and_materials/README.md). HIGH confidence, source level 1. Self-hosted adds version/feature validation and belongs in the optional path.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env
# Edit .env privately. Set workshop ID, provider key, Cloud region and project keys.
.venv/bin/python workshop.py preflight
.venv/bin/python workshop.py serve
```

Open `http://127.0.0.1:8090`. Ctrl-C stops both the portal and the banking MCP server. Both bind to loopback. Restarting resets synthetic card state. The portal can run from a local prompt fallback; it displays which prompt version was used. Managed prompt work starts in module 03.

## Start a learner exercise

Run from this package's root after installing dependencies and configuring `.env` once:

```bash
.venv/bin/python scripts/checkpoint.py 02-tracing --output work/02-tracing
cd work/02-tracing
../../.venv/bin/python workshop.py --env-file ../../.env serve
```

Edit `work/02-tracing/src/northwind/tracing.py`, following the lesson in that folder. Restart services after the edit. To advance, stop services, return to the package root and generate the next checkpoint into a new folder. Later checkpoints contain the earlier reference solutions, so participants can catch up independently. Your previous folder and edits remain intact.

00, 01 and 02 have the same untraced core. 03 starts with tracing complete, 04 with prompts complete, 05 with monitoring complete, 06 with an unfinished experiment task, and 07/08 with the experiment solution. This is filesystem checkpoint delivery, not Git checkout delivery. See [maintainer notes](docs/MAINTAINER.md) for packaging.

## Repository layout

- `src/northwind/`: copied bank agent, LangGraph, RAG, MCP, masking and lesson integration points.
- `src/static/`: workshop chat portal.
- `docs/learner/`, `docs/instructor/`, `docs/preparation_and_materials/`: learning and delivery paths.
- `data/seed-dataset.json`: eight draft synthetic regression examples.
- `scripts/`: checkpoint builder and Cloud prompt/dataset/experiment helpers.
- `original-demo/`: unchanged full banking demo, including voice, n8n, APM, governance and advanced arcs.
- `SOURCE_MANIFEST.json`, `NOTICE`, `LICENSE`: revisions, copy hashes and attribution.

## Constraints

This is a teaching app. Customer selection simulates identity. MCP ownership checks use a caller-provided ID; this is not an authenticated banking service. Regex masking does not cover all PII. Retrieval is deterministic TF-IDF, not a production vector service. Dataset references need human review, and eight examples cannot establish release readiness. The full original demo's historical evidence links are not evidence for this copy. [Bank source and documented limits](https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/README.md).

The resolved requirements pin Langfuse Python **4.17.0**, LangGraph **1.2.14** and MCP **1.30.0**. `propagate_attributes(prompt=...)` requires SDK **4.14.0+**, per [MCP documentation](https://langfuse.com/docs/prompt-management/features/link-to-traces). See [VALIDATION.md](VALIDATION.md) for local checks and live rehearsal requirements, and [the Astra audit](docs/2026-10-11-adversarial-review.md) for findings and corrections.

## Review Before Sending

- [ ] Confidence levels reviewed for each recommendation
- [ ] Zone B/C flags addressed
- [ ] Documentation gaps noted and verified
- [ ] Scale warnings checked if applicable
- [ ] Provenance line removed after review

Not yet reviewed by a human. The doc-backed block is 100% official sources, linked above; the assessment block is labeled as such.

# Northwind Bank assistant — Langfuse for a regulated bank

A reusable, customer-agnostic demo of Langfuse for a large retail bank: a
LangGraph customer assistant (RAG with sources, banking tools behind an MCP
server, guardrails, PII masking, a voice channel) plus an n8n low-code
workflow, instrumented end to end, with online and offline evaluation, judge
calibration, prompt management with a CI quality gate, APM (Dynatrace)
correlation, and a self-hosted Enterprise instance for governance features.

Northwind Bank is fictional; all data is synthetic.

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md) — 2-hour run-of-show with labs
- **What it proves, by capability ID:** [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md)
- **Requirements and validation status, with evidence links:** [REQUIREMENTS_TRACKER.md](REQUIREMENTS_TRACKER.md)
- **Architecture and deployment models:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Security, SSO, RBAC, audit, retention, export:** [docs/ENTERPRISE_SECURITY.md](docs/ENTERPRISE_SECURITY.md)
- **Operating self-hosted Langfuse:** [docs/OPERATIONS.md](docs/OPERATIONS.md)
- **Production gates, TCO, adoption playbook:** [docs/PATH_TO_PRODUCTION.md](docs/PATH_TO_PRODUCTION.md)
- **APM / Dynatrace correlation:** [docs/DYNATRACE.md](docs/DYNATRACE.md)
- **Low-code (n8n):** [n8n/README.md](n8n/README.md)

## What runs where

```
 Northwind portal :8090 ──► LangGraph agent ──► MCP core-banking server :8765
   (chat, voice,              │   (guardrails, RAG,      (separate process,
    presenter console)        │    prompt by label)       joins the same trace)
                              │
            one OpenTelemetry TracerProvider
             ├─► Langfuse (AI spans, PII-masked)      Cloud project or self-hosted :3100
             └─► APM (payload-free copy, same ids)    Jaeger :16686 = Dynatrace stand-in
 n8n :5678 ── complaint-triage workflow ──► Langfuse
```

The demo runs against a **Langfuse Cloud** project. `docker-compose.yml` runs
n8n and Jaeger locally; `--profile selfhosted` (or `./scripts/up.sh --selfhosted`)
adds a full self-hosted Langfuse v4 Enterprise stack on :3100 — the starting
point for a self-hosted POC. The Python app runs from `.venv`.

## Targets

| Profile | Langfuse | Selected by |
|---|---|---|
| `cloud` (default when `.env.cloud` exists) | Langfuse Cloud project | `.env.cloud` (project keys) |
| `selfhosted` | http://localhost:3100 (EE license) | `NORTHWIND_PROFILE=selfhosted` |

Shell-exported `LANGFUSE_*` variables are ignored on purpose (`northwind/config.py`).

## Set up from scratch

```bash
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
cp .env.example .env            # set ENCRYPTION_KEY; model keys may come from the repo-root .env
./scripts/up.sh                 # n8n + Jaeger (add --selfhosted for local Langfuse EE)
# Cloud: put project keys in .env.cloud (LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)
.venv/bin/python scripts/seed_prompts.py
.venv/bin/python scripts/seed_evals.py
.venv/bin/python scripts/seed_datasets.py
.venv/bin/python scripts/seed_dashboard.py
.venv/bin/python scripts/seed_business_dashboard.py
./scripts/run_portal.sh         # portal + MCP server → http://localhost:8090
.venv/bin/python scripts/generate_traffic.py --scenario all
.venv/bin/python scripts/fill_annotation_queue.py 20
./scripts/run_all_experiments.sh --fresh   # prompt A/B (EN+ES), model comparison, gates, red-team
./scripts/setup_n8n.sh          # n8n workflow (see n8n/README.md)
.venv/bin/python scripts/make_voice_samples.py   # synthetic caller audio (already committed)
```

## Scripts

| Script | What it does | IDs |
|---|---|---|
| `scripts/generate_traffic.py` | multi-turn sessions, PII, red-team; `--environment`, `--sample-rate`, `--prompt-label` | OBS-01..05, EVA-04 |
| `scripts/run_voice_calls.py` | synthetic calls through the voice channel (audio in the trace) | OBS-01 multi-modal |
| `scripts/run_n8n_samples.py` | complaints through the n8n workflow | OBS-02 |
| `scripts/seed_evals.py` | judge LLM connection, score configs, 3 managed judges + rules, SME queue | EVA-01, 03, 04, 06 |
| `scripts/fill_annotation_queue.py` | worst-first SME sample | EVA-06 |
| `scripts/judge_calibration.py [--bakeoff]` | judge vs SME agreement; judge-model bake-off | EVA-06 |
| `scripts/seed_dashboard.py` | quality, risk and cost dashboard as code | EVA-07, OBS-05 |
| `scripts/seed_business_dashboard.py` | business value & failure-mode dashboard (value vs spend, containment, outcomes, each issue by prompt version) | GATE-05, EVA-07 |
| `scripts/generate_traffic.py --scenario business [--prompt-label staging]` | the four business issues, on production or as a canary | story arc |
| `scripts/seed_datasets.py` | golden Q&A with expected outputs; red-team set | EXP-01 |
| `scripts/run_experiment.py` | prompt A/B (`--prompt-label`), model comparison (`--model`) | EXP-02, EXP-03, EVA-05 |
| `scripts/prompt_gate.py` | CI gate: exit 1 on regression | EXP-06 |
| `scripts/prompt_label.py` | promote / roll back by moving labels | EXP-04, EXP-05 |
| `scripts/bootstrap_selfhosted.sh` | optional: same demo state on a local self-hosted EE instance | — |

## Known limits (say them out loud)

- Regex masking does not catch names or street addresses (needs NER in the hook).
- Langfuse has no native n8n tracing integration; see `n8n/README.md` for the pattern used.
- Langfuse ships no prompt-injection judge template; the demo uses runtime
  guardrail rules + a custom judge. Production would put LLM Guard, Lakera,
  NeMo or Bedrock Guardrails in the guardrail step.
- 16 golden items is a demo-sized dataset: small deltas between runs are noise.

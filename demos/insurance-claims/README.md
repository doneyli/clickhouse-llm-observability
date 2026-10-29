# Northwind Life & Annuity — death-claims agents with Langfuse

A standalone demo of Langfuse for a regulated life & annuity carrier: agents that process death claims
against product- and state-specific SOPs, instrumented end to end, evaluated against an SME-codified
ground truth, released through a prompt CI gate, reviewed by a human examiner, and run on a self-hosted
Enterprise instance. Northwind Life & Annuity is fictional; all SOPs, policies, claims and documents are
synthetic.

**The question it answers:** when an agent extracts the rules that apply to a claim, how do you prove it
did not miss one — a block on the claim proceeding, a commission chargeback, a state interest rule?

**Two use cases, one method.** Death claims (individual life, group life, annuity death benefits) and
**retirement / annuity servicing** — in-force withdrawals, surrenders and replacements checked against
suitability and best-interest rules (`claims/servicing.py`, `annuity-service-*` prompts,
`annuity-service-golden-v1`, `scripts/run_service.py`). Same SOP library, oracle, evaluators, guard and gate.

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md) — the talk track, act by act
- **Design:** [SPEC.md](SPEC.md) · **What it proves:** [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md)
- **Which evals belong to the platform and which to the use case:** [docs/EVALS_PLATFORM_VS_USE_CASE.md](docs/EVALS_PLATFORM_VS_USE_CASE.md)
- **CI/CD, the gate, Terraform:** [cicd/README.md](cicd/README.md)

## What runs where

```
Examiner workbench :8095 ──► LangGraph claims graph ──► policy-admin MCP server :8767 (separate process)
                               │      └──── A2A ───────► doc-intake service :9210 (Strands agent, own venv)
                               │  load-claim → intake → policy-coverage → screening → assemble → sop-rules
                               │  → critical-rule guard → adjudication → examiner review (interrupt) → letter
              one OpenTelemetry TracerProvider per process
               ├─► Langfuse (PII-masked)     Cloud project, or self-hosted Enterprise on :3200
               └─► APM, payload-free copy, same trace ids — Jaeger :16686 as the Dynatrace stand-in
```

One claim = one Langfuse session: the recommendation, the examiner's review (a LangGraph interrupt,
resumed), the examiner's questions. One trace spans three processes and two agent frameworks.

## Set up from scratch

```bash
cd demos/insurance-claims
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
uv venv --python 3.12 doc_intake/.venv && uv pip install -p doc_intake/.venv/bin/python -r doc_intake/requirements.txt
.venv/bin/python -m unittest discover -s tests -t .        # oracle contract (15 tests)
.venv/bin/python scripts/provision_cloud.py                # Cloud project + keys → .env.cloud (org key in .env.cloud-org)
.venv/bin/python scripts/make_sop_library.py               # data/sops/ from claims/catalog.py
.venv/bin/python scripts/make_claims.py                    # data/claims/ + coverage check
.venv/bin/python scripts/seed_prompts.py
.venv/bin/python scripts/seed_evals.py
.venv/bin/python scripts/seed_datasets.py
.venv/bin/python scripts/seed_dashboards.py
docker compose --profile apm up -d                         # Jaeger (skip if the bank demo's Jaeger runs)
./scripts/run_services.sh                                  # MCP + A2A + workbench → http://127.0.0.1:8095
./scripts/run_all_experiments.sh --fresh                   # the experiment matrix (~45 min)
./scripts/run_traffic.sh                                   # production-like traffic for dashboards + judges
./scripts/bootstrap_selfhosted.sh                          # optional: self-hosted Enterprise on :3200
.venv/bin/python scripts/verify_demo.py                    # readiness check
```

Model keys come from this demo's `.env` or the repo-root `.env`. Shell-exported `LANGFUSE_*` variables
are ignored on purpose (`claims/config.py`). Processes do not hot-reload: restart with
`./scripts/run_services.sh` after a code change.

## Scripts

| Script | What it does |
|---|---|
| `scripts/run_claim.py CLM-…` | one claim end to end, next to the oracle's expected outcome |
| `scripts/run_experiment.py --suite rules\|e2e\|adversarial\|intake` | experiments; `--prompt-label`, `--model`, `--release` |
| `scripts/prompt_gate.py --prompt-label staging` | the CI gate (exit 1 on a regression) |
| `scripts/prompt_label.py --show\|--promote L\|--rollback` | release by moving labels |
| `scripts/generate_traffic.py --scenario baseline\|canary\|release-1.4.0` | sessions: recommendation → review → question |
| `scripts/try_rules.py` | fast local prompt iteration on the rules step (no Langfuse) |
| `scripts/run_service.py SR-…` | one annuity service request end to end (`--rules-label staging` for the candidate) |
| `scripts/fill_annotation_queue.py` | examiner QA queue, worst first; `--random-labelled N` adds stand-in labelled cases |
| `scripts/judge_calibration.py` | rule-completeness judge vs examiner labels (agreement, kappa) |
| `scripts/seed_*.py` | prompts, judges + score configs + queue, datasets, dashboards, self-hosted RBAC |
| `scripts/verify_demo.py` | reads Langfuse back and checks the demo state |

## Seeded failure modes

| ID | What breaks | What catches it |
|---|---|---|
| F1 | `claims-sop-rules` v2 'concise' (label `staging`) lists only the top three rules | the gate: rule recall 0.75, commission flag 0.65 — every block survives, the money and notices leak |
| F2 | release `claims-1.4.0` drops all-state SOPs when a state SOP matches | `retrieval-coverage` 0.22 in the trace; online the critical-rule guard adds the missed blocks |
| F3 | instructions embedded in claim documents | intake flags them, SOP ALL-SIU-002 refers to SIU, `instruction-resistance` judge |
| F4 | v3 'conservative' lists anything possibly relevant | precision 0.94 (soft) |
| F5 | model change | the pre-upgrade experiment: Sonnet 4.6 precision 0.65 vs Sonnet 5.5 1.0; Haiku 5.5 passes the gate at a third of the cost |
| F6 | `annuity-service-rules` v2 'process-first' (label `staging`) leaves reviews to downstream desks | the service gate: critical recall 0.86, 3 critical misses; online the guard adds the codified reviews |

## Known limits (say them out loud)

- SOPs, policies, claims and documents are synthetic: the demo proves the method, not accuracy on a real
  carrier's SOPs. 22 SOPs and 53 rules stand in for a library of hundreds; the method does not change.
- The agents run locally; AgentCore is configuration (`DISABLE_ADOT_OBSERVABILITY=true` + an OTLP exporter
  to Langfuse), shown in docs, not live.
- The APM is Jaeger standing in for Dynatrace; the pattern is the same OTLP export.
- Regex masking does not catch free-text health details or names; server-side ingestion masking
  (Enterprise) or an NER step is the production answer.
- The completeness judge only sees the SOP text that was retrieved: it cannot catch a rule that was never
  retrieved. `retrieval-coverage` covers that blind spot.
- The examiner in generated traffic is simulated from the oracle; the value figures use labelled
  assumptions (`claims/business.py`).

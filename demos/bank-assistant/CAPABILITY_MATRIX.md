# Capability matrix — what the demo proves, and how

One row per capability ID in a typical bank AI-observability evaluation matrix.
**Live** = demonstrated on running software in the workshop. **Config** =
shown as configuration plus docs (requires the bank's tenant or infrastructure).
Script references point to [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

| ID | Capability | How it is shown | Status | Where |
|---|---|---|---|---|
| OBS-01 | End-to-end traceability, pro-code | LangGraph agent auto-traced with the Langfuse callback handler; root `agent` observation; sessions, users, tags, environments, release; voice channel with audio in the trace | Live | Act 1.1, 1.2, 1.6 · `northwind/agent.py`, `northwind/voice.py` |
| OBS-02 | End-to-end traceability, low-code n8n | n8n complaint-triage workflow traced into Langfuse | Live | Act 1.5 · `n8n/README.md` |
| OBS-03 | RAG, context and sources | `retriever` observation with document ids, source URLs, effective dates, scores; full context on the root | Live | Act 1.1 · `northwind/knowledge.py` |
| OBS-04 | Tools, MCP and agent iterations | MCP server in a separate process joined to the same trace via W3C context in MCP `_meta`; agent graph shows the loop | Live | Act 1.1 · `northwind/mcp_server.py` |
| OBS-05 | Tokens, latency and cost | per generation, per trace, per user, dashboards | Live | Act 1.1 |
| OBS-06 | Correlation with Dynatrace | one OTel pipeline, two exporters, same trace id; payload-free APM copy; links both ways | Live (Jaeger stand-in) · Config (Dynatrace) | Act 1.4 · `docs/DYNATRACE.md` |
| EVA-01 | Faithfulness / groundedness | managed LLM judge on production traffic against retrieved context | Live | Act 2.1 |
| EVA-02 | Correctness / factuality | LLM judge against expected outputs + deterministic must-include facts | Live | Act 2.3 · `northwind/evals.py` |
| EVA-03 | Bank-specific evaluator | `banking-compliance` judge (conduct policy P1–P5); `manipulation-resistance`; guardrail scores | Live | Act 2.1, 2.2, Lab 2 |
| EVA-04 | Online evaluation | judges + deterministic scores on every production turn; per-rule sampling and targeting | Live | Act 2.1 |
| EVA-05 | Offline evaluation | dataset experiments with item- and run-level evaluators | Live | Act 2.3, 5.2 |
| EVA-06 | Human feedback and annotation | customer 👍/👎 from the app; SME annotation queue; judge-vs-human agreement; judge-model bake-off | Live | Act 2.4 · `scripts/judge_calibration.py` |
| EVA-07 | Quality trends | score averages over time on dashboards | Live | Act 2.4 |
| EXP-01 | Golden dataset and expected outputs | `northwind-golden-qa-v1` (16 items), `northwind-golden-qa-es-v1` (10 Spanish items), `northwind-redteam-v1` (10, EN + ES) | Live | Act 2.3 |
| EXP-02 | Prompt A/B experiment | production vs staging label on the same dataset, compare view | Live | Act 5.2 |
| EXP-03 | Model / configuration comparison | Claude Sonnet vs GPT-4.1, same prompt and items | Live | Act 5.2 |
| EXP-04 | Prompt management, dynamic consumption | prompt fetched by label at runtime with cache + fallback; generations linked to the version | Live | Act 5.1 · `northwind/prompts.py` |
| EXP-05 | Prompt rollback | move the `production` label back; live within ~10 s; no redeploy | Live | Act 5.4 · `scripts/prompt_label.py` |
| EXP-06 | Quality gate / regression | `prompt_gate.py` exits 1 on the regression prompt (English and Spanish golden sets); GitHub Actions workflow triggered by a Langfuse prompt webhook | Live (local) · Config (CI) | Act 5.3 · `cicd/` |
| ENT-01 | SSO with Entra ID and RBAC | org roles + project-level role live; SCIM endpoint; Entra ID OIDC configuration for self-hosted | Live (RBAC, SCIM) · Config (Entra ID) | M4 · `docs/ENTERPRISE_SECURITY.md` |
| ENT-02 | Audit logs | prompt promotions/rollbacks, membership and key changes in the org audit log (Enterprise) | Live (Cloud org) | M4 |
| ENT-03 | Data protection and retention | client-side PII masking; per-project retention (90 days on the demo project); server-side ingestion masking (self-hosted EE) | Live (masking, retention) · Config (server-side masking) | Act 1.3, M4 |
| ENT-04 | Export and portability | REST API, UI export, scheduled export to S3 (Parquet/CSV/JSONL) | Live | M4 |
| GATE-01 | Governance, identity and access | ENT-01, ENT-02, protected prompt label | Evidence | M3 · `docs/PATH_TO_PRODUCTION.md` |
| GATE-02 | Security and data protection | masking, guardrails, red-team suite, retention | Evidence | M3 |
| GATE-03 | Architectural integration | LangGraph, MCP, n8n, voice, APM; AWS reference architecture | Evidence | M3 · `docs/ARCHITECTURE.md` |
| GATE-04 | Operation and resilience | health endpoints, HA topology, backups, upgrades, platform monitoring | Config | M4 · `docs/OPERATIONS.md` |
| GATE-05 | Value, Enterprise support and TCO | cost per turn/customer, judge cost lever, TCO worksheet, support model | Evidence | M3 · `docs/PATH_TO_PRODUCTION.md` |

**Bilingual:** every capability above can be shown in English or Spanish (portal EN | ES toggle); Spanish adds `language-match` and `formal-register` (usted) evaluators online and offline.

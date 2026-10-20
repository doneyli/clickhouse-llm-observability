# Capability matrix — what the demo proves, and how

**Live** = shown on running software. **Config** = shown as configuration + docs (needs the carrier's
tenant or infrastructure). Acts refer to [DEMO_SCRIPT.md](DEMO_SCRIPT.md).

| Area | Capability | How it is shown | Status | Where |
|---|---|---|---|---|
| Observability | One trace across agents, tools, services and the human | LangGraph orchestrator + MCP server + Strands agent behind A2A; examiner review and questions in the claim's session | Live | Act 1 · claims/graph.py |
| | Multi-modal tracing | scanned certificates and statements as media on the intake observation | Live | Act 1 |
| | Cost and latency per step, per claim, per model | generation costs; `claim-cost-usd`; report card | Live | Act 1, 4 |
| | OpenTelemetry to the APM (Dynatrace) | dual export, same trace id, payload-free copy | Live (Jaeger) · Config (Dynatrace) | Act 1 · docs/INTEGRATIONS.md |
| | AgentCore | OTLP export from the runtime | Config | docs/INTEGRATIONS.md |
| | Root-cause in the trace | retriever bug (F2): `retrieval-coverage`, guard WARNING | Live | Act 2 |
| Evaluation | Rule completeness against an SME answer key | oracle over codified rules; recall, critical recall, precision, citations | Live | Act 3 · claims/oracle.py, claims/evals.py |
| | Online deterministic checks | citation validity, retrieval coverage, critical-rule guard, no-pay-with-open-block, notices, PII, reading level | Live | Act 2 · claims/evals.py |
| | Managed LLM judges (incl. multi-modal) | rule-completeness, adjudication-faithfulness, letter-quality, instruction-resistance, certificate-extraction — selected by `metadata.role` | Live | Act 4 · scripts/seed_evals.py |
| | Human feedback | examiner approve/override with reason; annotation queue; score configs | Live | Act 1, 4 |
| | Platform vs use-case evals | the split and the mechanics | Live + doc | Act 4 · docs/EVALS_PLATFORM_VS_USE_CASE.md |
| | Datasets and experiments | golden (92), adversarial (12), multi-modal intake (24); prompt, model and release comparisons | Live | Act 3 |
| | Pre-upgrade model check | Sonnet 4.6 vs 5.5 vs Haiku 5.5 on the same items | Live | Act 3 |
| Prompt management | Central repository, versions, labels, config with the model | 6 agent prompts across two frameworks, fetched by label at runtime | Live | Act 5 |
| | Prompt linked to every generation | LangChain metadata + `propagate_attributes(prompt=)` for Strands | Live | Act 1 |
| | Promotion and rollback without redeploy | `scripts/prompt_label.py` | Live | Act 5 |
| CI/CD | Quality gate | `scripts/prompt_gate.py` exits 1 on F1 | Live (local) | Act 5 |
| | GitHub Actions on prompt change | Langfuse automation → repository dispatch → `.github/workflows/claims-prompt-gate.yml` (gates claims-sop-rules and annuity-service-rules) | Config (needs repo secrets) | cicd/README.md |
| | Terraform | official AWS module + config-as-code seeders | Config | cicd/terraform/main.tf |
| Governance (self-hosted Enterprise) | Projects per environment and team; project-level roles | claims-prod / claims-dev / annuities-prod; four demo roles | Live | Act 6 · scripts/seed_selfhosted_rbac.py |
| | Protected `production` label | MEMBER can't move it; ADMIN can | Live | Act 5 |
| | Audit log | the promotion and the rollback | Live | Act 6 |
| | Retention | 90 days on claims-prod | Live | Act 6 |
| | Client-side PII masking | SSN, DOB, bank, card, phone, email before export | Live | claims/masking.py |
| | Server-side ingestion masking, SCIM, SSO | configuration | Config | docs/INTEGRATIONS.md |
| Second use case | Retirement / annuity servicing on the same platform | withdrawals, surrenders, replacements vs suitability / best-interest SOPs; own prompts, dataset (22), gate; own self-hosted project `annuities-prod` | Live | Act 3b · claims/servicing.py |
| Evaluation | Judge calibration against examiners | annotation queue worst-first; agreement and kappa | Live (stand-in labels) | Act 4 · scripts/judge_calibration.py |
| Value | Value and quality dashboards | value vs spend (labelled assumptions), decisions, override reasons, quality by release · prompt version | Live | Act 6 · scripts/seed_dashboards.py |

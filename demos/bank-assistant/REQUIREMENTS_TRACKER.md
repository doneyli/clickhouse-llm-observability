# Requirements tracker — bank AI-observability evaluation

A running list of the requirements a large regulated bank typically brings to a
Langfuse evaluation — the success-criteria matrix (28 IDs), the requested
training modules, and the questions raised during scoping — with what this demo
has **validated** and the evidence for each. Applicable to any bank; the
customer is never named. Last updated: 2026-10-08.

**Demo target:** a Langfuse **Cloud** project (`northwind-bank-assistant`) in an
organization with Enterprise features. The bank's own target is self-hosted in
its AWS VPC — covered by the docs (`docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`).
Evidence links point to that Cloud project; regenerate them on your own project
with the scripts in the README.

Legend: ✅ validated live (evidence linked) · 🟡 built, needs a final check · 📄 configuration + docs (needs the bank's tenant/infrastructure) · ⏳ in progress · ❌ gap

---

## A. Success-criteria matrix (28 IDs)

| ID | Requirement | Status | Evidence / how |
|---|---|---|---|
| OBS-01 | E2E traceability, ProCode | ✅ | LangGraph agent with the Langfuse callback; 34-observation trace (guardrail → 3 LLM iterations → 3 MCP tools): [trace](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/f381fe5b43bf983b9d3b2ff27c099f49). Sessions, users, tags, environment and release are set on every turn. 40 traffic turns. |
| OBS-02 | E2E traceability, LowCode n8n | ✅ | n8n built-in OpenTelemetry (workflow + node spans) joined by `traceparent` with the workflow's own generation spans (model, tokens, cost, linked prompt via the official Langfuse n8n node). 6 traces, e.g. [card-fraud complaint](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/9695b0fc761c635fdfd7ad601c13e6d1), [PII-masked sample](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/5fbc710446cfcae911d847f5c5497571). Candour: no *native* Langfuse n8n tracing; this is the documented pattern (`n8n/README.md`). |
| OBS-03 | RAG, context and sources | ✅ | `kb-retrieval` retriever observation: doc ids, source URLs, effective dates, scores; full context in root `metadata.context`. [trace](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/3b8a8390e4449234d57c653df3b197bc) |
| OBS-04 | Tools, MCP and iterations | ✅ | MCP server runs in a separate process. Its spans join the same trace through W3C traceparent in MCP `_meta` (`mcp-client` → `mcp-server` → `core-banking.*`). Agent iterations are visible. Same trace as OBS-01. |
| OBS-05 | Tokens, latency and costs | ✅ | Per generation, trace, user and session; hero trace: 4 generations, 6,472 tokens, 13.9 s. Dashboard: cost by model, p95 latency. Claude vs GPT cost on the model-comparison runs. |
| OBS-06 | Dynatrace correlation | ✅ (stand-in) / 📄 Dynatrace | Same trace id in Jaeger: 34 spans, 2 services (`northwind-assistant`, `core-banking-mcp`). Payloads are stripped from the APM copy. Dynatrace needs only the endpoint and token (`docs/DYNATRACE.md`). |
| EVA-01 | Faithfulness / Groundedness | ✅ | Managed judge `faithfulness` on assistant turns (chat + voice): mean 0.96, n=115. Follow-up turns now carry earlier-conversation context so the judge doesn't mark them unsupported. |
| EVA-02 | Correctness / Factuality | ✅ | `correctness` judge vs expected output + deterministic `must-include`: production EN 0.91 / 1.00, ES 0.85 / 1.00 ([EN run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/d0a9afed-6401-4e71-9b76-aaf354962bbe)) |
| EVA-03 | Custom bank evaluator | ✅ | `banking-compliance` (conduct policy P1–P5): mean 0.92, n=115 (inspect the 0.0s — at least one is a judge false positive, a calibration talking point). `manipulation-resistance` 1.0, n=24 (100% of guardrail-flagged + 20% sample of the rest). Deterministic guardrail scores on every turn (EN + ES). |
| EVA-04 | Online evaluation | ✅ | 3 managed rules (sampling 100%, one targeted by tag) and 6 deterministic scores per turn. |
| EVA-05 | Offline evaluation | ✅ | Dataset experiments with item- and run-level evaluators (EN + ES): [EN production run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/d0a9afed-6401-4e71-9b76-aaf354962bbe) · [ES production run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/aa375259-5822-4948-837b-fe8100c830f5) |
| EVA-06 | Human feedback and annotation | ✅ | Customer 👍/👎 (`user-feedback`) on traffic + portal; SME queue (20 items, worst-first, score configs `sme-faithfulness` / `sme-compliance` / `sme-failure-mode`). **Judge calibration** as Langfuse experiments on `judge-calibration/faithfulness` (labels known by construction): Sonnet 4.6 1.00, Haiku 4.5 0.87, GPT-4.1-mini 0.80, Sonnet 5.5 0.73 — per-category breakdown in Act 2.4 ([Sonnet 4.6 run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2y1p3053yad0e2hpk0mb6/runs/aa64b188-9a42-4379-9e9c-7c88cc86c7a2)). Optional: label SME items live → `judge_calibration.py --bakeoff` vs human labels. |
| EVA-07 | Quality trends | ✅ | Seeded dashboard *Northwind — AI quality, risk and cost*: judge scores over time, feedback, security-risk mix ([dashboard](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/dashboards/cmuz2p34r05e2ad0ec9otchz3)). |
| EXP-01 | Golden dataset + expected outputs | ✅ | `northwind-golden-qa-v1` (16 items, 2 in Spanish), `northwind-golden-qa-es-v1` (10), `northwind-redteam-v1` (10, EN + ES), `judge-calibration/faithfulness` (15). Expected output, sources and must-include facts per item. |
| EXP-02 | Prompt A/B experiment | ✅ | production v1 vs staging v4 — EN: [v1](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/d0a9afed-6401-4e71-9b76-aaf354962bbe) · [v4](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/17952a0a-cf13-421a-a8ce-ae81ac75fe8a); ES: [v1](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/aa375259-5822-4948-837b-fe8100c830f5) · [v4](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/0ad3da07-f2fe-483d-97bc-ba26128a1b55). v4 wins on deterministic metrics: formal-register 0→1.00, no-upsell 0.92→1.00; judge correctness within noise. |
| EXP-03 | Model / config comparison | ✅ | Claude Sonnet 4.6 vs GPT-4.1 on the same prompt + items: cites-expected-source 0.88 vs 0.56, correctness 0.91 vs 0.88 ([GPT run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/b686530c-8a60-496d-9556-2549f25498ee)) |
| EXP-04 | Prompt management, dynamic consumption | ✅ | `northwind-assistant-system`: v1 `production`, v4 `staging`, v3 `development` (v2 superseded). Fetched by label at runtime (10 s cache + fallback); generations linked to the prompt version. |
| EXP-05 | Prompt rollback | ✅ | Promote staging → production and roll back, run on the Cloud project (production back on v1; `previous-production` label shows the history). No redeploy. |
| EXP-06 | Quality gate / regression | ✅ (EN + ES) | Gate on v3 'growth' prompt **exit 1** in both languages — EN: upsell 0.385, language 0.938 while the judge gave correctness 0.94 ([EN gate run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xuy1050yad0es71jynk1/runs/21644eb8-1363-4b18-ac88-2f1aee10efc7)); ES: language 0.40, formal 0.60. Candidate v4 passes the EN gate (exit 0). GitHub Actions workflow `.github/workflows/northwind-prompt-gate.yml` needs repo secrets (📄). |
| ENT-01 | SSO Entra ID and RBAC | 🟡 RBAC / 📄 Entra | Cloud org roles live (Settings → Members); project-membership API responds. Entra ID setup is in `docs/ENTERPRISE_SECURITY.md` (OIDC, no SAML). SCIM API responds on Cloud (Enterprise). |
| ENT-02 | Audit logs | 🟡 | Cloud org has Enterprise features; the label promote and rollback via API should appear. **Check:** Org Settings → Audit logs. |
| ENT-03 | Data protection and retention | ✅ | Client-side PII masking (EN + ES: card/Luhn, CVV/OTP/código, national ID/cédula, account/número de cuenta, email, phone) verified on Cloud ([card](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/6b7ae876477eda435a0a8d50afeb6838), [email](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/01b896eb98a811334d57e7279b53ba00)); retention 90 days set via API. ⚠️ One early trace (before a regex fix) holds a synthetic national ID unmasked — see open items. Server-side masking is self-hosted EE (📄). |
| ENT-04 | Export and portability | 🟡 | REST API used throughout (v2 observations, v3 scores). Blob-export integration API responds (no bucket configured). UI CSV/JSON export: show live. |
| GATE-01 | Governance, identity and access | 📄 + evidence | `docs/PATH_TO_PRODUCTION.md`; uses ENT-01/02 and protected label |
| GATE-02 | Security and data protection | ✅ evidence | masking, guardrails, red-team (10 items, EN + ES) refused-safely 1.0 ([run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz1xwvb04yead0eozy5z3q1/runs/86456e2a-5078-480e-a96e-588a0237fb19)), retention |
| GATE-03 | Architectural integration | ✅ evidence | LangGraph + MCP + n8n + APM live; AWS reference architecture in `docs/ARCHITECTURE.md` (ALB, no NLB) |
| GATE-04 | Operation and resilience | 📄 | `docs/OPERATIONS.md` (HA, scaling, backups, upgrades, monitoring) |
| GATE-05 | Value, Enterprise support and TCO | 📄 | `docs/PATH_TO_PRODUCTION.md` TCO worksheet + support model (no invented prices) |

## B. Requested training modules (2-hour session)

| Module | Requirement (from the doc) | Status | Where in DEMO_SCRIPT |
|---|---|---|---|
| M0 | Data model (traces, observations, sessions, scores, datasets, prompts) | ✅ | M0 |
| M0 | Loop: observe → evaluate → experiment → deploy | ✅ | M0 |
| M0 | Self-hosted Enterprise reference architecture; deployment models | 📄 | M0 · `docs/ARCHITECTURE.md` |
| M1 | ProCode and LowCode (n8n) instrumentation | ✅ | Acts 1.1, 1.5 |
| M1 | RAG traces (context and sources) | ✅ | Act 1.1 |
| M1 | Tools/MCP and agent iterations | ✅ | Act 1.1 |
| M1 | Tokens, latency, costs | ✅ | Act 1.1 |
| M1 | Dynatrace correlation via trace ID | ✅ stand-in | Act 1.4 |
| M1 | Multi-modal observability for audio | ✅ (voice module + 5 sample calls built; subagent verifying) | Act 1.6 |
| M1 | Sessions, users, tags, environments | ✅ | Act 1.2 |
| M1 | PII masking at ingestion | ✅ | Act 1.3 |
| M1 | Sampling strategy | ✅ trace sampling on the app's provider (verified 25% → 26%) + judge-rule sampling (`manipulation-resistance-sampled` at 20%) | Act 1.2 |
| M2 | LLM-as-a-judge (Faithfulness, Correctness) | ✅ | Acts 2.1, 2.3 |
| M2 | Online and offline evaluation | ✅ | Acts 2.1, 2.3 |
| M2 | Human feedback, annotation queues, quality trends | ✅ | Act 2.4 |
| M2 | Security-focused evaluators | ✅ | Act 2.2 |
| M2 | Judge calibration vs human criteria | ✅ calibration experiments (reference labels) · SME labels optional live | Act 2.4 |
| M2 | Criteria to choose the bank-approved judge model | ✅ criteria + 4-model bake-off as experiment runs (accuracy per category, cost, latency) | Act 2.4 |
| M3 | Walk through the 5 gates with the collected evidence | ✅ | M3 |
| M3 | TCO and Enterprise support model | 📄 | M3 |
| M3 | Adoption playbook: owners, naming conventions, onboarding | 📄 | M3 |
| M4 | SSO Entra ID and RBAC | 🟡 / 📄 | M4 |
| M4 | Audit logs | 🟡 | M4 |
| M4 | Data protection and retention | ✅ | M4 |
| M4 | Export and portability (API, exports) | 🟡 | M4 |
| M4 | Self-hosted ops: HA, scaling, backups, upgrades, platform monitoring | 📄 | M4 · `docs/OPERATIONS.md` |
| M5 | Golden datasets and expected outputs | ✅ | Act 2.3 / 5.2 |
| M5 | Prompt A/B, model and config comparison | ✅ | Act 5.2 |
| M5 | Prompt management with dynamic consumption, rollback | ✅ | Acts 5.1, 5.4 |
| M5 | CI/CD regression quality gate | ✅ local / 📄 CI | Act 5.3 |
| M5 | Prompt lifecycle: labels (dev/stg/prod), versioning, approval flow | ✅ labels + versions · 🟡 approval = protected `production` label (**set it in the UI**: Project Settings → Prompts → Protected labels) | Acts 5.1, 5.4 |

## C. Additional topics requested

| Note | Status | How |
|---|---|---|
| Industry view: other companies using Langfuse | 📄 | `docs/INDUSTRY.md` (public stories: Trade Republic, Ramp, SumUp, Merck; adoption stats). |
| Voice agents, multi-modal observability | ✅ | voice channel (Act 1.6) |
| Agent instrumentation, with examples | ✅ | LangGraph callback + manual observation types (agent, retriever, tool, guardrail) + MCP cross-process |
| Build your own evaluator and apply it to different datasets | ✅ / Lab | `banking-compliance` judge; Lab 2 builds `complaint-escalation` and runs it on live traffic and on the golden dataset |
| Security guardrails (evaluator review) | ✅ | input/output guardrails, red-team dataset, `manipulation-resistance` judge |
| Order: path to production as module 3, experimentation as module 5 | ✅ | run-of-show order M0 → M1 → M2 → M3 → M4 → M5 |

## C2. Bilingual delivery (English / Spanish)

| Requirement | Status | Evidence / how |
|---|---|---|
| Portal switchable to Spanish (toggle) | ✅ | EN \| ES toggle in the header (every UI string), Spanish chips, 3 Spanish voice calls ([es-1 lost card](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/40d02a2a5d04eb7abe0b867f9df6df73)), Spanish presenter acts 9–11 ([Spanish chat turn](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/traces/f5b14ea006a2f075eeacaf2c04c46774)) |
| Assistant answers in the customer's language; bilingual retrieval | ✅ | Spanish keywords indexed per article; Spanish queries retrieve the right policy (KB-202, KB-302, …) |
| Spanish guardrails | ✅ | Spanish injection / cross-customer / investment patterns; Spanish refusal; red-team incl. 2 Spanish attacks: refused-safely 1.0 |
| Spanish evals: language match, formal register (usted) | ✅ | `language-match` + `formal-register` scores on every turn (online) and in experiments (offline) |
| Spanish golden dataset + A/B | ✅ | `northwind-golden-qa-es-v1` (10 items): production v1 formal-register **0.00** (uses "tú") vs staging v4 **1.00** ([v1](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/aa375259-5822-4948-837b-fe8100c830f5) · [v4](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/0ad3da07-f2fe-483d-97bc-ba26128a1b55)) |
| Spanish CI gate | ✅ | `prompt_gate.py --dataset northwind-golden-qa-es-v1` on development: language-match 0.40, formal-register 0.60 → **exit 1** ([run](https://us.cloud.langfuse.com/project/cmuz1kt5z04uead0eyjm92c7f/datasets/cmuz2wt0m056oad0edbc59izw/runs/2d079628-1f14-4fdd-ba08-4343ab40bd7d)) |
| Spanish traffic | ✅ | `generate_traffic.py --scenario es` (8 Spanish conversations incl. attacks and PII) |

## D. Questions raised during scoping (expect them)

| Topic | Status / answer |
|---|---|
| Runs in the bank's AWS VPC with no internet egress | 📄 `docs/ARCHITECTURE.md`. Caveats to raise: EE telemetry, Entra login needs an egress proxy, mirror images to ECR, presigned S3 URLs for media. |
| ALB vs NLB concern | 📄 Langfuse web is plain HTTP; works behind an ALB. Stateful components are internal only. |
| ClickHouse not yet an approved database in the bank's architecture | Talking point: BYOC (ClickHouse-managed, inside the bank's VPC) vs self-managed via the ClickHouse operator |
| Spring AI (Java) | 📄 `docs/SPRING_AI.md` — verified recipe from the Langfuse Spring AI integration page + official example repo (not built into the demo) |
| Multi-turn / session evaluation (N+1) | 📄 Talking point: real-estate demo pattern. Native session evaluation is expected soon (not verified). |
| Langfuse Cloud vs self-hosted: same software | ✅ M0 talking point |
| Frameworks in use: n8n, LangChain/LangGraph, Spring AI (Java) | ✅ n8n, ✅ LangGraph · 📄 Spring AI (`docs/SPRING_AI.md`) |

## Open items before the session

- [ ] **Label 8–10 SME queue items** (`sme-faithfulness`, `sme-compliance`) so
      Scores → Analytics (judge vs human) has pairs; then
      `scripts/judge_calibration.py --bakeoff`
- [ ] Protect the `production` prompt label (Project settings → protected prompt labels)
- [ ] Confirm Settings → Audit logs is visible on the org (ENT-02); else present from docs
- [ ] Optional clean-up in the Langfuse UI (deletion is the owner's call):
      the trace with an unmasked *synthetic* national ID from before the masking
      fix — `54f2beeae9f211f33cb56c9c465be073` (+ its 2 judge traces) — and the
      4 ERROR traces from the first smoke test (session `smoke-1`, user C-1001).
      Use "last 1 hour" views in the session and they won't show anyway.
- [ ] Morning prep in DEMO_SCRIPT.md (restart MCP, `verify_demo.py`, warm-up traffic)
- [x] n8n, voice (5 EN + 3 ES), portal (EN | ES), CI workflow, adversarial review (Fable) — done

# Path to production (Module M3)

**What this covers:** the five gates a 30-day self-hosted Enterprise POC must pass, a parameterized TCO worksheet, the enterprise support model, and an adoption playbook (RACI, naming conventions, onboarding, golden paths).
**Capability IDs:** GATE-01..05. Gates draw evidence from ENT-01..04, OBS-01..06, EVA-01..07, EXP-01..06.

How to use this document. Each gate lists its pass criteria, the evidence to collect, the place in this demo that shows it working, and an owner. Copy the tables into the POC tracker, and attach evidence as it is produced, not in week 4.

---

## GATE-01: Governance, identity and access

| | |
|---|---|
| **Objective** | Only authorized bank identities reach Langfuse, with least privilege, and every change is attributable |
| **Pass criteria** | 1. Entra ID OIDC sign-in works for test users in ≥ 3 personas; password login disabled; SSO enforced for the bank domain; Entra "Assignment required" gates sign-in. 2. Role matrix implemented. A user with a project role in project A cannot see project B (negative test). 3. Joiner/mover/leaver works end to end (SCIM or Org Management API): removal revokes access within the agreed SLA. 4. Project API keys are per project and environment, live in Secrets Manager, and have an expiry. Rotation rehearsed. 5. The `production` prompt label is **protected**: a Member's attempt to move it fails, and an Admin's promotion succeeds and appears in the audit log with actor and before/after state. 6. A break-glass procedure is documented and tested |
| **Evidence** | Redacted SSO config; role-matrix sheet; screenshots of the negative tests; audit-log export (CSV) covering a label promotion, an API-key creation and a membership change; JML test log |
| **Demo source** | SSO block in `../docker-compose.yml`; label lifecycle in `../northwind/prompts.py` (v1 production, v2 staging, v3 development); prompt seeders in `../scripts/`; audit-log viewer in the UI. Configuration in [ENTERPRISE_SECURITY.md](ENTERPRISE_SECURITY.md) |
| **Owner** | AI platform team (lead), IAM team; risk & compliance (sign-off) |

## GATE-02: Security and data protection

| | |
|---|---|
| **Objective** | Customer data is handled per bank policy: masked before storage, encrypted, retained only as long as allowed, and never sent to unapproved destinations |
| **Pass criteria** | 1. Client-side masking is on. Scripted conversations containing synthetic card, account, ID and OTP values show only `[REDACTED_*]` in Langfuse. 2. Server-side ingestion masking (EE) runs **fail-closed** and is proven by stopping the masker (events dropped, not stored raw). 3. The APM copy of the spans carries no prompt or completion attributes. 4. KMS at rest on RDS, ElastiCache, S3 and ClickHouse; TLS on every hop. 5. Retention is set on every project and the nightly deletion is verified. The events-bucket lifecycle is set and the media bucket has none. 6. The egress inventory is approved, with a written decision on EE license telemetry. Firewall logs show no other destinations over the POC. 7. Image vulnerability scans pass the bank threshold. SSRF allowlists hold only named internal hosts. Code evaluators are disabled or use `aws-lambda` |
| **Evidence** | Sample masked traces; APM span-attribute dump; KMS/TLS config export; retention API response and before/after counts; egress firewall report; scan reports; vendor SOC 2 Type II / ISO 27001 reports |
| **Demo source** | `../northwind/masking.py` (Luhn-gated card redaction, keyword-anchored account and ID rules); `../northwind/config.py` (payload stripping on the APM exporter); Jaeger UI at `http://localhost:16686` to inspect the stripped spans; `TELEMETRY_ENABLED` in `../docker-compose.yml` |
| **Owner** | Information security, data protection officer, AI platform team |

## GATE-03: Architectural integration

| | |
|---|---|
| **Objective** | Langfuse fits the bank's reference architecture and toolchain without exceptions |
| **Pass criteria** | 1. Deployed from the bank pipeline with Terraform/Helm on EKS, behind an **internal ALB**, with images from ECR and the stores described in [ARCHITECTURE.md](ARCHITECTURE.md). 2. A LangGraph app produces the expected trace shape: an `agent` root with turn I/O, `retriever` with document ids and sources, `tool` (MCP), `generation` with model, tokens and cost, and `guardrail`. Sessions, users, environments and release are populated. 3. **One trace spans agent → MCP client → MCP server** across two services. 4. Voice-channel audio is attached and plays in the UI. 5. n8n workflows are covered by the agreed pattern. 6. **The same trace id is visible in Dynatrace and Langfuse**, with deep links both ways. 7. Judges run through Bedrock PrivateLink or the internal gateway. 8. A Langfuse alert creates a Dynatrace event or incident. 9. The CI gate blocks the regressed prompt. 10. Scheduled export lands Parquet in the data-platform bucket and is loaded |
| **Evidence** | As-built diagram; IaC repo and pipeline run; trace screenshots/links; the Dynatrace ↔ Langfuse trace-id pair; CI run that failed on the regression; export manifest |
| **Demo source** | `../northwind/agent.py`, `mcp_server.py` (trace context in MCP `_meta`), `knowledge.py` (retriever), `config.py` (shared TracerProvider); `../n8n/`; seeders in `../scripts/`; [DYNATRACE.md](DYNATRACE.md) |
| **Owner** | Enterprise architecture, AI platform team, pilot application team |

## GATE-04: Operation and resilience

| | |
|---|---|
| **Objective** | The platform meets production SLOs and the bank's team can run it |
| **Pass criteria** | 1. HA as designed: ≥ 2 web, ≥ 2 worker, Multi-AZ RDS, Redis with failover, ClickHouse 3 replicas + 3 Keeper or BYOC. 2. A load test at 2× expected peak: queue depth returns to baseline, ingestion-to-visible lag stays within target, and the UI p95 stays within target. 3. Failure tests: kill web/worker pods, Redis failover, lose a ClickHouse replica, cordon an AZ. No accepted event is lost. 4. Restore drill (Postgres PITR + ClickHouse restore) within RTO/RPO. 5. A minor-version upgrade rehearsed with no ingestion loss. 6. Platform dashboards and alerts live in Dynatrace. The runbook has been walked through, including the worker-stall recovery |
| **Evidence** | Load-test report; failure-test log with timestamps; restore-drill record; upgrade log; Dynatrace dashboard export |
| **Demo source** | [OPERATIONS.md](OPERATIONS.md) runbook; health and readiness checks in `../scripts/up.sh`; healthchecks in `../docker-compose.yml` |
| **Owner** | AI platform team / SRE |

## GATE-05: Value, Enterprise support and TCO

| | |
|---|---|
| **Objective** | Proven value, a sustainable cost model and an agreed support model |
| **Pass criteria** | 1. At least 3 use cases show a measured improvement, e.g. time to root-cause a bad answer, a regression blocked by the CI gate, or judge/human agreement (Cohen's kappa or F1 in score analytics) above the agreed bar. 2. The TCO worksheet below is filled with **measured** POC data and the bank's AWS prices. 3. The support model is agreed: severity definitions, escalation path, coverage of both Langfuse and ClickHouse. 4. The adoption plan has named owners (RACI below) |
| **Evidence** | Before/after metrics; completed worksheet; draft support terms; signed RACI |
| **Demo source** | Experiment compare view (v1 vs v2 vs v3 prompts); score analytics; dashboards; this document |
| **Owner** | AI platform lead (sponsor), finance, procurement |

---

## TCO

### Cost drivers

| Driver | Scales with | Lever |
|---|---|---|
| Web/worker compute (EKS) | Peak ingest rate, UI/API usage, judge throughput | Autoscaling; split ingestion from UI web pods |
| ClickHouse storage | Observations × stored payload × retention | Retention per project; masking and trimming of payloads; sampling |
| ClickHouse compute | Ingest rate, dashboard and API query load | Time-filtered queries; read-only compute group (BYOC) |
| PostgreSQL | Users, prompts, datasets, configs (small) | Right-size the instance; Multi-AZ is required |
| Redis / Valkey | Peak events per minute (~1 GB per 100k/min) | Node size; cluster mode only when needed |
| S3 | Raw-event replay window, media (voice audio), exports | Events lifecycle; retention deletes media; export field groups |
| **LLM-judge tokens** | Traces × sampling rate × judges × tokens per eval | **Often the largest variable cost.** Evaluator sampling %, narrow filters (root observation only, production only), cheaper judge models where calibration allows |
| People and ops | Number of teams and projects, upgrade cadence, SME annotation hours | Golden-path templates; evaluators and dashboards as code; support contract |

### Estimation worksheet

Do not use list prices from this document. Plug in the bank's AWS pricing (EKS/EC2, RDS, ElastiCache, S3, Bedrock), the ClickHouse BYOC quote and the Langfuse EE quote.

**Inputs** (measure during the POC wherever possible):

| Symbol | Input | How to obtain |
|---|---|---|
| T | Traces/day (average; also peak hour) | App traffic forecast |
| O | Observations per trace | POC: observations ÷ traces |
| P | Avg stored bytes per observation in ClickHouse, after compression | POC query below |
| Pr | Avg raw event size in S3 (KB) | Sample the events bucket |
| R | Retention days (per project) | Policy |
| L | Events-bucket lifecycle days | Replay policy (e.g. 30) |
| Rep | Storage replication factor | Operator: 3; BYOC/Cloud: per vendor |
| M | Media bytes/day (voice audio) | Minutes/day × encoded bitrate |
| s | Judge sampling rate (0–1) | Evaluator config |
| J | Judges per sampled trace | Evaluator config |
| Ti / To | Judge input / output tokens per evaluation | POC: Bedrock/gateway usage report ÷ evaluations run |
| W | Events/s one worker sustains at 50% CPU | POC load test |
| H | SME annotation minutes/day | Queue plan |

**Derived quantities:**

```
observations/day        N   = T × O
peak events/s           E   = N × peak_factor / 86,400
worker replicas             = ceil(E / W) + 1                     # N+1
ClickHouse storage          ≈ N × P × R × Rep                     # steady state, before overhead
S3 events                   ≈ N × Pr × L
S3 media                    ≈ M × R
Redis memory                ≈ 1 GB × (peak events/min ÷ 100,000)  # plus headroom
judge evaluations/day   V   = T × s × J
judge tokens/day            = V × (Ti + To)
judge cost/day              = V × (Ti × price_in + To × price_out)
monthly cost                = Σ (quantity × unit price from your AWS / vendor pricing) + people time
```

Measure **P** after the load test (in the Langfuse ClickHouse database):

```sql
SELECT table, sum(rows) AS rows,
       formatReadableSize(sum(data_compressed_bytes)) AS on_disk,
       round(sum(data_compressed_bytes) / sum(rows)) AS bytes_per_row
FROM system.parts WHERE active AND database = 'default'
GROUP BY table ORDER BY sum(data_compressed_bytes) DESC;
```

To get P, divide the bytes of all tables that hold observations (v4: `events_full` and `events_core`) by the observation count. On a replicated operator cluster, run the query on one replica and multiply by Rep.

**Why judges dominate:** with illustrative inputs of T = 50,000 traces/day, s = 10%, J = 3 judges and Ti + To = 2,200 tokens, that is 15,000 evaluations and ~33M judge tokens **per day**, before any experiments. Storage for the same traffic is bounded by retention, but judge spend grows linearly with traffic and with every judge added. Control it with sampling rules and filters first.

### Enterprise support model

- **Severity-based response targets** and an escalation path, with **follow-the-sun** coverage.
- **One support channel for the full stack:** Langfuse (control plane: web, worker, SDKs) **and** ClickHouse (data plane: BYOC/Cloud).
- Per the [self-hosted pricing page](https://langfuse.com/pricing-self-host), Enterprise includes a named lead support engineer, solutions-architect support during evaluation and rollout, a private Slack channel, a product-team feedback channel, a support SLA, and SOC 2 Type II / ISO 27001 reports. Billing is via AWS Marketplace or invoice. Langfuse pricing is additive to the ClickHouse commercial plan.
- **Response times, coverage hours and severity definitions are set per contract and scale with volume.** Agree them in writing before go-live. This document does not quote them.

---

## Adoption playbook

### RACI (R = responsible, A = accountable, C = consulted, I = informed)

| Activity | AI platform team | App teams | Risk & compliance | SMEs / annotators | InfoSec |
|---|---|---|---|---|---|
| Run the platform (deploy, upgrade, backup, monitor) | **A/R** | I | I | n/a | C |
| SSO, roles, project creation | **A/R** | C | C | n/a | C |
| Masking policy library + server-side masker | **A/R** | C | C | n/a | **C** |
| Retention and export policy per data class | R | C | **A** | n/a | C |
| Instrumentation of an app (golden path) | C | **A/R** | n/a | n/a | n/a |
| Prompts, labels, promotion to `production` | C (protects label) | **A/R** | I | C | n/a |
| Datasets, experiments, CI gate thresholds | C | **A/R** | C | C | n/a |
| Judge design and calibration vs humans | C | **A/R** | C | **R** (labels) | n/a |
| Annotation queues (review, error analysis) | I | A | C | **R** | n/a |
| Alerts → Dynatrace / incident tool | R | **A** (app SLOs) | I | n/a | n/a |
| Audit-log review | I | I | **A/R** | n/a | C |

### Naming conventions

| Object | Convention | Example | Why |
|---|---|---|---|
| Project | One per application | `retail-assistant` (the demo project) | RBAC, keys, retention and prompts are project-scoped |
| Environment | `production`, `staging`, `development` **inside** the project | `LANGFUSE_TRACING_ENVIRONMENT=production` | Prompts, datasets and judges are shared across environments. If production data must be restricted to fewer people, use **separate projects** per environment instead, because RBAC is per project, not per environment ([FAQ](https://langfuse.com/faq/all/managing-different-environments)) |
| Trace (root observation) name | Low-cardinality, stable, no ids | `assistant-turn`, `voice-turn` | Filters, dashboards and judge targeting depend on it |
| Observation names | `<component>.<action>`, stable across releases | `kb.search`, `mcp.block_card`, `guardrail.pii`, `llm.answer` | Readable trees; per-step metrics |
| Tags | `key:value` from a registered list; never PII | `channel:voice`, `channel:chat`, `team:cards`, `use-case:dispute` | Segmentation without cardinality blow-ups |
| Metadata | Free-form context, no PII | `{"kb_version": "2026-10"}` | Debugging; judge selection by metadata |
| Prompt | `<app>-<purpose>` | `northwind-assistant-system` (demo) | Discoverable; one prompt, many versions |
| Prompt labels | `production` (**protected**), `staging`, `development` | Fetch by label, never by version, in apps | Promotion and rollback without redeploys |
| Dataset | `<app>-<purpose>-<version>` | `northwind-assistant-golden-v1`, `northwind-assistant-injection-v1` | Comparable experiments over time |
| Score | kebab-case, backed by a **registered score config** (type, range or categories) | `groundedness`, `pii-leak`, `policy-compliance`, `user-feedback` | Consistent analytics; judge and human scores comparable |
| Release | App semver | `assistant-1.4.0` | Before/after comparisons |
| Users / sessions | Pseudonymous customer handle; conversation or call id | `cust-7f3a…`, `call-…` | Per-customer views without PII |

### Onboarding checklist for a new team (day 0 → day 30)

- **Day 0:** platform team creates the project (Org API), assigns Entra groups and project roles, issues API keys per environment into Secrets Manager, sets retention, and registers the team's tags and score configs.
- **Days 1–3:** instrument from the golden-path template: shared TracerProvider, `propagate_attributes(session_id, user_id, tags, version)`, environment and release, masking hook, flush on exit. Review one trace against the naming conventions with the platform team.
- **Week 1:** move hard-coded prompts into Langfuse and fetch them by label (cache and fallback configured). Build dashboards for cost, latency and errors per release. Link alerts to the Dynatrace automation.
- **Week 2:** build the first dataset (≥ 50 items from real or scrubbed traffic and SME-written edge cases) and run a baseline experiment. Add 1–3 observation-level judges with sampling. Open an annotation queue for SMEs.
- **Week 3:** calibrate the judges against SME labels in score analytics and adjust until agreement meets the bar. Add the CI gate to the prompt/repo pipeline.
- **Week 4:** production-readiness review against GATE-01..04 for the app: retention confirmed, export configured, on-call knows the runbook, rollback rehearsed by moving the label.

### Golden-path templates (owned by the platform team)

1. **Python LangGraph service**: the `../northwind/config.py` pattern. One TracerProvider, Langfuse processor plus an APM processor with payloads stripped, masking, sampling, flushing both exporters.
2. **MCP server**: W3C trace context read from MCP `_meta`, server-side authorization (`../northwind/mcp_server.py`).
3. **Retriever contract**: a `retriever` observation whose output lists document ids, sources and scores (`../northwind/knowledge.py`).
4. **Prompt client**: fetch by label, cache TTL, fallback prompt, prompt linked to the generation (`../northwind/prompts.py`).
5. **Masking library + unit tests**: shared regex/NER policy and the server-side masker contract.
6. **Experiment runner + CI workflow**: `dataset.run_experiment(...)` plus GitHub Action `langfuse/experiment-action` (SDK ≥ 4.7) with thresholds.
7. **Evaluators as code**: judges and code evaluators through the stable API (`/api/public/v2/evaluators`, `/api/public/v2/evaluation-rules`, server ≥ 4.23), plus a score-config registry.
8. **Dashboards and alerts as code**, and the alert-to-Dynatrace webhook bridge ([DYNATRACE.md](DYNATRACE.md)).
9. **Voice channel**: audio attached with `LangfuseMedia`; transcript on the generation; per-call session.
10. **Low-code (n8n)**: the approved tracing pattern, plus the Langfuse prompt node.

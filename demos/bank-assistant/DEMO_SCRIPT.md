# Northwind Bank — 2-hour Langfuse workshop script

A presenter script for a large regulated bank's AI team (data science,
analytics, architecture) ahead of a self-hosted Enterprise proof of concept.
Every act names the capability IDs it proves, so the room can tick off its own
evaluation matrix as you go. Each act has four beats:

- **Frame** — the problem in the bank's terms, before you touch the screen
- **Show** — the exact clicks or commands
- **Land** — the one-sentence takeaway
- **Ask** — an open question that maps it to their world (write the answers down)

Northwind Bank is fictional. All customers, accounts and policies are synthetic.

**Bilingual.** The portal has an **EN | ES** toggle (header). The assistant answers
in the customer's language; Spanish has its own golden dataset, guardrail
patterns, `language-match` and `formal-register` (usted) evaluators, red-team
items and voice calls. Run any act in Spanish by switching the toggle and using
the Spanish chips — the Spanish beats are marked 🇪🇸.

---

## Screens and tabs (open before you start)

| Tab | URL | Used in |
|---|---|---|
| Northwind portal (the bank's app) | http://localhost:8090 | every act |
| Langfuse Cloud project `northwind-bank-assistant` | portal header → "Langfuse" | every act |
| APM stand-in for Dynatrace (Jaeger) | http://localhost:16686 | M1 |
| n8n | http://localhost:5678 | M1 |
| Docs | `docs/ARCHITECTURE.md`, `docs/INDUSTRY.md`, `docs/ENTERPRISE_SECURITY.md`, `docs/OPERATIONS.md`, `docs/PATH_TO_PRODUCTION.md`, `docs/DYNATRACE.md`, `docs/SPRING_AI.md` | M0, M1, M3, M4 |
| Lab handout | `LABS.md` | M1, M2, M5 |
| Requirements + evidence | `REQUIREMENTS_TRACKER.md` (live links per requirement) | close |

The portal's **Presenter console** tab runs every scripted step with one click
and prints the Langfuse links. Use it instead of a terminal on screen.

## Run-of-show (120 min)

| Time | Module | Capability IDs | Format |
|---|---|---|---|
| 0:00–0:10 | M0 Foundations and architecture | — | talk + one screen |
| 0:10–0:45 | M1 Observability (tracing) | OBS-01..06 | demo + **Lab 1** |
| 0:45–1:10 | M2 Evaluation | EVA-01..07 | demo + **Lab 2** |
| 1:10–1:20 | M3 Path to production | GATE-01..05 | talk over evidence |
| 1:20–1:40 | M4 Enterprise: security, governance, operations | ENT-01..04 | Cloud org settings + self-hosted docs |
| 1:40–1:55 | M5 Experimentation and prompts | EXP-01..06 | demo + **Lab 3** |
| 1:55–2:00 | Close: POC plan and next steps | — | talk |

If you run short: cut Lab 3 (show it instead), then shorten M3 to the gate table.

---

## M0 · Foundations and architecture (10 min)

**Frame.** "Every team here has asked the same three questions about an AI
assistant in production: what did it do, was it right, and what changed when it
got worse. Langfuse answers those with six objects. Let's name them once, then
see them on real traffic."

**Show.**
1. On a slide or whiteboard, draw the data model (from `docs/ARCHITECTURE.md`):
   **trace** (one customer turn) → **observations** (each step: generation,
   retriever, tool, guardrail, agent) · **session** (the conversation) ·
   **scores** (any judgement: code, LLM judge, human, customer 👍/👎) ·
   **datasets** (golden examples with expected outputs) · **prompts**
   (versioned, served by label).
2. The loop: observe → evaluate → experiment → deploy → observe.
3. Deployment models, side by side: Langfuse Cloud · self-hosted with
   ClickHouse Cloud · self-hosted with ClickHouse BYOC inside the bank's VPC.
   Show the AWS reference architecture diagram in `docs/ARCHITECTURE.md`
   (EKS behind an **Application** Load Balancer, RDS Postgres, ElastiCache,
   S3 over a VPC endpoint, ClickHouse, Bedrock over PrivateLink for judges).
4. "Today we work in a Langfuse Cloud project because it's the fastest way to
   show everything. It is the same software you'll run in your AWS account: web,
   worker, Postgres, ClickHouse, Redis, S3. For the POC, day one is the
   `docker compose` in `docs/ARCHITECTURE.md`; production is the Helm chart or
   the Terraform module."

5. Industry view (2 min, `docs/INDUSTRY.md`): public adoption numbers and
   financial-services users with published stories (e.g. Trade Republic runs it
   self-hosted). Keep it to public sources.

**Land.** Cloud and self-hosted run the same codebase. The Enterprise license
switches on governance features (RBAC per project, audit logs, retention,
protected labels, server-side masking); it doesn't change the product.

**Ask.** "Which of these components already exists as an approved service in
your AWS landing zone, and which one needs an architecture review?"

---

## M1 · Observability — tracing (35 min)

### Act 1.1 — one customer turn, end to end · OBS-01, OBS-03, OBS-04, OBS-05

**Frame.** "A customer says: 'the app told me my dispute was opened, but I
never got a case number.' Today, how long does it take your team to find out
what the assistant actually did?"

**Show.**
1. Portal → **Customer assistant** → customer **Ana Torres (C-1001)**, channel
   **web** → chip *"I don't recognise a charge from UNKNOWN MERCHANT LAGOS —
   dispute it"*. Note the tool badges (`list_accounts → get_recent_transactions
   → open_dispute`) and the case number in the answer.
2. Click **Open in Langfuse**. Walk the trace top to bottom:
   - root `agent` observation `northwind-assistant`: input = the customer's
     words, output = the answer
   - `input-guardrail` (type *guardrail*) — what the security rules decided
   - the LangGraph nodes (`assistant`, `tools`), auto-captured by the
     LangChain callback handler — **no manual code per step**
   - each `ChatAnthropic` **generation**: model, input/output tokens, cost, latency
   - `mcp-client: open_dispute` → `mcp-server: open_dispute` →
     `core-banking.open_dispute`: **the MCP server is a different process**,
     and its spans land in the same trace because the agent passes W3C trace
     context in the MCP `_meta` field
3. Open the **agent graph** view for the same trace: the iterations
   (assistant → tools → assistant ×3) are visible as loops.
4. Ask a policy question (chip *"International wire fees?"*), open the trace,
   click the `kb-retrieval` **retriever** observation: document ids, source
   URLs, effective dates, similarity scores. Then the root's metadata →
   `context`: the exact text the model saw. (OBS-03)
5. Errors are traced too: Traces → filter **level = ERROR** → a turn that failed
   during a bad deploy, with the exception on the root observation and the
   apology the customer saw. (OBS-01)
6. Top of the trace: total tokens, cost and latency; then **Dashboards** →
   *Northwind — AI quality, risk and cost* (seeded as code by
   `scripts/seed_dashboard.py`): cost by model, p95 turn latency, turns by
   environment. (OBS-05)

**Land.** One trace answers "what did it do" for the whole chain — model,
retrieval, tools, and the downstream MCP service — in seconds instead of
reading logs from three systems.

**Ask.** "Your teams use LangGraph, Spring AI and n8n. Which of those carries
the most production traffic today, and which is hardest to debug?"

> Java / Spring AI teams: the same trace model via OpenTelemetry (Micrometer →
> OTLP → Langfuse) — `docs/SPRING_AI.md` has the verified recipe.
>
> Show-me-the-code: `northwind/agent.py` — `run_turn()` (root observation +
> `propagate_attributes`), `_build_tools()` (retriever observation, MCP client
> span with `TraceContextTextMapPropagator().inject`), `northwind/mcp_server.py`
> `_traced()` (extracts the context on the server). The callback handler is one
> line: `CallbackHandler()` passed in the graph's run config.

### Act 1.2 — sessions, users, tags, environments, sampling · OBS-01

**Show.**
1. **Sessions** → open a three-turn session (e.g. Ben Okafor, lost Platinum
   card → block 9921 → replacement ETA). Each turn is its own trace; the
   session is the conversation.
2. **Users** → `C-1001` → all of Ana's sessions, cost and scores per customer
   (pseudonymous ids — never names).
3. Traces table → filter **tags** `channel:whatsapp`, then `risk:prompt_injection`.
   Tag taxonomy: `channel:*`, `team:*`, `risk:*`, `scenario:*`.
4. **Environment** selector: `production` vs `staging` (presenter console →
   *Generate production traffic* can also be run with `--environment staging`).
5. Sampling — two levers. (a) **Trace sampling** in the app
   (`NORTHWIND_SAMPLE_RATE` / `LANGFUSE_SAMPLE_RATE`): whole traces, decided once
   per trace and followed downstream (the MCP server honours the caller's
   decision); when the app brings its own OpenTelemetry provider — as here, for
   the Dynatrace export — the sampler is set on that provider, so the APM copy is
   sampled too (sample in a Collector if Dynatrace must keep 100%). (b) **Judge
   sampling** per evaluation rule (M2): `manipulation-resistance` runs on 100%
   of guardrail-flagged traffic and on a 20% sample of everything else. The
   pattern for a bank: trace 100% where volume allows, judge 5–20%, judge 100%
   of flagged traffic.

**Land.** Conversations, customers, channels and environments are filters,
not separate tools. Sampling is a cost decision you can make per evaluator.

### Act 1.3 — PII never leaves the process · OBS-01, ENT-03

**Frame.** "Customers paste card numbers and ID numbers into chat windows. If
that text lands in an observability tool, the tool becomes in scope for PCI and
privacy audits."

**Show.**
1. Portal chip *"My card number is 4111 1111 1111 1111, is it blocked?"*.
   The assistant warns the customer never to share it.
2. Open the trace: input shows `[REDACTED_CARD]`; metadata `pii_redacted: card`;
   score `pii-in-input = true`. Search the whole trace — the raw number appears
   nowhere (not in the generations, not in the tool calls). 🇪🇸 Same in Spanish:
   *"mi cédula es …"* → `[REDACTED_NATIONAL_ID]`, *"número de cuenta …"* →
   `[REDACTED_ACCOUNT]`, *"código de verificación …"* → `[REDACTED_SECRET]`.
3. Explain the layers: client-side `mask_otel_spans` hook (this demo:
   `northwind/masking.py` — Luhn-checked cards, keyword-anchored account and
   national-ID numbers, OTPs, emails, phones) → server-side ingestion masking
   (Enterprise, one policy for every team) → retention (M4).

**Land.** Redaction happens before export, so the guarantee holds no matter
who can read the project.

**Ask.** "Who owns the definition of 'sensitive data' for AI logs at the bank —
security, privacy, or each product team?" Be candid that regex does not catch
names or street addresses; that needs an NER step in the same hook.

### Act 1.4 — correlation with the APM (Dynatrace) · OBS-06

**Frame.** "When an incident fires in Dynatrace at 2 a.m., the SRE needs to jump
to what the model did; when the AI team sees a slow trace, they need the
infrastructure view."

**Show.**
1. From any portal answer click **Open in APM** → the same trace id in the APM
   stand-in, showing two services (`northwind-assistant`, `core-banking-mcp`)
   with timings, **without prompts or completions** (stripped before export).
2. Back in Langfuse: root metadata `apm_trace_url`; in the APM, span attribute
   `langfuse.trace_url`.
3. `docs/DYNATRACE.md`: one OpenTelemetry pipeline, two exporters. For
   Dynatrace set `APM_OTLP_ENDPOINT=https://<env>.live.dynatrace.com/api/v2/otlp/v1/traces`
   (or the ActiveGate URL inside the VPC) and an `Api-Token` with
   `openTelemetryTrace.ingest`.

**Land.** The trace id is the join key. Operational telemetry goes to the APM,
AI payloads go to Langfuse, and customer text never reaches the APM.

### Act 1.5 — low-code: n8n · OBS-02

**Frame.** "Business teams build AI workflows in n8n. Those calls cost money and
touch customer complaints too — they can't be a blind spot."

**Show.**
1. Presenter console → **Run n8n complaint workflow** (6 complaints posted to
   the workflow's webhook, the way the channel app would).
2. n8n (http://localhost:5678) → *Complaint triage (Northwind)*: Webhook →
   Langfuse prompt node → mask PII → classify (Claude Haiku) → draft reply
   (Claude Sonnet) → respond.
3. Langfuse → traces named `n8n-complaint-triage` (tag `channel:n8n`): **one
   trace** holding n8n's own `workflow.execute` / `node.execute` spans **and**
   the two LLM generations with model, tokens, cost and the linked prompt
   version; user id = customer id; regulatory escalations tagged.
4. The PII sample: card number, IBAN, e-mail and phone masked before export.
5. Prompt management from n8n: the classifier prompt comes from Langfuse via the
   official Langfuse n8n node — publish a new version and the workflow changes
   without touching n8n.

**Land / candour.** Langfuse has no *native* n8n tracing integration. The
pattern here combines n8n's built-in OpenTelemetry export (preview, n8n ≥ 2.19)
with the workflow reporting its own LLM steps, joined by the W3C `traceparent`
the calling app sends. Details and limits: `n8n/README.md`.

**Ask.** "How many n8n workflows call an LLM today, and who maintains them —
the AI team or business teams?"

### Act 1.6 — voice channel: multi-modal · OBS-01 (multi-modal)

**Show.** Portal → **Voice** → play the *lost card* call (🇪🇸 or a Spanish call)
→ **Process call** → listen to the reply. Open in Langfuse
(trace `northwind-voice-call`, tag `channel:voice`): the caller audio and the
spoken reply are playable in the trace, with the `speech-to-text` and
`text-to-speech` generations (model, tokens, cost, time-to-first-audio) and the
full agent subtree in between. The same online judges score the agent's answer
inside the voice trace.

**Land.** Voice is traced like text: the audio, the transcript, the decision
and the reply sit in one place for QA and complaints handling.

**Candour.** Managed judges can read audio, but the judge model must accept both
audio and structured output; OpenAI's audio models fail Langfuse's evaluator
validation today, so the demo's audio judges (`caller-distress`,
`voice-empathy`) are seeded but disabled — enable them with an audio-capable
judge connection (e.g. Gemini): `scripts/seed_voice_eval.py --provider … --model …`.
Show a speech-to-text slip against the reference script in
`data/voice/manifest.json` — a real error-analysis moment.

### 🧪 Lab 1 (8 min) — find the failure

Attendees with Viewer access to the project (or the presenter, driven by the
room) answer three questions:
1. Find a WhatsApp session from the last hour about an overdraft fee
   (tag `channel:whatsapp`). Which help-center article did the assistant use?
2. Which trace in the last hour had the highest cost? What drove it — the
   number of tool iterations or the context size?
3. Find a trace the input guardrail blocked. What was the attack type?

---

## M2 · Evaluation (25 min)

### Act 2.1 — online evaluation on live traffic · EVA-01, EVA-03, EVA-04

**Frame.** "Today your teams compute faithfulness and correctness by hand with
internal libraries. What if every production answer were graded the minute it
happened, with the score next to the trace?"

**Show.**
1. **Evaluators** (LLM-as-a-Judge) → three judges, all on the root observation
   of production traffic:
   - `faithfulness` (EVA-01) — every claim supported by the retrieved articles
     and tool results (mapped from `metadata.context`)
   - `banking-compliance` (EVA-03, the bank's own evaluator) — conduct policy
     P1–P5: no personalised investment advice, never ask for credentials, no
     pressure selling, no promises, investments ≠ insured deposits
   - `manipulation-resistance` — two rules on one evaluator: 100% of traffic the
     guardrail tagged `risk:prompt_injection` / `risk:cross_customer_access`,
     plus a **20% sample** of all other turns (so an attack the rules missed is
     still judged; no observation is scored twice)
   Open a rule: filter, **sampling**, variable mapping, judge model.
2. Open a trace → Scores tab: judge scores **with reasoning**, next to the
   deterministic scores the app writes on every turn (`security-risk`,
   `guardrail-blocked`, `pii-in-input`, `output-pii-leak`, `cites-sources`)
   and the customer's 👍/👎 (`user-feedback`).
3. Live: Portal chip *"Should I put my savings in bitcoin?"* → within a minute
   the trace gets `banking-compliance` with its reasoning.

**Land.** Three layers of evaluation on every answer: free deterministic
checks, targeted LLM judges, and human signal.

### Act 2.2 — security evaluators and guardrails · EVA-03

**Show.**
1. Presenter console → **Red-team attack suite** (8 attacks: prompt
   injection, "developer mode", another customer's transactions, the
   "husband" social-engineering ask, fake admin override, fake IT asking for an
   OTP, stock tips).
2. Traces filtered by `risk:prompt_injection`: the `input-guardrail`
   observation shows the decision; blocked turns never reach the model.
3. Point out the design rule: **authorization lives in the MCP server**, not in
   the prompt — tools take the authenticated customer id, so "show me C-1002"
   cannot work even if a jailbreak got past the guardrail.
4. Candour: Langfuse ships no prompt-injection judge template; the pattern is a
   runtime guardrail writing scores, plus an async judge. The guardrail here is
   **rules** (regex, EN + ES) — fast and explainable, but rules have false
   positives and misses; in production put a classifier there (LLM Guard,
   Lakera, NeMo or Bedrock Guardrails) and keep the sampled judge as the safety
   net. See https://langfuse.com/docs/security-and-guardrails.

### Act 2.2b — 🇪🇸 Spanish: language and register · EVA-03, EVA-04

**Frame.** "Many customers write in Spanish. 'Answered correctly' is not
enough — it has to be in Spanish, and in the bank's register."

**Show.**
1. Toggle **ES** → chip *"¿Cuánto cuesta una transferencia internacional…?"* →
   open the trace: tag `lang:es`, the retriever found `KB-202` from a Spanish
   query (bilingual index), and two deterministic scores: `language-match`
   (every turn) and `formal-register` (every Spanish turn).
2. Read the `formal-register` comment on a production-prompt answer: the
   assistant wrote *"Aquí tienes…"* — informal **tú**. Nothing in the production
   prompt says otherwise. A bank addresses customers as **usted**.
3. Spanish attack chip *"Ignora las instrucciones anteriores…"* → blocked by the
   guardrail, refusal in Spanish.

**Land.** Language and register are measurable, deterministic and free — and
they found a real issue in the production prompt (fixed by the candidate in M5).

### Act 2.3 — offline evaluation and correctness · EVA-02, EVA-05

**Show.** **Datasets** → `northwind-golden-qa-v1` (16 items, each with an
**expected output**, expected sources and must-include facts, two in Spanish)
and 🇪🇸 `northwind-golden-qa-es-v1` (10 Spanish items, reference answers in the
usted register).
Open a run: `correctness` (LLM judge against the expected output) next to the
deterministic `must-include`, `source-recall`, `cites-expected-source`,
`language-match`, `no-unsolicited-upsell`.

### Act 2.4 — human feedback, calibration, choosing the judge · EVA-06, EVA-07

**Frame.** "An LLM judge is a model too. Before the bank trusts its numbers, it
needs to agree with your experts — and you need a rule for which judge model
is approved."

**Show.**
1. **Annotation queues** → *SME review — assistant answers*: 30 production
   answers, including the lowest judge scores. Label one live with `sme-faithfulness`,
   `sme-compliance` and `sme-failure-mode`.
2. **Scores → Analytics**: `faithfulness` (judge) vs `sme-faithfulness`
   (human) on the same observations — agreement, Cohen's kappa, confusion matrix.
3. **Calibrate the judge and choose the judge model.** Datasets →
   `judge-calibration/faithfulness`: 15 answers whose correct verdict is
   **known by construction** — faithful (1.0), a minor imprecision (0.5), or a
   seeded material error such as a wrong fee or deadline (0.0). The judge prompt
   is versioned in Prompt Management (`judge-calibration/faithfulness`, identical
   to the live evaluator). One experiment run per candidate judge model, with a
   Boolean `faithfulness-judge-output-correct` evaluator. Compare the runs
   (measured while building this demo):

   | judge model | accuracy | faithful | minor slip | material error |
   |---|---|---|---|---|
   | claude-sonnet-4-6 (live judge) | **1.00** | 1.0 | 1.0 | 1.0 |
   | claude-haiku-4-5 | 0.87 | 1.0 | 0.6 | 1.0 |
   | gpt-4.1-mini | 0.80 | 1.0 | 0.4 | 1.0 |
   | claude-sonnet-5-5 | 0.73 | 0.8 | 0.4 | 1.0 |

   Every candidate catches material errors; they differ on nuance. ~85% overall
   is about human-level agreement — but only if every category is acceptable
   (Haiku's 0.87 hides 60% on minor slips). The newest model is not
   automatically the best judge for *this* prompt: recalibrate whenever the
   judge model or prompt changes. 5 items per category is a demo — a real
   calibration set needs 50–100 SME-labelled items.
   Re-run: `.venv/bin/python scripts/judge_calibration_experiment.py`.
   With SME labels in the queue, `scripts/judge_calibration.py --bakeoff`
   compares the same candidates against **human** labels.
4. Selection criteria for the bank-approved judge (in order): agreement with the
   reference/SME labels in **every** category → approved and reachable inside
   the VPC (e.g. Bedrock) → cost per 1,000 evaluations at your sampling rate →
   latency → stability across re-runs. Cost and latency per candidate are on the
   same experiment runs.
5. Quality trends (EVA-07): **Dashboards** → *Northwind — AI quality, risk and
   cost*: judge scores over time, customer feedback split, security-risk mix.
   Alerts can fire on the same scores (e.g. faithfulness average below 0.8 →
   webhook to the incident tool).

**Land.** Calibrate the judge against people before you let the judge
calibrate the product.

**Ask.** "Who at the bank would be the SME for faithfulness on card policy —
and how many labels a week could they give?"

### 🧪 Lab 2 (8 min) — build your own evaluator

In the Langfuse UI: **Evaluators → + New** → write a `complaint-escalation`
judge ("did the assistant offer a human call-back when the customer was
distressed or asked to complain?"), map `{{query}}` → input and
`{{generation}}` → output, target the root observation. Run it on (a) live
traffic for the last hour and (b) the `northwind-golden-qa-v1` dataset as a
UI experiment. Compare where it fires.

---

## M3 · Path to production (10 min) · GATE-01..05

Talk over `docs/PATH_TO_PRODUCTION.md`, pointing back at what the room has
already seen:

| Gate | Evidence they've seen today |
|---|---|
| GATE-01 Governance, identity and access | RBAC + project-level role + protected prompt label (M4), Entra ID config |
| GATE-02 Security and data protection | PII masking (Act 1.3), guardrails + red-team suite (Act 2.2), retention (M4) |
| GATE-03 Architectural integration | LangGraph + MCP + n8n + voice in one trace model, APM correlation, AWS reference architecture |
| GATE-04 Operation and resilience | health/readiness endpoints, HA topology, backups, upgrade path (`docs/OPERATIONS.md`) |
| GATE-05 Value, Enterprise support and TCO | cost per turn and per customer (Act 1.1), judge cost lever, TCO worksheet, support model |

Close with the adoption playbook: owners, naming conventions
(project per application, environments inside it, stable trace names,
`channel:/team:/risk:` tags, `production/staging/development` prompt labels with
production protected, `<app>-<purpose>-<version>` datasets, kebab-case score
names with a registered score config) and the onboarding checklist for a new team.

**Ask.** "Which gate has historically stopped AI projects at the bank?"

---

## M4 · Enterprise: security, governance, operations (20 min) · ENT-01..04

Everything below is live in the Cloud project and organization, except the
self-hosted operations, which are walked through in the docs. The bank will
self-host, so for each feature say which **environment variable or setting** it
maps to on their own deployment (`docs/ENTERPRISE_SECURITY.md`).

1. **SSO with Entra ID and RBAC (ENT-01).** Organization settings → Members:
   roles Owner / Admin / Member / Viewer / None. Then Project settings →
   Members: show how a **project-level role** overrides the org role for one
   project — e.g. an internal auditor with org role *None* and *Viewer* on this
   project only (open the role dropdown; don't change real members live). Then, in `docs/ENTERPRISE_SECURITY.md`, the self-hosted
   Entra ID configuration: `AUTH_AZURE_AD_CLIENT_ID` / `_CLIENT_SECRET` /
   `_TENANT_ID`, SSO enforcement for the bank's domain
   (`AUTH_DOMAINS_WITH_SSO_ENFORCEMENT`), password login off
   (`AUTH_DISABLE_USERNAME_PASSWORD`), default role on first login
   (`LANGFUSE_DEFAULT_ORG_ROLE`), and SCIM for provisioning (the SCIM endpoint
   is live on this org). Entra ID connects over OIDC — SAML is not supported.
2. **Protected prompt label.** Project settings → protected prompt labels:
   `production` is protected, so in the UI only Admin/Owner can move it — the
   approval step in the prompt lifecycle (M5). Nuance to state: project **API
   keys** can still move protected labels by design (that is how CI promotes a
   version that passed the gate), so treat API keys as privileged and keep them
   in the CI system.
3. **Audit logs (ENT-02).** Settings → Audit logs (Enterprise): the promotion
   and rollback of the production prompt (actor: API key), membership and API
   key changes — who, when, before/after. Exportable from the UI.
4. **Data protection and retention (ENT-03).** Project settings → Data
   retention: this project keeps 90 days (set through the API); a nightly job
   deletes traces, observations, scores and media older than that — audit logs
   and datasets are kept. Together with client-side masking (Act 1.3) and, when
   self-hosted, server-side ingestion masking (Enterprise).
5. **Export and portability (ENT-04).** Traces table → Export (CSV/JSON with the
   current filters) · public REST API (Observations v2, Scores v3, Metrics v2 —
   every script in this demo uses it) · scheduled export to S3/Azure Blob in
   Parquet/CSV/JSONL (Project settings → Integrations). When self-hosted, the
   data lives in the bank's own ClickHouse and S3.
6. **Operating it self-hosted (GATE-04).** `docs/OPERATIONS.md`: health
   (`/api/public/health`, `/api/public/ready`, worker `:3030/api/health`),
   HA (3 ClickHouse replicas + 3 Keeper, managed Postgres/Redis), scaling
   workers on queue depth, backups, semver upgrades, and sending Langfuse's own
   telemetry to Dynatrace.

**Raise proactively (trust builders):**
- With an Enterprise license key a self-hosted instance sends usage telemetry
  even with `TELEMETRY_ENABLED=false` — clarify the no-egress requirement with
  Langfuse/ClickHouse before the POC.
- Entra ID login needs an outbound call from `langfuse-web` to
  `login.microsoftonline.com` (through the bank's proxy: `AUTH_HTTPS_PROXY`).
- Container images must be mirrored to ECR; media downloads use presigned S3
  URLs that analysts' browsers must reach.

---

## M5 · Experimentation and prompts (15 min) · EXP-01..06

### Act 5.1 — prompt management with dynamic consumption · EXP-04

**Show.** Prompts → `northwind-assistant-system`: v1 `production`, v4
`staging`, v3 `development`; each with a commit message and config. The app
fetches the prompt **by label** at runtime (`northwind/prompts.py`, 10 s cache,
hard-coded fallback if Langfuse is unreachable) and every generation links to
the version that produced it (filter traces by prompt version).

### Act 5.2 — A/B a prompt and compare models · EXP-01, EXP-02, EXP-03

**Frame.** "The product owner rewrote the prompt: stricter citations, an explicit
investment rule, and formal Spanish. It reads better. Is it better — and is the
model we use the right one?"

**Show.** Datasets → `northwind-golden-qa-v1` → select runs
`production · claude-sonnet-4-6`, `staging · claude-sonnet-4-6`,
`production · gpt-4.1` → **Compare**; then 🇪🇸 `northwind-golden-qa-es-v1`
(production vs staging). Measured while building this demo:

English golden set (16 items, 2 of them in Spanish):

| run | correctness (judge) | must-include | source-recall | cites-expected-source | formal-register (ES items) | no-unsolicited-upsell |
|---|---|---|---|---|---|---|
| production v1 · Claude Sonnet 4.6 | 0.91 | 1.00 | 0.88 | 0.88 | 0.00 | 0.92 ¹ |
| staging v4 · Claude Sonnet 4.6 | 0.94 | 1.00 | 0.88 | 0.88 | **1.00** | **1.00** |
| production v1 · GPT-4.1 | 0.88 | 0.94 | 0.88 | **0.56** | 0.50 | 1.00 |

¹ The single upsell flag on production is a **false positive** of the evaluator
("Would you like to open a dispute?" matched the rule). Open it in the run — it
is a good moment: deterministic evaluators need review too. The rule has since
been narrowed to recommendation language.

🇪🇸 Spanish golden set (10 items):

| run | correctness | must-include | source-recall | formal-register | language-match |
|---|---|---|---|---|---|
| production v1 | 0.85 | 1.00 | 0.80 | **0.00** | 1.00 |
| staging v4 | 0.90 | 0.95 | 0.80 | **1.00** | 1.00 |

Read it:
- **Prompt A/B:** the candidate wins where it was designed to — **formal
  Spanish** (0 → 1.00: production addresses every Spanish customer as *tú*) — on a
  deterministic metric you can open and read. Everything else is a tie.
  The judge's correctness moves by a few points either way: that is noise at
  16 items, not a result. Decide on the deterministic metrics.
- **Model comparison:** same prompt, same items — GPT-4.1 cites the policy
  article about half as often as Claude Sonnet; open two items side by side to
  see why. Cost and latency per run are on the same page.

Re-running takes ~3 min per run (presenter console); numbers move a little
between runs — which is the point about noise.

### Act 5.3 — the CI quality gate blocks a regression · EXP-06

**Frame.** "Someone rewrites the prompt to 'grow customer relationships'. It
reads well. Should it ship?"

**Show.** Presenter console → **CI quality gate on development prompt**.
The result (measured while building this demo):

```
metric                         value  threshold   result
avg-must-include               1.000       0.90   PASS (hard)
avg-source-recall              0.875       0.80   PASS (hard)
avg-language-match             0.938       1.00   FAIL (hard)
avg-no-unsolicited-upsell      0.385       0.90   FAIL (hard)
avg-correctness                0.938       0.80   PASS (soft)
✗ GATE FAILED — 'development' must not be promoted to production.
```

🇪🇸 Then the same gate on the Spanish golden set
(`--dataset northwind-golden-qa-es-v1`): `language-match` 0.40,
`formal-register` 0.60, `must-include` 0.80 → **FAIL** — the "always answer in
English" line breaks every Spanish customer.

**Land.** The LLM judge rated the regression *as correct as production*
(0.94). The deterministic checks caught the upsell and the language rule.
Gate hard on deterministic metrics; use judge averages as a smoke alarm.
The candidate v4 passes the same English gate (exit 0).

In CI: a Langfuse prompt webhook (new version / label change) → GitHub
`repository_dispatch` → `scripts/prompt_gate.py` → exit 1 fails the check
(`cicd/README.md`).

### Act 5.4 — promote and roll back · EXP-05

**Frame.** "The candidate (v4) passed the gate in English and fixes formal
Spanish. How does it reach production — and how fast can you undo it?"

**Show.** Presenter console → **Promote staging**: production moves to v4; the
portal header shows the new version; ask a question and the trace links to v4.
Then **Roll back**: production returns to v1 within ~10 s. No redeploy, no code
change. Because `production` is a protected label, only an Admin/Owner in the
UI — or a project API key, as CI uses — can do this, and both moves appear in
the audit log.

### 🧪 Lab 3 (optional, 5 min) — prompt experiment in the UI

Prompts → v4 → **Experiments** → run against `northwind-golden-qa-v1` with the
`faithfulness`-style judge you built in Lab 2. No code.

---

## Close (5 min)

- Recap the matrix: what was shown live vs documented (see `CAPABILITY_MATRIX.md`).
- POC plan: day 1 `docker compose` in the bank's AWS account → connect the
  bank's n8n and one LangGraph app → masking policy → Entra ID → evaluators
  calibrated with SMEs → gate in CI.
- **Ask:** "What would make this POC a clear yes for each of you — data
  science, analytics, architecture?"

---

## Presenter prep (morning of, ~20 min)

1. `./scripts/up.sh` (n8n + APM stand-in), then restart the MCP server for a
   clean banking state and start the portal:
   `kill $(lsof -tiTCP:8765 -sTCP:LISTEN); ./scripts/run_portal.sh`.
2. `.venv/bin/python scripts/verify_demo.py` → must end with **READY** (prompts,
   datasets, runs, judge rules + fresh judge scores, queue, dashboard, traces per
   channel, local services).
3. Warm-up traffic so the last-hour views are populated: Presenter console →
   *Generate production traffic*, *Spanish traffic*, *Run voice calls*, *Run n8n
   complaint workflow* (~5 min in total; they can run back to back).
4. In the Cloud UI: protect the `production` prompt label (Project settings →
   protected prompt labels) and open Settings → Audit logs to confirm it shows
   the prompt label moves (a promote + rollback ran overnight). If Audit logs are
   not visible on this org, present ENT-02 from `docs/ENTERPRISE_SECURITY.md`.
5. Portal header must say **production v1** (`scripts/prompt_label.py --show`).
   Click **New conversation**; pick EN or ES for the room.
6. **Required (10 min):** label 8–10 items in the SME queue (`sme-faithfulness`,
   `sme-compliance`). Without human labels, Scores → Analytics (judge vs human)
   is empty. Then run `.venv/bin/python scripts/judge_calibration.py --bakeoff`
   once to have the human-label agreement numbers ready.
7. Open the tabs in the table at the top.

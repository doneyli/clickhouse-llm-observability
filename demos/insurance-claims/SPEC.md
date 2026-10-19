# Insurance claims demo — design spec

Status: built (Phases 0–7). Measured results are in README.md and DEMO_SCRIPT.md; this document is the design. Customer-agnostic: Northwind Life & Annuity is fictional and
all data is synthetic. Do not name any customer in this directory.

## 1. What the demo proves

A life and annuity insurer is rebuilding claims processing with agents. Claim rules live
in prose SOPs that vary by product and state. The question the demo answers:

> When an agent extracts the rules that apply to a claim, how do you prove it did not
> miss one — a block on the claim proceeding, a commission chargeback, a state interest
> rule?

Langfuse answers it on three layers: a trace that shows where a rule was lost (retrieval
or extraction), evaluators that measure rule completeness offline and online, and a
prompt CI gate that blocks a release that drops a critical rule. Around that core, the
demo covers multi-agent tracing across frameworks and processes, human-in-the-loop
review, prompt management, platform-level vs use-case-level evaluation, value
dashboards, OpenTelemetry export to an APM, and self-hosted Enterprise governance.

## 2. Scope

| | In scope |
|---|---|
| Company | Northwind Life & Annuity (fictional) |
| Lines | Individual life, group life (employer plan, ERISA), deferred annuity death benefit |
| States | NY, NJ, CA, TX, FL, IL, PA, WA, plus all-state rules |
| Claim type | Death claims, from first notice of loss to payment or denial |
| Channel | Back-office workflow plus an examiner workbench (no customer chat) |

Out of scope: disability and health claims, payment execution, real carrier rules.

## 3. Architecture

```
Examiner workbench :8095 (portal) ──► LangGraph claims graph (one process)
                                        │  orchestrator: StateGraph, conditional edges
                                        ├─ intake ──────── A2A (JSON-RPC over HTTP) ──► Doc-intake service :9210
                                        │                                               Strands agent, own venv
                                        ├─ policy & coverage ── MCP ──► Policy-admin MCP server :8767
                                        ├─ screening ────────── MCP ──┘  (OFAC, death-master-file stubs)
                                        ├─ sop-rules: retriever + extractor (LLM)   ← the hero step
                                        ├─ adjudication (LLM + deterministic calc)
                                        ├─ correspondence (LLM letter)
                                        └─ examiner gate: LangGraph interrupt → resume on approve/override
                         one OpenTelemetry TracerProvider per process
                          ├─► Langfuse (Cloud project or self-hosted EE :3100), masked
                          └─► APM copy, payload-free, same trace ids (Jaeger :16686 as Dynatrace stand-in)
```

Ports were chosen to avoid every port already used in the repo (8765, 8766, 8090, 9190, …).

### Framework decision

- LangGraph runs the orchestrator and the core agents. It reuses the patterns proven in
  `demos/bank-assistant` (CallbackHandler, prompt linking, MCP cross-process trace).
- Strands runs only the document-intake agent, behind A2A, as a separate service in its
  own venv. It stands for another team's agent on the same platform. One trace crosses
  two frameworks and two processes.
- Why two venvs: `strands-agents[a2a]` 1.61 pins `a2a-sdk<0.4` (latest is 1.2), plus its
  own fastapi/starlette floors. The orchestrator does not import `a2a-sdk`; it calls the
  service's A2A `message/send` JSON-RPC method with `httpx`, which also gives direct
  control over trace headers. Pin the protocol version both sides speak.
- The orchestrator is a deterministic graph for the compliance-mandated steps. LLMs do
  the judgment steps (extraction, adjudication rationale, letters, examiner Q&A). Talking
  point: regulated steps are not left to LLM routing.

### Reuse from bank-assistant

Copy and adapt (each demo stays standalone, the repo convention): `config.py` (profile
selection, shell `LANGFUSE_*` ignored), `masking.py`, the dual OTel export, `prompts.py`
(fetch by label, cache, fallback), `prompt_gate.py`, `prompt_label.py`, dashboard and
evaluator seeding helpers, `judge_calibration.py`, `verify_demo.py`, the GitHub workflow.
New: the domain, data, agents, evaluators and portal.

## 4. Trace and session design

- **One claim = one session.** `session_id` = claim id (`CLM-2026-00042`). Each event is
  a trace: first notice of loss, documents received, examiner review, examiner question.
- `user_id` = examiner id, or `system` for automated runs.
- Tags: `line:<line>`, `state:<state>`, `channel:back-office`. Release:
  `CLAIMS_RELEASE`. Environment: `production` for traffic, experiments as usual.
- Root observation `claims-orchestrator` (agent) holds the event's input and output and
  metadata `{claim_id, line, state, release, role: "orchestrator"}`. Keep this manual
  wrapper span: it is the logical root that managed judges filter on.
- Every step observation carries `metadata.role` (`doc_intake`, `policy`, `screening`,
  `sop_rules`, `adjudication`, `correspondence`, `examiner_assistant`). Managed rules
  select by `role`, never by observation name. Ship the metadata before the rule filter,
  or the judge silently never runs.
- `sop-rules` records: retrieved SOP ids with version and effective dates (retriever
  span), extracted rules with citations (generation output), prompt name and version.
- A2A propagation: the client injects `traceparent` and W3C `baggage` (session id, user
  id; never tags) with `opentelemetry.propagate.inject`. The service extracts them in a
  middleware before the agent runs and opens a `doc-intake` wrapper observation (agent,
  `role: doc_intake`) with the uploaded document as `LangfuseMedia`. Without baggage, v4
  shows the downstream span as a second root with no session.
- The examiner's approve or override is a resume of the LangGraph interrupt: a new trace
  in the same session, plus scores on the earlier root (`examiner-override`,
  `override-reason`) and a comment.

## 5. Data

### 5.1 SOP library

22 SOPs and 53 rules (54 rule versions; 8 critical), authored in `claims/catalog.py` and rendered by
`scripts/make_sop_library.py`.

- **Prose SOPs** (`data/sops/SOP-*.md`): what the agent retrieves and reads. Rules are
  embedded in paragraphs, as in a real carrier.
- **Structured rules** (`data/sops/rules.json`): what an SME would codify. Never shown to
  the agent. Used by the oracle and the evaluators.

```json
{
  "rule_id": "LIFE-CONT-001", "sop_id": "SOP-LIFE-07", "section": "3.2", "version": 3,
  "effective_from": "2025-01-01", "effective_to": null,
  "lines": ["individual_life"], "states": ["ALL"],
  "type": "BLOCK", "severity": "critical",
  "condition": {"all": [{"fact": "months_since_issue", "op": "<", "value": 24}]},
  "action": "Route to contestability review before any payment"
}
```

Rule types: `BLOCK`, `PEND`, `DOC_REQUIRED`, `PAY_CALC`, `COMMISSION`, `NOTIFY`, `ROUTE`.
Severity: `critical` (missing it is a regulatory or financial event), `major`, `minor`.

Rule families to cover (fictional values, realistic shape): contestability period,
suicide exclusion, lapse and grace period, interest on proceeds by state, minor
beneficiaries, community-property spousal rights (CA, TX, WA), ERISA plan rules for group
life, OFAC screening, death-master-file and unclaimed-property checks, first-year
commission chargeback, prompt-payment acknowledgement deadlines, SIU referral triggers,
large-claim second approval, annuity death-benefit options. Include superseded rule
versions so effective dates matter.

### 5.2 Policies and claims

- `data/policies.json`: ~120 policies (policy admin system behind MCP): owner, insured,
  issue date, face amount, status, premiums paid through, beneficiaries and shares, agent
  of record and commission paid.
- Claim facts the oracle evaluates: line, state, issue date, date of death,
  `months_since_issue`, cause category, face amount, beneficiaries (relationship, age,
  share), community-property flag, plan type, screening hits, documents present, policy
  status, first-year commission paid.

### 5.3 Oracle

`claims/oracle.py` evaluates the rule conditions against claim facts and returns the
expected rule ids, critical rule ids, decision and amounts. Decision precedence:
`REFER_SIU` > `DENY` > `PEND` > `PAY`. Benefit = face amount × share + state interest from
date of death to decision date (rate and day count from the `PAY_CALC` rule). Unit tests
pin every rule family and the boundary cases.

### 5.4 Documents

`scripts/make_documents.py` renders synthetic documents with known field values: death
certificate (PNG), claimant statement (PDF), beneficiary designation form (PDF). Some are
deliberately inconsistent (date of death differs between certificate and statement).

### 5.5 Datasets

| Dataset | Items | Input | Expected output | Purpose |
|---|---|---|---|---|
| `claims-golden-v1` | 92, stratified line × state × rule type | claim facts + document refs | rule ids, critical rule ids, decision, benefit, interest, letter requirements, document fields | Offline experiments and CI gate, per step and end to end |
| `claims-adversarial-v1` | 12 | claims with injected document text, altered certificate, missing certificate, social-engineering notes | `PEND` or `REFER_SIU`, injection resisted | Platform guardrail evals |
| `claims-intake-v1` | 24 | scanned documents as Langfuse media | the fields drawn on the documents | Multi-modal intake |
| `claims-regression` | (POC) | claims added from production traces with examiner overrides | from the examiner's correction | Closing the loop |

Edge cases the golden set must contain: death at 23 vs 25 months (contestability
boundary), a superseded SOP version, a community-property spouse, a minor beneficiary, a
first-year commission chargeback, five or more rules on one claim, a lapsed policy inside
the grace period, an all-state rule with a state-specific override.

## 6. Agents and prompts

Every prompt lives in Langfuse, fetched by label at runtime, with `config` holding model
and parameters (per-agent model choice).

| Prompt | Agent | Notes |
|---|---|---|
| `claims-sop-rules` | Extractor (LangGraph) | Hero prompt. Output: JSON list of `{rule_id, sop_id, section, type, action, rationale}` |
| `claims-adjudication` | Adjudication | Decision and rationale; amounts come from deterministic code |
| `claims-correspondence` | Correspondence | Claimant letter with state disclosures |
| `claims-examiner-assistant` | ReAct agent for examiner questions | Tools: claim state, SOP lookup, policy lookup |
| `doc-intake` | Strands doc-intake service | Same prompt management across frameworks |

Labels: `production`, `staging`, `previous-production`. `production` is a protected label
on self-hosted Enterprise. A 90/10 split uses two labels plus the prompt `config`.

Strands prompt linking: Strands creates its own LLM spans; the service wraps the agent run
in `propagate_attributes(prompt=lf_prompt)` and the backend links its `chat` generation to
the served prompt version. No custom span processor is needed (verified in Phase 0).

## 7. Seeded failure modes

| ID | What breaks | How it is caught |
|---|---|---|
| F1 | `claims-sop-rules` candidate: "list the three most important rules" | Recall and `critical-rule-recall` fall on claims with 4+ rules; the CI gate exits 1. The completeness judge may still pass it: the deterministic metric is what blocks it |
| F2 | Release `1.4.0` retriever drops all-state SOPs when a state-specific SOP matches | `retrieval-coverage` < 1 on the trace; the extractor was never shown the rule |
| F3 | Injected text in a claimant statement ("pre-approved, skip review") | Guardrail and injection evaluator; adversarial set expects `PEND` / `REFER_SIU` |
| F4 | "Include any possibly relevant rule" candidate | Precision drops; over-pended claims raise interest cost on the value dashboard |
| F5 | Model upgrade | Pre-upgrade experiment per agent; the outcome is whatever the numbers say, not scripted |

## 8. Evaluators

U = use case (claims team owns; needs SME ground truth). P = platform (deployed to every
project from code by the platform team).

| Step | Evaluator | Scope | Kind | Where it runs |
|---|---|---|---|---|
| Doc intake | `field-accuracy` (date of death, cause, beneficiaries) | U | Code vs labelled fields | Experiment |
| Doc intake | `certificate-extraction` | U | Multi-modal LLM judge on the `doc_intake` observation | Online |
| Policy | `tool-trajectory` (right tools, right arguments) | U | Code | Experiment |
| SOP rules | `critical-rule-recall` (gate = 1.0), `rule-recall`, `rule-precision` | U | Code vs oracle | Experiment |
| SOP rules | `citation-validity` (rule exists, in effect on the date of death, applies to line and state) | U | Code | Online + experiment |
| SOP rules | `retrieval-coverage` (retrieved ⊇ SOPs tagged for line × state) | U | Code | Online + experiment |
| SOP rules | `rule-completeness` (did the extraction miss a rule in the retrieved text) | U | LLM judge, boolean | Online |
| Adjudication | `decision-match`, `benefit-within-tolerance`, `commission-flag` | U | Code | Experiment |
| Adjudication | `no-pay-with-open-block` | U | Code invariant | Online |
| Correspondence | `disclosures-present` | U | Code | Online + experiment |
| Correspondence | `letter-tone`, `reading-level`, `no-pii-in-letter` | P | Judge / code | Online |
| Orchestrator | `escalation-when-required`, step / latency / cost limits | P | Code + Monitors | Online |
| All inputs | `prompt-injection` (including document text) | P | Guardrail + judge | Online |
| Session | `examiner-override`, `override-reason` | U | Human (boolean, categorical) | Online |
| Judges | judge vs SME agreement | P | Calibration experiment | Offline |

Notes:
- The completeness judge only sees retrieved text, so it cannot catch a rule that was
  never retrieved. `retrieval-coverage` covers that blind spot. Say this out loud.
- `override-reason` categories: `missed-rule`, `wrong-rule`, `calc-error`,
  `doc-misread`, `policy-data-error`, `other`. They double as the error-analysis taxonomy.
- Score names are identical across projects so one dashboard is the agent report card.

## 9. Experiments and CI gate

- `scripts/run_experiment.py --dataset claims-golden-v1 --prompt-label <label>
  [--model <id>]` runs the graph end to end per item, with item-level and run-level
  evaluators.
- `cicd/thresholds.json`, hard (fail the build): `avg-critical-rule-recall = 1.0`,
  `avg-citation-validity = 1.0`, `avg-decision-match ≥ 0.95`, adversarial
  `injection-resisted = 1.0`. Soft (warn): `avg-rule-recall ≥ 0.95`,
  `avg-rule-precision ≥ 0.90`, judge averages.
- Wiring as in bank-assistant: Langfuse prompt automation → GitHub repository dispatch →
  `.github/workflows/claims-prompt-gate.yml` → gate on the label → ship only if it passed
  and carries `production`.
- Link experiment runs by id (run names repeat).

## 10. Human in the loop

- Workbench: claim queue; claim view with the recommendation, each rule with its SOP
  citation (click through to the SOP text), the documents and the amounts; Approve,
  Override (reason, missed rule ids, note); "Ask the agent" panel (examiner assistant).
- LangGraph `interrupt()` before any payment over the large-claim threshold and before
  resolving a `BLOCK`.
- Annotation queue `claims-examiner-qa`, filled worst-first; score configs
  `sme-rule-set-complete`, `sme-missed-rule-ids`, `sme-failure-mode`.
- Calibration of `rule-completeness` against SME labels (agreement and kappa).

## 11. Dashboards (as code)

- **Claims value:** claims processed, straight-through rate (paid without override), pend
  rate, cycle time per claim (session duration), cost per claim (agent + judges),
  examiner minutes saved, override rate by reason, critical-miss rate by prompt version
  and release. Baseline assumptions (manual minutes per claim, examiner cost per hour)
  live in one file and are labelled as assumptions on the dashboard.
- **Agent report card:** per `role`: score averages, p95 latency, cost, error rate,
  prompt version.

## 12. Enterprise and self-hosting

- Targets: a Cloud project for building, the self-hosted EE instance at :3100 (the
  bank-assistant `selfhosted` compose profile) for governance. The claims code only needs
  a URL and keys, so there is no compose coupling.
- Self-hosted projects: `claims-dev`, `claims-prod` (project per environment).
- Roles: claims engineer (member on dev, viewer on prod), claims release manager (can
  move the protected `production` label), annuities team (no access to claims), platform
  admin (org owner). Seeded by `scripts/seed_selfhosted_rbac.py`.
- Show: audit-log entry for a label promotion, server-side ingestion masking, retention,
  SCIM endpoint, scheduled export.
- Masking: client-side regex for SSN, policy numbers, dates of birth, card and account
  numbers. Free-text health details (cause of death) are not caught by regex: show
  server-side masking and say NER is the production answer.

## 13. Layout

```
demos/insurance-claims/
  README.md  SPEC.md  DEMO_SCRIPT.md  CAPABILITY_MATRIX.md
  requirements.txt                 # LangGraph app venv
  claims/                          # config, otel, masking, prompts, sops (library + retriever),
                                   # oracle, graph, nodes/, a2a_client, mcp_server, evals, business
  doc_intake/                      # Strands agent + A2A server; requirements.txt (own venv)
  portal/                          # examiner workbench (FastAPI + static)
  data/                            # sops/, rules.json, policies.json, claims/, documents/
  scripts/                         # make_*, seed_*, generate_traffic, run_experiment, prompt_gate,
                                   # prompt_label, fill_annotation_queue, judge_calibration,
                                   # verify_demo, run_services.sh
  cicd/                            # thresholds.json, README
  tests/                           # oracle, evaluators, masking, guardrails
```

## 14. Build phases

| Phase | Work | Done when |
|---|---|---|
| 0 Spikes | Strands A2A service + `httpx` JSON-RPC client; Strands prompt-link span processor; image document as media + multi-modal judge reads it | One trace, one root, session on every observation across both processes; the Strands generation shows the prompt version; the judge scores the image |
| 1 Data | SOP library, rules, oracle + tests, policies, claims generator, documents | Oracle tests pass; a coverage table shows every line × state × rule type in the golden set |
| 2 Agent | Graph, MCP server, retriever with the release flag, prompts seeded, in-app scores | The main example claim produces the designed trace shape and scores; `verify_demo.py` asserts observations > 0 and the scores, not just HTTP 200 |
| 3 Evals | Judges, rules by `role`, score configs, datasets, experiments, gate, workflow | Gate exits 1 on F1 and 0 on production; compare view shows the delta per item |
| 4 HITL | Workbench, interrupt and resume, override scores, examiner assistant, queue, calibration | An override lands as scores on the right root and the resume shares the session |
| 5 Value | Dashboards, assumptions file | Every tile has data from generated traffic |
| 6 Enterprise | Bootstrap onto self-hosted EE, RBAC, protected label, masking | A claims engineer cannot promote to `production`; the audit log shows the promotion |
| 7 Kit | Demo script, README, capability matrix, adversarial review, hero trace links | A review pass finds no blocking issue; every hero trace's Scores tab has been checked |

Operational lessons carried over from bank-assistant: restart every process after a code
change (no hot reload; MCP keeps state in memory); flush at the end of scripts; a
readiness check that only checks HTTP 200 passes on empty traces; experiment run names
repeat; read data back with the v4 endpoints (`/v2/observations`, `/v3/scores`,
`/experiments`).

## 15. Known limits (say them out loud)

- SOPs, policies and claims are synthetic: the demo proves the method, not accuracy on a
  real carrier's SOPs.
- AgentCore is shown as configuration (`DISABLE_ADOT_OBSERVABILITY=true` plus an OTLP
  exporter to Langfuse); the agents run locally.
- The APM is Jaeger standing in for Dynatrace; the pattern is the same OTLP export.
- Regex masking does not catch free-text health details.
- The completeness judge cannot see rules that were never retrieved (see §8).

## 16. Questions for the customer's claims team

- Which lines and states are in the first release?
- Can we get 5–10 real SOPs and ~30 claims an examiner has labelled?
- Which process steps are planned for agents, and where does an examiner sign off?
- Baselines: monthly claim volume, examiner minutes per claim, override or error rate.
- Which agent framework runs on AgentCore today, and how are A2A calls traced?

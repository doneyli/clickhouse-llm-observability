# Demo script — "The rule nobody applied"

About 50 minutes of demo inside a 60-minute meeting. Every act opens with the question the audience is
already asking, shows it live, and ends on the point to land and a question back to the room.
Run `scripts/verify_demo.py --links` before the meeting: it checks the state and prints fresh links for
every beat below.

**Before you start:** `./scripts/run_services.sh` (MCP, A2A, workbench), Jaeger up, workbench open on
`CLM-2026-00042`, Langfuse open on the project (Cloud) and on `http://localhost:3200` (self-hosted
Enterprise) in a second tab. Presenter view off in the workbench until Act 3.

---

## 0 · The question (2 min)

> "Your claims team asked a precise question: when an agent extracts the rules for a product and state
> from hundreds of SOPs, how do you prove it didn't miss one — a block on the claim, a commission
> chargeback? Everything today hangs off that question."

One slide or none. Name the fictional carrier, the three lines (individual life, group life, annuity death
benefits) and eight states, and that every SOP and claim is synthetic.

## 1 · One claim, every interaction, one pane (10 min)

**Workbench → `CLM-2026-00042` → Process claim.** A New York policy issued 11 months before death, a minor
co-beneficiary. While it runs, name the steps: document intake (a Strands agent behind A2A, another
team's service), the policy agent calling MCP tools, sanctions screening, the SOP rules agent, the
critical-rule guard, adjudication.

Result: **PEND**, six rules — contestability review (critical), first-year commission chargeback, the
minor's share, New York interest, timelines and the DFS notice. Click a rule id → the SOP paragraph.

**"Trace in Langfuse ↗":**
- One root `claims-orchestrator`, LangGraph nodes, and *inside* it the MCP client and server spans and
  the A2A call with the Strands agent's own spans — three processes, two frameworks, one trace.
- `doc-intake`: the scanned certificate rendered in the trace (multi-modal); `certificate-extraction`
  is a judge that reads the image.
- `sop-retrieval`: which SOPs, which versions. `sop-rules`: the claim file and the SOP text the model saw,
  the rules it returned; the generation is linked to prompt `claims-sop-rules` v1.
- Cost per step and per claim (~$0.07).

**"Same trace in APM ↗"** → the same trace id in Jaeger, payloads stripped. Land: *"This is what your
Dynatrace receives — timings and structure, no claim data, and a link back."*

**Back in the workbench: Approve** → the graph resumes from the interrupt and drafts the letter. Open the
**session**: recommendation, examiner review, letter — the human is in the same pane.

> Land: every interaction — agent to agent, agent to system, human to agent — is one drillable record.
> Ask: *"How do you trace across your A2A calls today?"*

## 2 · A failure in production, and where it happened (8 min)

Langfuse → Traces, filter score `guard-triggered = true` (or tag `traffic:release-1.4.0`).

> "Release 1.4.0 shipped a retriever change. Watch what it did to a New York claim."

- `critical-rule-guard` at WARNING: the agent missed `LIFE-CONT-001`. The guard — the eight critical rules
  codified as code — added it before adjudication, so the claim was held, not paid.
- `sop-rules` → `retrieval-coverage` far below 1 (a New York life claim gets 1 of its 12 tagged SOPs): the
  retriever never returned the company-wide SOPs. The model was never shown the rule. Without the
  intermediate spans you would blame the prompt.
- `rule-completeness` (judge) says "complete" — it only sees what was retrieved. That is why coverage is a
  separate, deterministic check. Say it plainly: judges have blind spots; design around them.

Dashboard **Claims — value & quality** → "Guard interventions over time", "Retrieval coverage by
version".

> Land: the guard protects the claimant; the trace tells you which component to fix.
> Ask: *"Which of your rules would you codify as a guard on day one?"*

## 3 · Before it ships: the answer key and the experiment (12 min)

Datasets → `claims-golden-v1`: 92 claims, each with the rules an SME says apply (from the codified
rules), the decision and the amounts. Open one item.

> "This is the asset. Codify the rules once; every prompt, model and release is then measured against it."

**Compare runs:** production v1 vs the `staging` candidate (v2, "concise — examiners said the lists are too
long").
- Critical-rule recall: 1.0 for both. The judge likes v2 too.
- Rule recall 0.98 → **0.75**; commission flag 1.0 → **0.65**. Open an item: the commission chargeback
  and the state notice are gone. *"Every block survived. The money and the notices leaked."*

**Model comparison** (same prompt, `--model`):
- Sonnet 4.6, the previous model: precision 0.65, decision match 0.75 — why the team upgraded.
- Haiku 5.5: recall 0.97, precision 1.0, passes the gate at about a third of the cost. That's the
  business decision on the table.

Presenter view on in the workbench to show the oracle next to the agent on any claim.

> Land: you see a regression — and a cheaper model's real trade-off — before a claimant does.
> Ask: *"What would your examiners label first: 30 claims for one product, or 10 for each state?"*

## 4 · Global vs use-case evaluations (5 min)

Evaluators page: `rule-completeness`, `adjudication-faithfulness`, `letter-quality`,
`instruction-resistance`, `certificate-extraction`, each a rule that selects its step by
`metadata.role` and runs only on production.

Walk the split in [docs/EVALS_PLATFORM_VS_USE_CASE.md](docs/EVALS_PLATFORM_VS_USE_CASE.md): platform
checks need no business knowledge (PII, injection, tone, latency, cost); use-case checks need the
examiner's answer key (recall, citations, amounts, notices). Dashboard **Claims — agent report card**:
the same score names in every project.

> Land: the platform team owns the harness, the claims team owns the answer key.

## 5 · Prompt management and the CI gate (8 min)

Prompts → `claims-sop-rules`: versions, labels, the model in the prompt config. Production fetches by
label with a 10 s cache.

```bash
.venv/bin/python scripts/prompt_gate.py --prompt-label staging      # exit 1: blocked on rule-recall, commission-flag
```

Show the Markdown verdict; in GitHub Actions the same script runs on every prompt change (Langfuse prompt
automation → repository dispatch → `claims-prompt-gate.yml` → "Ship it" only on a pass with
`production`). Terraform: the official `langfuse-terraform-aws` module for the instance, the seeders for
configuration as code ([cicd/README.md](cicd/README.md)).

Self-hosted tab → claims-prod → Prompts: `production` is a **protected** label. Sign in as the claims
engineer (MEMBER on prod): they can create versions and move `staging`, but not `production`; the release
manager (ADMIN on prod) can. Roll back = move the label.

> Land: prompts are change-managed like code, without redeploying the agents.

## 6 · Value and governance (5 min)

Dashboard **Claims — value & quality**: claims processed, LLM spend, examiner time saved and value (labelled
assumptions — replace with your baselines), decisions, override reasons, quality by release · prompt
version.

Self-hosted Enterprise (`:3200`): Settings → Members (project-level roles: claims, annuities, model risk),
Audit logs (the promotion and the rollback), Data retention (90 days), ingestion masking and SCIM as
configuration. Same containers as open source; the license key turns these on.

> Close: *"In a POC we'd do this with 5–10 of your SOPs and 30 claims your examiners label, on your AWS
> account. Who on your side owns the SOP rules, and who would label the claims?"*

---

## Appendix — commands behind each act

| Act | Command / place |
|---|---|
| 1 | `./scripts/run_services.sh` → workbench `:8095`; `scripts/run_claim.py CLM-2026-00042` as fallback |
| 2 | `scripts/generate_traffic.py --scenario release-1.4.0` (already run) · filter `guard-triggered` |
| 3 | `scripts/run_all_experiments.sh` (already run) · Datasets → `claims-golden-v1` → Compare |
| 4 | `scripts/seed_evals.py` · `scripts/seed_dashboards.py` |
| 5 | `scripts/prompt_gate.py --prompt-label staging` · `scripts/prompt_label.py --show` |
| 6 | `scripts/bootstrap_selfhosted.sh` (already run) · `http://localhost:3200` |

Fallback if live calls are slow: every act has a pre-run trace or run — `scripts/verify_demo.py --links`.

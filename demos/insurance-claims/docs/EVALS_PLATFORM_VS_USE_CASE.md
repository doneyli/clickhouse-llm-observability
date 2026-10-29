# Which evaluations belong to the platform, and which to the use case

A carrier running many agents on one AI platform has to decide who owns which evaluations. The rule of
thumb this demo follows:

> **Platform evaluations need no business knowledge. Use-case evaluations need a domain expert's answer
> key — and they are the ones regulators ask about.**

The platform team owns the *harness* for both: the judge model connection, the score vocabulary, the
dataset conventions, the CI gate, calibration and the dashboards. The use-case team owns the ground truth
and the domain checks.

## The split, applied to the claims agents

| Evaluation | Scope | Kind | Why it sits there |
|---|---|---|---|
| PII / PHI in customer-facing text (`no-pii-in-letter`) | Platform | Code | Same patterns for every agent; backed by ingestion masking |
| Prompt injection, including text inside documents (`instruction-resistance`) | Platform | Guardrail + judge | Every agent that reads untrusted input needs it |
| Tone and plain language of customer letters (`letter-quality`, `reading-level`) | Platform | Judge + code | A company standard, not a claims rule |
| Latency, cost, tool errors, warnings per agent | Platform | Metrics + Monitors | The agent report card |
| Hand-off to a human when required (`escalation-when-required`) | Platform | Code | Policy for every agent: thresholds vary, the check does not |
| Groundedness of explanations (`adjudication-faithfulness`) | Platform template, use-case rubric | Judge | The shape is generic; the policy it checks is the use case's |
| Rule recall / critical-rule recall / precision | **Use case** | Code vs SME answer key | Needs codified SOP rules — the claims team's asset |
| Citation validity (rule exists, in scope, in effect on the date of death) | **Use case** | Code | Needs the rule registry with effective dates |
| Decision, benefit and interest within tolerance, commission chargeback | **Use case** | Code vs oracle | Financial correctness of this process |
| Required state and plan notices in letters (`disclosures-present`) | **Use case** | Code | State-specific regulation |
| Rule completeness in production (`rule-completeness`) | **Use case** | Judge, calibrated against SMEs | No answer key in production, so a judge — validated by examiners |
| Document field accuracy (`field-accuracy`, `certificate-extraction`) | **Use case** | Code + multi-modal judge | The fields that matter are the claim's |
| Examiner override rate and reason | **Use case** | Human | The best quality signal in production |

## How the platform team runs it in Langfuse

1. **Evaluators as code.** Judges and their rules are created through the public evaluators and
   evaluation-rules API from the platform repository (`scripts/seed_evals.py` here), and the same
   definitions are applied to every project from the pipeline. The seeder reconciles: a changed prompt,
   filter or mapping is updated, not skipped.
2. **Select by metadata, never by name.** Every step observation carries `metadata.role`; rules filter on
   it (`stringObject` filter on `metadata`, key `role`). A renamed span never silently stops a judge. Ship
   the code that emits the key before the rule that filters on it.
3. **One score vocabulary.** Score configs are registered once (`examiner-override`, `override-reason`,
   `sme-rule-set-complete`, …), so every team's scores aggregate into the same dashboards — the agent
   report card.
4. **Online vs offline.** Deterministic checks run on every claim in the app (zero judge cost). Judges run
   on production traffic with per-rule sampling — the cost lever. Experiments run the full evaluator set
   against the answer key before a release; the CI gate reads their averages.
5. **Codify the critical rules.** Only the eight critical rules are codified as a deterministic guard that
   runs before adjudication; the long tail is the LLM's job and is measured offline. When the guard fires,
   the trace shows it and the claim is held, not paid.
6. **Calibrate the judges.** Examiner labels from the annotation queue are compared with the judge's
   scores (agreement and kappa) before a judge's score is trusted on a dashboard.

## What the use-case team brings to a POC

- 5–10 real SOPs and the rules an SME would codify from them (conditions, severity, effective dates)
- 30–50 historical claims labelled by examiners (applicable rules, decision, amounts)
- Baselines for the value view: monthly volume, examiner minutes per claim, override or error rate

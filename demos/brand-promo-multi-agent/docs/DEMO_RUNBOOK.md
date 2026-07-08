# Demo Runbook: Brand Promo Multi-Agent + Langfuse

60-minute demo flow for PromoPlanner with Langfuse observability.

---

## Morning-of Checklist (do this 30+ minutes before call)

- [ ] Langfuse running: open `http://localhost:3001` and verify login works
- [ ] `.env` has all keys: `ANTHROPIC_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`
- [ ] Dry run: `uv run scripts/run_live_demo.py play-all` from the demo root - all 5 queries complete
- [ ] In Langfuse: verify trace history shows data (50k traces visible in Traces list)
- [ ] Dashboards visible: Executive, Ops, Engineer dashboards all show charts
- [ ] Annotation queue visible: "PromoPlanner Human Review" has 10 items
- [ ] Prompts visible: Prompts page has ~12 entries under `promo-planner/`
- [ ] Dataset visible: `promo-planner-golden-v1` shows ~75 items in Datasets tab
- [ ] Pre-run experiment baseline: `uv run python scripts/run_experiment.py --run-name morning-baseline --sample 10` (so at least one run is visible in the Runs tab before the demo)
- [ ] Browser tabs pre-loaded: Traces, Dashboards (all 3), Prompts, Datasets (golden dataset open), Evaluators

---

## Demo Script (60 min)

### Segment 1: The Problem We're Solving (5 min)

**What to say:**
"AI agents make decisions that are hard to understand, hard to audit, and hard to improve. When something goes wrong in production - or when you want to make it better - you need visibility. That's what Langfuse gives you. Let me show you a real multi-agent system."

**What to show:**
- Architecture diagram (docs/ARCHITECTURE.md or slide)
- PromoPlanner is built with LangGraph + CrewAI + Anthropic Claude

---

### Segment 2: Running a Live Query (10 min)

**What to do:**
Run query 1 (happy path):
```
uv run scripts/run_live_demo.py play q1_happy_path
```

**While it runs, explain:**
"The orchestrator is classifying intent, then spinning up a research crew pulling from our mock sales and inventory data, a strategy crew generating promo options, and a compliance agent checking against brand guidelines. All of this is being traced."

**After it completes:**
- Switch to Langfuse Traces
- Open the most recent trace
- Show the Agent Graph view: "Here's the full span tree - you can see exactly what each sub-agent did, how long it took, what it cost."
- Point out: classify_intent -> research_crew -> strategy_crew -> compliance_agent -> compose_brief
- Show model used per span: "Sonnet for research, Opus for strategy (more reasoning), Haiku for compliance checks (faster/cheaper)"

---

### Segment 3: Compliance Catch (10 min)

**What to do:**
Run query 2 (compliance catch):
```
uv run scripts/run_live_demo.py play q2_compliance_catch
```

**What to say:**
"This query is asking us to market to children under 12. Watch what happens."

**After it runs:**
- Open the trace in Langfuse
- Navigate to the compliance_agent span
- Show the findings: HIGH severity, Rule 8
- Show the final brief: "Brief is REJECTED pending legal review - the agent didn't just fail silently, it produced an actionable output explaining why."

**Key talking point:** "This is Rule 8 in our brand guidelines triggering a HIGH severity finding. The agent caught it without a human in the loop."

---

### Segment 4: Observability Dashboards (10 min)

Switch to the Executive dashboard "Executive - Agent Fleet":
- "This is what the VP of AI would look at - total invocations, error rates, cost trends."
- Point at the agent invocations bar: "6 agents in the fleet. PromoPlanner is 20% of volume, CustomerCareBot is 30%."

Switch to "Ops - Agent Health":
- "The on-call team needs latency percentiles and error rates by tool. If query_sales is timing out at 4am, this is where you'd see it first."
- Point at latency percentiles: "p95 for PromoPlanner is higher because the strategy crew uses Opus which is slower but more capable."

Switch to "Engineer - PromoPlanner Deep Dive":
- "The AI engineer owns the quality loop. These score histograms show the distribution across the 3 evaluators."
- Point at response-factuality: "A few traces scoring 0.3-0.5 - those are our hallucination cases."

---

### Segment 5: LLM-as-Judge Evaluation (10 min)

**Navigate to Evaluators in Langfuse settings.**

"We have 3 evaluators configured. 10% of incoming live traces get scored automatically."

Run query 5 (hallucination catch):
```
uv run scripts/run_live_demo.py play q5_hallucination_catch
```

"This query asks for SKUs we've never released. The agent may hallucinate a SKU. Let's watch the evaluator catch it."

After the run:
- Open the trace
- Wait or fast-forward to evaluation score appearing
- Show the response-factuality score dropping below 0.6
- "The evaluator identified a hallucinated SKU code. Without this, a brand manager might have included it in an actual campaign brief."

---

### Segment 6: Prompt Management + Datasets (10 min)

**Navigate to Prompts.**
"Every prompt the agent uses is version-controlled here. If we want to improve the compliance check prompt, we edit it here, bump the version label to 'production', and the next run uses the new version."

**Navigate to Datasets.**
"We have a golden evaluation dataset with 75 labeled examples - 25 hand-authored to cover known failure modes, 50 generated from our product catalog slots to give us breadth across brands, regions, and retail partners."

Walk through 2-3 items to show the structure:
- Show input (the query), expected_output (intent, expected_tools, compliance_status, brief_should_contain)
- Point out metadata.intent_bucket: "These are stratified - 50% plan_promo, 20% compare_brands, 15% compliance checks, 10% edge cases, 5% out-of-scope. A realistic distribution for what real users ask."

---

### Segment 6.5: Live Experiment Run (10 min)

**This is the "here's how we measure improvement" moment.**

In terminal, run a fast 10-item sample:
```
uv run python scripts/run_experiment.py --run-name demo-baseline --sample 10
```

While it runs (60-90 seconds):
"The experiment runner is calling the PromoPlanner on each of these 10 items, then scoring the output with 6 deterministic evaluators and 4 LLM-as-judge evaluators. No human in the loop."

When it finishes, point at the rich summary table:
- "Here are the run-level scores. Intent classification is at X%, compliance matching at Y%."
- "The certification gate checks three thresholds: intent >= 85%, compliance >= 90%, factuality >= 80%."
- Point at gate result: "PASSED / FAILED."

**Navigate to Langfuse - Datasets > promo-planner-golden-v1 > Runs tab.**
- Show the run as a row with per-dimension scores
- Click into a specific item - show the per-item judge score with rationale comment
- "This is the audit trail. The brand manager can see exactly why a brief scored 0.4 on compliance_adherence."

**Bonus: A/B comparison (if time allows)**
```
uv run python scripts/run_experiment.py --run-name demo-v2 --label strategy-v2 --system-prompt-file prompts/strategy_v2.md --sample 10
```
"Same 10 items, different system prompt - v2 adds margin-protection constraints. Now look at the Runs tab - two rows, side-by-side. This is how you measure prompt engineering improvement."

---

### Segment 7: Human Review Queue (5 min)

**Navigate to Annotation Queues - "PromoPlanner Human Review".**

"Online eval catches a lot, but some cases need a human eye. Traces with ambiguous factuality scores (0.6-0.8) land here for review. A brand manager or AI quality reviewer can add their annotation directly in the UI."

Show one trace in the queue, highlight the score, add a demo annotation.

---

### Recovery Paths

**If a live query fails (LLM error, timeout):**
"This is a great moment to show the error trace. Open it in Langfuse - you'll see exactly which span failed and the error message. This is why observability matters in production."

**If Langfuse is not responding:**
- Check `docker ps` - Langfuse containers must be running
- Restart: `docker compose up -d` from the Langfuse directory
- Verify: `curl http://localhost:3001/api/public/health`

**If a query returns an unexpected brief:**
- Open the trace and show the span tree - walk through what the agent decided at each step
- "The LLM made a different decision than expected. In production, this is how you'd debug it."

---

## Post-Demo Commands

List all available demo queries:
```
uv run scripts/run_live_demo.py --help
```

Re-run all 5 queries:
```
uv run scripts/run_live_demo.py play-all
```

Re-seed everything (idempotent):
```
uv run scripts/seed_all.py
```

Run a cheap 10-item experiment rehearsal:
```
uv run python scripts/run_experiment.py --run-name rehearsal --sample 10
```

Run the full golden dataset (deterministic evaluators only, fast):
```
uv run python scripts/run_experiment.py --run-name full-baseline --evaluators deterministic
```

Run full experiment with all judges (slow, ~$10-15):
```
uv run python scripts/run_experiment.py --run-name full-all --evaluators all
```

Compare two prompt variants side-by-side:
```
uv run python scripts/run_experiment.py --run-name baseline --label baseline --sample 10
uv run python scripts/run_experiment.py --run-name v2 --label strategy-v2 --system-prompt-file prompts/strategy_v2.md --sample 10
```

Register score configs (run once, or after re-seeding):
```
uv run python scripts/setup_score_configs.py
```

# Langfuse LLM Observability Demo Runbook

A screen-by-screen demo script for presenting Langfuse LLM observability to customers. Designed for a 45-minute session (30 min demo + 15 min discussion).

**Target audience:** Engineering teams using AI coding assistants (Claude Code, Codex, Cursor) who want observability, evaluation, and optimization for their LLM usage.

---

## Pre-Demo Checklist (30 min before)

### Infrastructure
- [ ] Demo stack running: `./setup.sh --status`
- [ ] Langfuse UI accessible: http://localhost:3001
- [ ] Login: demo@example.com / demodemo1!
- [ ] Seed data loaded: `./scripts/seed-demo-data.sh`

### External Traces (optional - for Claude Code demo)
- [ ] Source Langfuse running (e.g., `claude-code-langfuse-template` on port 3050)
- [ ] Import traces:
  ```bash
  SOURCE_LANGFUSE_PUBLIC_KEY=<key> SOURCE_LANGFUSE_SECRET_KEY=<key> \
    python scripts/import-external-traces.py --limit 30 --scrub --add-tag claude-code-demo
  ```
- [ ] Verify: filter by tag `claude-code-demo` in Langfuse UI

### Datasets
- [ ] Datasets seeded: `python scripts/seed-datasets.py`
- [ ] Verify: Datasets sidebar shows `coding-assistant-quality` (12 items) and `coding-assistant-security` (8 items)

### Playground Setup
- [ ] LLM connections configured: Settings > LLM Connections
  - [ ] OpenAI key added (for GPT-4o)
  - [ ] Anthropic key added (for Claude)
- [ ] Test: open Playground, run a quick prompt

### Prompt Management
- [ ] Pre-create prompt `coding-assistant-v1`:
  - System: "You are a coding assistant. Answer the user's question with clear, correct code and explanations."
  - User: `{{question}}`

### Browser Tabs (pre-opened)
- [ ] Tab 1: Langfuse Traces view
- [ ] Tab 2: Langfuse Datasets view
- [ ] Tab 3: Langfuse Playground
- [ ] Tab 4: Claude Code integration docs - https://langfuse.com/integrations/other/claude-code
- [ ] Tab 5: Langfuse Get Started page (tool tabs) - https://langfuse.com/docs/observability/get-started

---

## Architecture Diagrams

### How a Customer Would Deploy

```
    +-------------------------------------------------------------+
    |              Engineering Team (N developers)                  |
    |                                                              |
    |   +--------------+  +--------------+  +--------------+      |
    |   |  Claude Code  |  |    Codex     |  |    Cursor    |      |
    |   +-------+------+  +------+-------+  +------+-------+      |
    |           |                 |                  |              |
    |           |  hooks          |  langfuse        |  cursor      |
    |           |  integration    |  skill           |  plugin      |
    |           +--------+        |       +----------+              |
    |                    |        |       |                         |
    |                    v        v       v                         |
    |              +-------------------------+                     |
    |              |    Langfuse Platform     |                     |
    |              |  (Cloud or Self-Hosted)  |                     |
    |              |                          |                     |
    |              |  Traces  Sessions  Costs |                     |
    |              |  Evals   Datasets  Proms |                     |
    |              +-----------+--------------+                     |
    |                          |                                    |
    |                   +------+------+                             |
    |                   | ClickHouse  |                             |
    |                   |   (OLAP)    |                             |
    |                   +-------------+                             |
    +-------------------------------------------------------------+
```

### Observe / Prevent / Optimize Cycle

```
              +-----------+
              |  OBSERVE  |
              |           |
              |  Traces   |
              |  Token $  |
              |  Sessions |
              |  Tool Use |
              +-----+-----+
                    |
            +-------+--------+
            v                v
       +----------+   +------------+
       | PREVENT  |   |  OPTIMIZE  |
       |          |   |            |
       | LLM-as-  |   | Datasets   |
       | Judge    |   | Playground |
       | Scores   |   | Prompt     |
       | Alerts   |   | Experiments|
       |          |   |            |
       | + Guard- |   | A/B Test   |
       |   rails* |   | Compare    |
       +----------+   +------------+

  * Guardrails = external layer (LLM Guard, NeMo, Lakera).
    Langfuse monitors their effectiveness.
```

### RBAC / Project Structure Options

**Option A: Per-Tool Projects**
```
    Organization: <Customer>
      +-- Project: claude-code       (dedicated API key)
      +-- Project: codex             (dedicated API key)
      +-- Project: cursor            (dedicated API key)
      +-- Project: shared-datasets   (cross-tool evaluation)
```

**Option B: Single Project with Tags (simpler)**
```
    Organization: <Customer>
      +-- Project: ai-coding-assistants
            Tags: tool:{claude-code|codex|cursor}
                  team:{frontend|backend|data|platform}
            Environments: production, staging
```

---

## Demo Script

---

### Opening: Frame the Problem [0:00 - 3:00]

**Screen:** Camera only, no screen share yet.

**Say:**

> "You have N people using AI coding assistants every day, hitting LLM providers directly. Right now you have zero visibility into what's happening - how much it costs, whether the outputs are good, which tools are most effective, and whether anyone is accidentally sending sensitive data to the LLMs.
>
> Today I'll walk you through Langfuse - an open-source LLM engineering platform - and show how it addresses three goals: Observe, Prevent, and Optimize. I'll use a live demo environment with real data."

**Transition:** Share screen.

---

### Act 1: Tracing & Observability [3:00 - 13:00]

---

#### Step 1.1: Traces Overview [3:00 - 5:00]

**Screen:** Langfuse > Traces tab

**Action:** Navigate to localhost:3001 > left sidebar > Traces.

**What audience sees:** A table of traces with columns: Name, Timestamp, Latency, Tokens, Cost, Tags.

**Say:**

> "This is the Traces view - every LLM interaction from every developer lands here as a row. You can see the trace name, when it happened, how long it took, how many tokens it consumed, and what it cost.
>
> Each trace captures the full lifecycle - the user's prompt, the model's response, and every tool call in between."

**Fallback:** If empty, run `./scripts/seed-demo-data.sh` from terminal.

---

#### Step 1.2: Drill into a Trace [5:00 - 7:00]

**Screen:** Click any trace tagged `text-to-sql`.

**Action:** Open trace detail view. Show the span hierarchy.

**What audience sees:** Tree of spans: User prompt > Claude generation (analysis) > SQL execution > Claude generation (response). Each span shows input, output, latency, tokens.

**Say:**

> "Let me drill into one trace. This is a text-to-SQL interaction - a natural language question, LLM analysis, SQL generation, execution against ClickHouse, and a formatted answer.
>
> Notice the span hierarchy - you can see every step, what data went in and came out, how long each step took, and the token count for each LLM call."

---

#### Step 1.3: Claude Code Traces [7:00 - 9:00]

**Screen:** Back to Traces list. Filter by tag `claude-code-demo` (or `claude-code`).

**What audience sees:** Traces named "Turn 1", "Turn 2", etc., with tool spans (Tool: Read, Tool: Edit, Tool: Bash, Tool: Grep).

**Say:**

> "These are real Claude Code session traces. Each 'Turn' is one interaction - the developer's prompt, Claude's response, and every tool call: file reads, edits, bash commands, grep searches.
>
> This is exactly what every Claude Code session from your engineers would look like in Langfuse."

**Action:** Click into a trace with multiple tool spans. Expand a "Tool: Edit" or "Tool: Bash" span.

**Key transition:**

> "The integration is a simple hook. Let me show you - all three coding tools are supported."

**Action:** Switch to Tab 5 (Langfuse Get Started page). Show tabs for Cursor, Claude Code, Codex.

**Say:**

> "Langfuse has integrations for all three major coding tools. Claude Code and Codex are officially maintained by Langfuse. Cursor has a community integration. All follow the same hooks pattern."

---

#### Step 1.4: Sessions View [9:00 - 10:00]

**Screen:** Left sidebar > Sessions.

**What audience sees:** Sessions grouped by session ID, showing traces per session, duration, cost.

**Say:**

> "Sessions group traces by conversation. One Claude Code session - one coding task. You can see how many turns it took, how long it ran, and the total cost. This answers: 'what did this developer spend on AI today?'"

---

#### Step 1.5: Cost Dashboard [10:00 - 13:00]

**Screen:** Left sidebar > Dashboard (or Metrics > Overview).

**What audience sees:** Charts: total cost over time, cost by model, token usage, trace counts.

**Say:**

> "Out of the box, Langfuse gives you cost tracking for OpenAI and Anthropic models. Spend by model, by day, drill into sessions or users driving cost.
>
> For a full team, you'd tag traces by developer, team, and tool - then build custom dashboards: 'how much is the backend team spending on Claude Code vs Codex this month?'"

---

### Act 2: Datasets [13:00 - 23:00]

---

#### Step 2.1: Show Pre-seeded Datasets [13:00 - 16:00]

**Screen:** Left sidebar > Datasets.

**Action:** Two datasets visible: `coding-assistant-quality` and `coding-assistant-security`.

**Say:**

> "Datasets are how you build test suites for your AI tools."

**Action:** Click into `coding-assistant-quality`. Show the 12 items.

**Say:**

> "12 coding challenges - write a function, debug code, explain a pattern, refactor, write tests. Each item has the input question and expected output - the quality criteria you'd judge the response against.
>
> This is your regression suite. When you update a system prompt or switch models, run your dataset against it to see if quality improves or regresses."

---

#### Step 2.2: Security Dataset [16:00 - 18:00]

**Screen:** Back to Datasets. Click into `coding-assistant-security`.

**Say:**

> "This dataset tests how the coding assistant handles sensitive content - API keys pasted into prompts, database credentials, requests to access unauthorized directories.
>
> The expected output defines what the assistant should do: detect the credential, don't echo it back, suggest environment variables instead. Use this with automated evaluators to catch when your tools mishandle sensitive data."

---

#### Step 2.3: Add Items from Traces [18:00 - 20:00]

**Screen:** Left sidebar > Traces. Find a trace.

**Action:** Click into a trace. Click "Add to Dataset".

**Say:**

> "Here's the production feedback loop. You're reviewing traces, you find a great interaction - or a terrible one. Click 'Add to Dataset' and it becomes a test case.
>
> Over time, your team builds a regression suite from real production interactions."

**Fallback:** If button isn't visible, try from an observation within the trace.

---

#### Step 2.4: Bulk Add + CSV [20:00 - 23:00]

**Screen:** Traces or Observations table.

**Action:** Show multi-select, Actions > Add to Dataset. Also show CSV import option in a dataset.

**Say:**

> "You can bulk-add from the traces table. And if you have existing test data in spreadsheets, there's a CSV upload option. Create items manually, import from CSV, or capture from production."

---

### Act 3: Playground & Experiments [23:00 - 33:00]

---

#### Step 3.1: Playground Side-by-Side [23:00 - 26:00]

**Screen:** Left sidebar > Playground.

**Action:**
- Set prompt: System="You are a coding assistant..." / User="Write a Python function to merge two sorted lists."
- Duplicate to create Variant 2
- Variant 1: GPT-4o, temp 0.3
- Variant 2: Claude Sonnet, temp 0.3
- Click "Run all"

**What audience sees:** Two responses side by side with different outputs, tokens, latency.

**Say:**

> "The Playground lets anyone - engineer or not - test prompts without writing code. Same prompt, two models, side by side. One run, immediate comparison.
>
> You can see the difference in responses, token usage, and latency. This is how you'd evaluate whether switching models makes sense."

---

#### Step 3.2: Prompt Management [26:00 - 28:00]

**Screen:** Prompt Management (show pre-created `coding-assistant-v1`).

**Say:**

> "When you find a prompt that works, save it to Prompt Management. Version-controlled - v1, v2, v3. Your team pulls prompts from Langfuse instead of hardcoding them. New version? Test it before rolling out.
>
> This is how you standardize prompts across the team."

---

#### Step 3.3: Run Experiment on Dataset [28:00 - 31:00]

**Screen:** Datasets > `coding-assistant-quality` > Run Experiment.

**Action:**
1. Select `coding-assistant-v1` prompt
2. Select `coding-assistant-quality` dataset
3. Choose model
4. Optionally add evaluator
5. Click "Create"

**Say:**

> "Now we connect the dots. This prompt, this dataset, this model. Langfuse runs the prompt for each item and collects outputs.
>
> You get actual output vs expected output for every item. Run again with a different model or prompt version - compare runs to see which performs better."

**Fallback:** If slow, show test-scenario traces and scores as an example of completed results.

---

#### Step 3.4: Evaluators — LLM-as-a-Judge + Code [31:00 - 33:00]

**Screen:** Evaluators page (this demo pre-provisions both kinds — see [CODE_EVALUATORS.md](CODE_EVALUATORS.md)).

**Say:**

> "Automated evaluation, two complementary kinds. LLM-as-a-Judge evaluators run on your observations or dataset runs — define criteria like 'was the code correct?' and Langfuse scores every response automatically. This project ships three: Relevance, Correctness, and Hallucination, scoring the test scenarios live.
>
> And code evaluators — deterministic TypeScript that runs inside Langfuse. For the security dataset, `security-behavior-check` answers exactly 'did the assistant detect and redact the credential?' — with string logic, not a model, so it's free, instant, and runs on 100% of items. Same idea on live traffic: every text-to-sql response gets an `sql-risk` score, every generation is scanned for leaked API keys."

**Action:** Open `security-behavior-check` → show the TypeScript source and a scored run. Then open a `text-to-sql` trace → Scores panel shows `sql-risk` next to the judge scores.

---

### Closing Discussion [33:00 - 45:00]

---

#### Map Back to Goals [33:00 - 37:00]

**Observe:**
> "Tracing gives full visibility into every AI interaction across all three tools. Cost tracking shows exact spend. Sessions group by coding task. Custom dashboards slice by team, tool, developer."

**Prevent:**
> "Langfuse is your eyes - it sees everything. For active prevention - blocking sensitive data, enforcing cost limits - pair it with a guardrails layer like LLM Guard or NeMo Guardrails. Langfuse monitors whether those guardrails work, which is arguably more important than the guardrails themselves."
>
> Docs reference: https://langfuse.com/docs/security-and-guardrails

**Optimize:**
> "Datasets, Playground, and Experiments give you the iteration loop. Before changing a system prompt, run it against your dataset. Experiments give quantified comparison - not just vibes."

---

#### Deployment Options [37:00 - 40:00]

> "Langfuse Cloud - managed service, fastest to start. Sign up, get API keys, install hooks, tracing in minutes."
>
> "Self-hosted - full control, your infrastructure. Langfuse v3 uses ClickHouse as its analytics backend - same columnar engine optimized for high write throughput."
>
> Docs reference: https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse

---

#### RBAC & Scale [40:00 - 42:00]

Show RBAC diagram from above.

> "Full RBAC: Organizations, Projects, Roles (Owner/Admin/Member/Viewer). Project-level overrides. Enterprise SSO via OIDC (Okta, Azure AD). SCIM for automated provisioning."
>
> Docs reference: https://langfuse.com/docs/administration/rbac

---

#### Open Questions [42:00 - 45:00]

1. "How are coding tools provisioned? API keys per dev, org accounts?"
2. "Central proxy or direct API access?"
3. "What does 'prevent' mean specifically? Cost limits? Content filtering? Policy enforcement?"
4. "Compliance requirements for storing traces? Data residency?"

---

## Fallback Plans

| If This Breaks... | Do This Instead |
|---|---|
| Langfuse UI not loading | `docker compose --profile langfuse up -d`, wait 60s. If still down, use https://langfuse.com/demo (view-only). |
| No traces visible | Run `./scripts/seed-demo-data.sh` during the call. Traces appear in ~60s. |
| Claude Code traces missing | Filter by `text-to-sql` instead. Walk through span hierarchy. Mention "Claude Code traces look identical but with tool call spans." |
| Playground LLM connection fails | Show Prompt Management concept. Use docs Playground screenshots. |
| Experiment too slow | Show test-scenario traces and scores as example of completed results. |
| Dataset creation fails | Walk through UI manually: Datasets > New > add one item live. |
| Screen share issues | Have https://langfuse.com/watch-demo ready. |

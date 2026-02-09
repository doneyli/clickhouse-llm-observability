# Demo Slides - Speaker Notes

## Slide 18: Live Demo - Agent Conversation
**Title:** "Demo: AI Agent Querying ClickHouse"

### Speaker Notes:

"Let me show you how this all works in practice. Here we have LibreChat - an open source AI chat interface similar to ChatGPT, but one we can fully instrument and customize.

I've configured an agent with access to what we call the MCP SQL Playground. MCP stands for Model Context Protocol - it's an open standard that lets AI agents interact with external tools in a structured way.

Watch what happens when I ask the agent a question about our data..."

**[Type query like: "What are the top 5 most expensive queries from the last hour?"]**

"The agent understands I want to analyze query performance. Behind the scenes, it's:
1. Formulating a SQL query against ClickHouse's system tables
2. Executing that query through the MCP server
3. Interpreting the results in natural language

This is powerful - business users can ask questions in plain English and get answers from ClickHouse without knowing SQL. But here's the key point for observability: every single step of this interaction is being traced and sent to ClickHouse itself."

**Key points to emphasize:**
- Natural language to SQL translation happening in real-time
- Agent autonomously deciding which tool to use
- All of this is observable - we're not flying blind

---

## Slide 19: Live Demo - Traces in Langfuse
**Title:** "Demo: Full Trace Visibility"

### Speaker Notes:

"Now let's see what that conversation looks like from an observability perspective. I'm switching to Langfuse - an open source LLM observability platform that uses ClickHouse as its analytics backend.

**[Navigate to Langfuse at localhost:3001, open the Traces view]**

Here's the trace from our conversation. Let me walk you through what we're seeing:

**[Point to the trace waterfall]**

At the top level, we have the user's request. Then you can see the LLM call - notice these gen_ai attributes. This is OpenTelemetry's semantic convention for generative AI:

- `gen_ai.prompt` - the actual prompt sent to the model
- `gen_ai.completion` - what the model returned
- `gen_ai.usage.prompt_tokens` - input tokens consumed
- `gen_ai.usage.completion_tokens` - output tokens generated
- `gen_ai.request.model` - which model was used

**[Expand the MCP tool call span]**

And here's the MCP tool call. You can see the SQL query the agent generated, how long ClickHouse took to execute it, and the results that came back.

This is the visibility you need when running AI in production. When something goes wrong - maybe a query is slow, maybe the agent hallucinates - you can trace exactly what happened.

**[Show token costs if available]**

And because we're capturing token counts, we can calculate costs. Each of these requests has a dollar value attached. Over time, you can identify which features or users are driving your AI spend."

**Key points to emphasize:**
- gen_ai.* semantic conventions are the emerging standard
- Full prompt/completion visibility for debugging
- Token tracking enables cost management
- Same ClickHouse backend storing both app metrics AND AI telemetry

---

## Slide 20: Live Demo - Evaluation Pipeline
**Title:** "Demo: Automated Quality Evaluation"

### Speaker Notes:

"Traces tell us WHAT happened. But how do we know if the AI's responses were actually GOOD? That's where evaluation comes in.

**[Switch to Langfuse dashboard or ClickHouse query results]**

We've integrated Langfuse for evaluation. Let me show you how this works:

Every conversation that goes through LibreChat gets exported to Langfuse. Langfuse is an open source LLM engineering platform - think of it as purpose-built analytics for AI applications.

**[Show Langfuse trace view]**

Here's the same conversation we just looked at, but now with evaluation scores attached:

- **Groundedness**: Did the response stick to facts from the retrieved data, or did the model make things up?
- **Answer Relevance**: Did the response actually answer what the user asked?
- **Context Relevance**: Was the retrieved context useful for answering the question?

These scores are computed automatically using a technique called LLM-as-judge - we use a model to evaluate another model's outputs.

**[Show ClickHouse query or dashboard with evaluation trends]**

And here's the best part - all of these evaluation results flow back into ClickHouse. So now I can write queries like:

```sql
SELECT
  avg(groundedness_score),
  avg(relevance_score)
FROM llm_evaluations
WHERE timestamp > now() - INTERVAL 1 DAY
```

Or build dashboards showing quality trends over time. Are we getting better or worse? Which types of questions have low groundedness? This is how you operationalize AI quality at scale."

**Key points to emphasize:**
- Manual review doesn't scale - need automated evaluation
- LLM-as-judge pattern for computing quality metrics
- Evaluation data lives in ClickHouse alongside traces
- Enables alerting on quality degradation
- Same SQL skills your team already has

---

## Demo Tips

### Before the demo:
1. Have LibreChat open and logged in
2. Have Langfuse open in another tab, filtered to recent traces
3. Have a few interesting queries ready to ask the agent
4. Clear any old test data that might confuse the narrative

### Good demo queries to ask the agent:
- "What are the top 5 slowest queries from today?"
- "Show me memory usage trends for the last hour"
- "Which tables are using the most disk space?"
- "Are there any failed queries I should investigate?"

### If something goes wrong:
- If agent fails: "This actually demonstrates why observability matters - let's look at the trace to see what went wrong"
- If traces are delayed: "Traces are batched for efficiency, should appear in a few seconds"
- If evaluation scores are missing: "Evaluation runs async - scores populate within a minute"

### Timing:
- Slide 18 (Agent conversation): ~3 minutes
- Slide 19 (Traces): ~4 minutes
- Slide 20 (Evaluation): ~3 minutes
- Total demo section: ~10 minutes

---

## Backup Screenshots

If live demo fails, have these screenshots ready:
1. LibreChat conversation with SQL results
2. Langfuse trace view with spans and token usage
3. Langfuse evaluation scores
4. ClickHouse dashboard with quality metrics over time

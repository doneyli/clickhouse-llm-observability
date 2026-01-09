# LLM Evaluation Architecture

This document explains the evaluation strategy for LLM applications in production, based on industry best practices and research from leading observability platforms.

---

## The Problem: Real-Time vs Async Evaluation

When building LLM observability, a key question arises: **Should we evaluate LLM outputs in real-time (before showing to users) or asynchronously (after the fact)?**

The answer depends on **what you're evaluating**:

| Evaluation Type | Purpose | Timing | Action |
|----------------|---------|--------|--------|
| **Safety Guardrails** | Block harmful content | Real-time | Block response |
| **Quality Evaluation** | Measure output quality | Async | Score & learn |
| **Human Feedback** | Ground truth signal | After response | Inform improvements |

---

## Why Async for Quality Evaluation?

Industry research from [Langfuse](https://langfuse.com/blog/2025-03-04-llm-evaluation-101-best-practices-and-challenges), [Datadog](https://www.datadoghq.com/blog/llm-evaluation-framework-best-practices/), and [Arize](https://arize.com/llm-evaluation/) recommends **async evaluation** for quality metrics because:

### 1. Latency Impact
Real-time LLM-as-judge evaluation adds **5-10 seconds** per request. Users won't wait.

### 2. Cost at Scale
Evaluating 100% of traffic with an LLM judge is expensive. Best practice is to sample:
- **1-5% random sampling** for baseline quality metrics
- **100% of thumbs-down** flagged outputs for targeted analysis

### 3. No Blocking Needed
A relevance score of 0.7 doesn't mean you should hide the response. Quality scores inform improvements, they don't gate responses.

### 4. Human Feedback is Ground Truth
Automated evaluations scale human intuition, but **human feedback remains the gold standard**. Async evaluation lets you correlate automated scores with actual user satisfaction.

---

## When Real-Time Guardrails ARE Needed

Real-time blocking is appropriate for **safety and compliance**, not quality:

| Guardrail Type | Example | Why Real-Time |
|---------------|---------|---------------|
| PII Detection | Block SSN, credit cards | Legal/compliance requirement |
| Toxicity Filter | Block offensive content | Reputation risk |
| Prompt Injection | Block malicious inputs | Security requirement |
| Topic Boundaries | Block off-topic requests | Product scope |

These are **binary safety decisions** where blocking is required before the user sees the response.

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PRODUCTION LLM ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User Input                                                                  │
│      │                                                                       │
│      ▼                                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    REAL-TIME LAYER (Optional)                        │    │
│  │  ┌─────────────────┐              ┌─────────────────┐               │    │
│  │  │ Input Guardrails│    LLM      │ Output Guardrails│               │    │
│  │  │ - PII detection │ ──────────▶ │ - Toxicity       │               │    │
│  │  │ - Prompt inject │              │ - Harmful content│               │    │
│  │  └────────┬────────┘              └────────┬────────┘               │    │
│  │           │ Block if unsafe                │ Block if unsafe        │    │
│  └───────────┼────────────────────────────────┼────────────────────────┘    │
│              │                                │                              │
│              ▼                                ▼                              │
│         Response to User ◀────────────────────┘                             │
│              │                                                               │
│              │  User provides feedback                                       │
│              ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         USER FEEDBACK                                │    │
│  │                    👍 Thumbs Up  /  👎 Thumbs Down                   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│              │                                                               │
│ ═════════════╪═══════════════════════════════════════════════════════════   │
│              │           ASYNC EVALUATION LAYER                              │
│              ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    TRACE STORE (HyperDX/ClickHouse)                  │    │
│  │                                                                      │    │
│  │  • Prompts & completions (gen_ai.prompt, gen_ai.completion)         │    │
│  │  • Token usage & latency                                            │    │
│  │  • User feedback linked to trace_id                                 │    │
│  │  • Model information                                                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│              │                                                               │
│              │  Batch/Scheduled Job                                          │
│              ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    ASYNC QUALITY EVALUATION                          │    │
│  │                                                                      │    │
│  │  Sampling Strategy:                                                 │    │
│  │  • 1-5% random sample of all traces                                 │    │
│  │  • 100% of thumbs-down flagged outputs                              │    │
│  │  • New/changed prompts get higher sampling                          │    │
│  │                                                                      │    │
│  │  Evaluations (TruLens LLM-as-Judge):                                │    │
│  │  • Answer Relevance - Does answer address question?                 │    │
│  │  • Coherence - Is response well-structured?                         │    │
│  │  • Groundedness - Is it supported by context? (RAG)                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│              │                                                               │
│              ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    DASHBOARDS & ALERTING                             │    │
│  │                                                                      │    │
│  │  HyperDX (Operational)          TruLens (Quality)                   │    │
│  │  • Latency percentiles          • Quality score trends              │    │
│  │  • Token usage & cost           • Low-scoring outputs               │    │
│  │  • Error rates                  • Judge reasoning                   │    │
│  │  • Trace exploration            • App comparison                    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│              │                                                               │
│              ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    HUMAN REVIEW QUEUE                                │    │
│  │                                                                      │    │
│  │  Flagged for human review:                                          │    │
│  │  • Low quality scores (< 0.5)                                       │    │
│  │  • User thumbs-down feedback                                        │    │
│  │  • Automated evaluation disagreements                               │    │
│  │                                                                      │    │
│  │  Human reviewers provide ground truth labels                        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Async Batch Evaluation (Current)

Evaluate LibreChat conversations by querying traces from HyperDX/ClickHouse.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  LibreChat  │────▶│  HyperDX    │────▶│   Trace     │────▶│  TruLens    │
│  (traces)   │     │  ClickHouse │     │  Evaluator  │     │  Dashboard  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

**Components:**
- `trace-evaluator/` - Python service that queries ClickHouse and runs TruLens evaluations
- Scheduled job (cron or manual trigger)
- Results stored in shared TruLens SQLite database

**What gets evaluated:**
- LibreChat conversations (prompts + completions)
- Sampled at configurable rate (default 100% for demo, 1-5% for production)

### Phase 2: Human Feedback Integration (Future)

Add thumbs up/down feedback to LibreChat and link to evaluation pipeline.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  LibreChat  │────▶│  Feedback   │────▶│  Trace      │
│  👍 / 👎    │     │  Service    │     │  Evaluator  │
└─────────────┘     └─────────────┘     └─────────────┘
                                               │
                                               ▼
                                        Prioritize
                                        thumbs-down
                                        for evaluation
```

**Components:**
- Feedback UI in LibreChat (or webhook)
- Feedback linked to trace IDs
- Priority evaluation for negative feedback

### Evaluation OTEL Span Structure

Each evaluation emits OpenTelemetry spans to HyperDX/ClickHouse, providing full traceability between the original LLM call and its quality evaluation.

#### Span Hierarchy

For each evaluated conversation, the trace-evaluator creates:

```
llm.evaluation (root span)
├── gen_ai.request.model = "claude-3-5-haiku-20241022"  (judge model)
├── eval.source_model = "claude-sonnet-4-20250514"      (generation model)
├── eval.source_trace_id = "abc123..."                  (link to original)
├── eval.relevance_score = 0.95
├── eval.coherence_score = 0.88
│
├── ChatAnthropic.chat (child span - relevance evaluation)
│   └── gen_ai.request.model = "claude-3-5-haiku-20241022"
│
├── ChatAnthropic.chat (child span - coherence evaluation)
│   └── gen_ai.request.model = "claude-3-5-haiku-20241022"
│
└── ChatAnthropic.chat (child span - additional judge call if needed)
```

#### Key Attributes

| Attribute | Location | Description |
|-----------|----------|-------------|
| `gen_ai.request.model` | `llm.evaluation` span | The judge/evaluator model (e.g., claude-3-5-haiku) |
| `eval.source_model` | `llm.evaluation` span | The original model that generated the response |
| `eval.source_trace_id` | `llm.evaluation` span | TraceId of the original LLM conversation |
| `eval.source_span_id` | `llm.evaluation` span | SpanId of the original LLM conversation |
| `eval.source_service` | `llm.evaluation` span | Service name (e.g., librechat-conversations) |
| `eval.relevance_score` | `llm.evaluation` span | Answer relevance score (0.0-1.0) |
| `eval.coherence_score` | `llm.evaluation` span | Coherence score (0.0-1.0) |
| `eval.input` | `llm.evaluation` span | Original prompt (truncated to 1000 chars) |
| `eval.output` | `llm.evaluation` span | Original completion (truncated to 1000 chars) |

#### Correlating Conversations with Evaluations

**In HyperDX UI:**
1. Find a `librechat-conversations` trace, copy its TraceId
2. Search: `eval.source_trace_id:<trace-id>` to find its evaluation

**SQL Query - Join conversations with evaluations:**
```sql
SELECT
    o.TraceId as conversation_trace,
    e.TraceId as evaluation_trace,
    o.SpanAttributes['gen_ai.request.model'] as generation_model,
    e.SpanAttributes['gen_ai.request.model'] as judge_model,
    e.SpanAttributes['eval.relevance_score'] as relevance,
    e.SpanAttributes['eval.coherence_score'] as coherence,
    substring(o.SpanAttributes['gen_ai.prompt.0.content'], 1, 50) as prompt
FROM otel_traces o
JOIN otel_traces e ON o.TraceId = e.SpanAttributes['eval.source_trace_id']
WHERE o.ServiceName = 'librechat-conversations'
  AND e.ServiceName = 'trace-evaluator'
  AND e.SpanName = 'llm.evaluation'
ORDER BY e.Timestamp DESC
```

**SQL Query - View all evaluations with model attribution:**
```sql
SELECT
    SpanAttributes['eval.source_trace_id'] as conversation_trace_id,
    substring(SpanAttributes['eval.input'], 1, 40) as prompt,
    SpanAttributes['eval.source_model'] as generation_model,
    SpanAttributes['gen_ai.request.model'] as judge_model,
    SpanAttributes['eval.relevance_score'] as relevance,
    SpanAttributes['eval.coherence_score'] as coherence
FROM otel_traces
WHERE ServiceName = 'trace-evaluator'
  AND SpanName = 'llm.evaluation'
ORDER BY Timestamp DESC
LIMIT 20
```

#### Why Multiple Spans Per Evaluation?

Each evaluation generates 3-4 spans:
- **1 `llm.evaluation` span**: Parent span with all metadata and scores
- **2-3 `ChatAnthropic.chat` spans**: Actual LLM calls to the judge model (one per feedback function)

This is expected behavior - TruLens runs separate LLM calls for each feedback function (relevance, coherence), and OpenLLMetry auto-instruments these as child spans.

---

### Phase 3: Real-Time Safety Guardrails (Future, Optional)

Add input/output guardrails for safety-critical applications.

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   User      │────▶│   Input     │────▶│    LLM      │────▶│   Output    │
│   Input     │     │  Guardrail  │     │             │     │  Guardrail  │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                    Block: PII,                              Block: Toxic,
                    Prompt Injection                         Harmful Content
```

**When to implement:**
- Compliance requirements (healthcare, finance)
- User-facing apps with reputation risk
- Applications handling sensitive data

---

## Sampling Strategies

For production systems with high traffic, evaluate a sample:

| Strategy | Sample Rate | Use Case |
|----------|-------------|----------|
| Random | 1-5% | Baseline quality metrics |
| Thumbs-down | 100% | Targeted improvement |
| New prompts | 20-50% | Regression detection |
| Error traces | 100% | Debug failures |

**Cost estimation:**
- Judge LLM (Claude Haiku): ~$0.001 per evaluation
- 10,000 daily requests at 5% sample = 500 evaluations = $0.50/day

---

## Metrics to Track

### Operational (HyperDX)
- **Latency**: p50, p95, p99 response times
- **Token usage**: Input/output tokens per request
- **Cost**: Estimated cost per request/day
- **Errors**: Failed requests, timeouts

### Quality (TruLens)
- **Answer Relevance**: Does the answer address the question?
- **Coherence**: Is the response well-structured?
- **Groundedness**: Is it supported by retrieved context? (RAG only)
- **Quality trends**: Score changes over time

### Human Feedback
- **Thumbs up/down ratio**: User satisfaction signal
- **Feedback by category**: Which topics have issues?
- **Correlation**: Human feedback vs automated scores

---

## References

- [Langfuse - LLM Evaluation 101](https://langfuse.com/blog/2025-03-04-llm-evaluation-101-best-practices-and-challenges)
- [Datadog - LLM Evaluation Framework Best Practices](https://www.datadoghq.com/blog/llm-evaluation-framework-best-practices/)
- [Datadog - LLM Guardrails Best Practices](https://www.datadoghq.com/blog/llm-guardrails-best-practices/)
- [Giskard - Real-Time Guardrails vs Batch Evaluations](https://www.giskard.ai/knowledge/real-time-guardrails-vs-batch-llm-evaluations)
- [Confident AI - LLM Observability Guide](https://www.confident-ai.com/blog/what-is-llm-observability-the-ultimate-llm-monitoring-guide)
- [Arize - LLM Evaluation Platforms](https://arize.com/llm-evaluation-platforms-top-frameworks/)
- [AWS - Human-in-the-Loop LLM Evaluation](https://github.com/aws-samples/human-in-the-loop-llm-eval-blog)

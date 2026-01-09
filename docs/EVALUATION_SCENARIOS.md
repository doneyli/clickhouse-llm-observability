# LLM Evaluation Failure Scenarios

This document describes common LLM evaluation failure modes and how to demonstrate them using the test scenarios tool.

---

## Why Evaluation Matters

In production LLM applications, responses can fail in subtle ways that aren't immediately obvious:

| Failure Mode | What Happens | Business Impact |
|--------------|--------------|-----------------|
| **Off-Topic** | LLM answers a different question | Users don't get what they need |
| **Contradictory** | LLM gives conflicting information | Users can't trust the output |
| **Hallucination** | LLM fabricates confident-sounding facts | Misinformation, liability risk |

Quality scores from LLM-as-judge evaluation help surface these issues automatically.

---

## The Three Failure Scenarios

### Scenario 1: Low Relevance (Off-Topic Response)

**What it is:** The LLM provides a well-written, coherent response—but about the wrong topic.

**Example:**
- **Prompt:** "What are ClickHouse's pricing tiers for cloud hosting?"
- **Response:** *(Detailed explanation of ClickHouse's technical architecture, MergeTree engine, columnar storage... but nothing about pricing)*

**Why it happens:**
- The LLM latches onto keywords ("ClickHouse") and generates related content
- No retrieval/grounding to specific documentation
- Common in open-ended models without RAG

**Expected Scores:**
| Metric | Score | Why |
|--------|-------|-----|
| Relevance | 0.2-0.4 | Doesn't answer the question |
| Coherence | 0.9-1.0 | Well-structured, just wrong topic |

**Production Impact:** Users asking specific questions get generic marketing content instead.

---

### Scenario 2: Low Coherence (Contradictory Response)

**What it is:** The LLM contradicts itself multiple times, making the response unusable.

**Example:**
- **Prompt:** "Should I use ClickHouse or PostgreSQL for analytics?"
- **Response:** "ClickHouse is better. Actually PostgreSQL is better. Neither is suitable. Both are perfect. Use MongoDB instead. ClickHouse is the only option..."

**Why it happens:**
- Model uncertainty leads to hedging
- Long responses lose track of earlier statements
- Attempting to please all possible interpretations
- Can indicate model confusion or ambiguous prompts

**Expected Scores:**
| Metric | Score | Why |
|--------|-------|-----|
| Relevance | 0.5-0.7 | Attempts to address the question |
| Coherence | 0.1-0.3 | Contradicts itself repeatedly |

**Production Impact:** Users can't extract actionable advice; lose trust in the system.

---

### Scenario 3: Hallucination (Fabricated Information)

**What it is:** The LLM confidently states false information that sounds plausible.

**Example:**
- **Prompt:** "Who created ClickHouse and what is its history?"
- **Response:** "ClickHouse was created by Dr. Elena Volkov at MIT in 2008, funded by a $50M NSF grant. It was acquired by Google in 2012 for $200 million..."

**The Truth:** ClickHouse was created at Yandex by Alexey Milovidov and team, open-sourced in 2016.

**Why it happens:**
- Model generates plausible-sounding content from patterns
- No fact-checking mechanism
- Confident tone masks uncertainty
- Especially common for specific facts, dates, names

**Expected Scores:**
| Metric | Score | Why |
|--------|-------|-----|
| Relevance | 0.8-1.0 | Directly answers the question |
| Coherence | 0.9-1.0 | Well-structured narrative |

**Production Impact:** Misinformation at scale; potential legal/compliance issues; reputational damage.

> **Note:** Standard relevance and coherence metrics won't catch hallucinations! You need **groundedness** evaluation (checking against source documents) or **factual accuracy** checks.

---

## Using the Test Scenarios Tool

### List Available Scenarios

```bash
docker compose run --rm test-scenarios --list
```

### Export All Scenarios

```bash
docker compose run --rm test-scenarios
```

### Export Specific Scenarios

```bash
# Just the hallucination scenario
docker compose run --rm test-scenarios --scenario 3

# Multiple scenarios
docker compose run --rm test-scenarios --scenario 1 2 3
```

### Run Evaluations

```bash
# Evaluate the test scenarios
docker compose run --rm trace-evaluator python main.py \
  --service test-scenarios \
  --hours 1
```

### View Results

1. **HyperDX** (http://localhost:8080)
   - Search: `service:test-scenarios`
   - See the traces with `test_scenario.*` attributes

2. **TruLens Dashboard** (http://localhost:8501)
   - Look for `test-scenarios-eval` app
   - Compare scores across scenarios

---

## Interpreting Results

### Score Ranges

| Score | Interpretation |
|-------|----------------|
| 0.9-1.0 | Excellent - No issues detected |
| 0.7-0.9 | Good - Minor issues, acceptable |
| 0.5-0.7 | Fair - Noticeable issues, investigate |
| 0.3-0.5 | Poor - Significant problems |
| 0.0-0.3 | Critical - Major failure |

### What Low Scores Tell You

| Low Relevance | Low Coherence | Both Low |
|---------------|---------------|----------|
| Wrong topic | Contradictory | Completely broken |
| Missing key info | Rambling | Model confusion |
| Generic response | Illogical flow | Bad prompt design |

### Correlating with Traces

In HyperDX, you can filter evaluations by score:
```sql
SELECT
    SpanAttributes['eval.source_trace_id'] as trace_id,
    SpanAttributes['eval.relevance_score'] as relevance,
    SpanAttributes['eval.coherence_score'] as coherence,
    SpanAttributes['test_scenario.name'] as scenario
FROM otel_traces
WHERE ServiceName = 'trace-evaluator'
  AND SpanName = 'llm.evaluation'
  AND (
    toFloat64OrZero(SpanAttributes['eval.relevance_score']) < 0.5
    OR toFloat64OrZero(SpanAttributes['eval.coherence_score']) < 0.5
  )
ORDER BY Timestamp DESC
```

---

## Extending the Scenarios

To add custom scenarios, edit `test-scenarios/export_test_scenarios.py` and add to the `SCENARIOS` list:

```python
TestScenario(
    id=5,
    name="Your Scenario Name",
    category="Category",
    description="What this tests",
    prompt="The user's question",
    response="The problematic LLM response",
    model="claude-sonnet-4-20250514",
    expected_relevance="0.X-0.Y",
    expected_coherence="0.X-0.Y",
    why_low="Explanation of why scores should be low"
)
```

---

## References

- [TruLens RAG Triad](https://www.trulens.org/getting_started/core_concepts/rag_triad/) - Groundedness, relevance, context relevance
- [Confident AI - LLM Evaluation Metrics](https://www.confident-ai.com/blog/llm-evaluation-metrics-everything-you-need-for-llm-evaluation) - Comprehensive metrics guide
- [Evidently AI - LLM Hallucination Examples](https://www.evidentlyai.com/blog/llm-hallucination-examples) - Real-world hallucination patterns
- [Langfuse - LLM Evaluation Best Practices](https://langfuse.com/blog/2025-03-04-llm-evaluation-101-best-practices-and-challenges) - Evaluation challenges
- [Arize - LLM Hallucination Examples](https://arize.com/llm-hallucination-examples/) - Hallucination detection

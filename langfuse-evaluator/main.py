"""
Langfuse Evaluator - Async quality evaluation using Langfuse

Queries traces from Langfuse and runs LLM-as-judge evaluations (same as TruLens),
storing scores back in Langfuse.

Usage:
    python main.py                          # Evaluate recent traces
    python main.py --hours 24               # Evaluate traces from last 24 hours
    python main.py --limit 50               # Limit to 50 traces
    python main.py --list                   # List available traces
"""

import os
import sys
import argparse
import json
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx
from langfuse import Langfuse
from langchain_anthropic import ChatAnthropic


def get_langfuse_client() -> Langfuse:
    """Initialize Langfuse client."""
    return Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "http://localhost:3001")
    )


def get_langfuse_api_client():
    """Get HTTP client for Langfuse REST API."""
    host = os.getenv("LANGFUSE_HOST", "http://localhost:3001")
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    return httpx.Client(
        base_url=host,
        auth=(public_key, secret_key),
        timeout=30.0
    )


def evaluate_relevance(llm: ChatAnthropic, question: str, answer: str) -> tuple:
    """Evaluate answer relevance using LLM-as-judge (same as TruLens)."""
    prompt = f"""Evaluate how relevant the following answer is to the question.

Question: {question}

Answer: {answer}

Scoring criteria:
- 1.0: The answer directly and completely addresses the question
- 0.7-0.9: The answer mostly addresses the question with minor gaps
- 0.4-0.6: The answer partially addresses the question
- 0.1-0.3: The answer barely relates to the question
- 0.0: The answer is completely off-topic

Respond with a JSON object containing:
- "score": a float between 0.0 and 1.0
- "reason": a brief explanation of your scoring

Example: {{"score": 0.85, "reason": "The answer addresses the main question but misses some details"}}
"""

    try:
        response = llm.invoke(prompt)
        content = response.content

        # Parse JSON response
        import json
        import re

        # Try to find JSON in response
        json_match = re.search(r'\{[^}]+\}', content)
        if json_match:
            result = json.loads(json_match.group())
            return float(result.get("score", 0.5)), result.get("reason", "")
        else:
            return 0.5, "Could not parse evaluation response"

    except Exception as e:
        return 0.5, f"Evaluation error: {str(e)}"


def evaluate_coherence(llm: ChatAnthropic, answer: str) -> tuple:
    """Evaluate answer coherence using LLM-as-judge (same as TruLens)."""
    prompt = f"""Evaluate the coherence of the following text.

Text: {answer}

Scoring criteria:
- 1.0: The text is well-structured, logically organized, and easy to follow
- 0.7-0.9: The text is mostly coherent with minor issues
- 0.4-0.6: The text has some coherence issues but is understandable
- 0.1-0.3: The text is difficult to follow and poorly organized
- 0.0: The text is completely incoherent or self-contradictory

Respond with a JSON object containing:
- "score": a float between 0.0 and 1.0
- "reason": a brief explanation of your scoring

Example: {{"score": 0.9, "reason": "Well-structured response with clear logical flow"}}
"""

    try:
        response = llm.invoke(prompt)
        content = response.content

        # Parse JSON response
        import json
        import re

        json_match = re.search(r'\{[^}]+\}', content)
        if json_match:
            result = json.loads(json_match.group())
            return float(result.get("score", 0.5)), result.get("reason", "")
        else:
            return 0.5, "Could not parse evaluation response"

    except Exception as e:
        return 0.5, f"Evaluation error: {str(e)}"


def get_traces(hours: int = 24, limit: int = 100) -> List[Dict[Any, Any]]:
    """Fetch recent traces from Langfuse REST API."""
    try:
        client = get_langfuse_api_client()
        response = client.get(f"/api/public/traces?limit={limit}")
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])
    except Exception as e:
        print(f"Error fetching traces: {e}")
        return []


def extract_io_from_trace(trace: Dict) -> tuple:
    """Extract input/output from a Langfuse trace dict."""
    input_text = None
    output_text = None

    # Try to get input/output from trace directly
    trace_input = trace.get('input')
    if trace_input:
        if isinstance(trace_input, dict):
            input_text = trace_input.get('question') or trace_input.get('input') or str(trace_input)
        elif isinstance(trace_input, list):
            # Handle list of messages
            for msg in trace_input:
                if isinstance(msg, dict) and msg.get('role') == 'user':
                    input_text = msg.get('content', '')
                    break
            if not input_text:
                input_text = str(trace_input)
        else:
            input_text = str(trace_input)

    trace_output = trace.get('output')
    if trace_output:
        if isinstance(trace_output, dict):
            output_text = trace_output.get('answer') or trace_output.get('output') or trace_output.get('content') or str(trace_output)
        else:
            output_text = str(trace_output)

    return input_text, output_text


def has_evaluation_scores(trace: Dict) -> bool:
    """Check if a trace already has evaluation scores."""
    scores = trace.get('scores', [])
    if scores:
        score_names = [s.get('name', '') for s in scores if isinstance(s, dict)]
        return "relevance" in score_names or "coherence" in score_names
    return False


def evaluate_trace(
    llm: ChatAnthropic,
    trace: Dict,
    skip_evaluated: bool = True
) -> Optional[Dict[str, Any]]:
    """Run evaluations on a single trace and store scores in Langfuse."""

    trace_id = trace.get('id')
    trace_name = trace.get('name', '')

    # Skip eval_root traces (these are TruLens evaluations)
    if trace_name == 'eval_root':
        return None

    # Skip if already evaluated
    if skip_evaluated and has_evaluation_scores(trace):
        return None

    # Extract input/output
    input_text, output_text = extract_io_from_trace(trace)

    if not input_text or not output_text:
        return None

    # Run evaluations
    relevance_score, relevance_reason = evaluate_relevance(llm, input_text, output_text)
    coherence_score, coherence_reason = evaluate_coherence(llm, output_text)

    # Store scores in Langfuse via REST API
    try:
        client = get_langfuse_api_client()

        # Create relevance score
        client.post("/api/public/scores", json={
            "traceId": trace_id,
            "name": "relevance",
            "value": relevance_score,
            "dataType": "NUMERIC",
            "comment": relevance_reason
        })

        # Create coherence score
        client.post("/api/public/scores", json={
            "traceId": trace_id,
            "name": "coherence",
            "value": coherence_score,
            "dataType": "NUMERIC",
            "comment": coherence_reason
        })

    except Exception as e:
        print(f"Error storing scores for {trace_id}: {e}")
        return None

    return {
        "trace_id": trace_id,
        "input": input_text[:100] + "..." if len(input_text) > 100 else input_text,
        "relevance": relevance_score,
        "coherence": coherence_score,
        "relevance_reason": relevance_reason,
        "coherence_reason": coherence_reason
    }


def list_traces(limit: int = 20):
    """List recent traces."""
    print(f"\nFetching up to {limit} recent traces...\n")

    traces = get_traces(limit=limit)

    if not traces:
        print("No traces found.")
        return

    print(f"{'Trace ID':<40} {'Name':<30} {'Timestamp':<25}")
    print("-" * 95)

    for trace in traces:
        trace_id = trace.get('id', '')[:36]
        if len(trace.get('id', '')) > 36:
            trace_id += "..."
        name = (trace.get('name') or "N/A")[:28]
        timestamp = str(trace.get('timestamp', 'N/A'))[:23]
        print(f"{trace_id:<40} {name:<30} {timestamp:<25}")

    print(f"\nTotal: {len(traces)} traces")


def main():
    parser = argparse.ArgumentParser(
        description="Langfuse Evaluator - Run LLM-as-judge evaluations on Langfuse traces"
    )
    parser.add_argument("--hours", type=int, default=24, help="Hours of traces to evaluate")
    parser.add_argument("--limit", type=int, default=100, help="Maximum traces to evaluate")
    parser.add_argument("--list", action="store_true", help="List traces without evaluating")
    parser.add_argument("--force", action="store_true", help="Re-evaluate already scored traces")

    args = parser.parse_args()

    # Check environment
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
        sys.exit(1)

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY must be set for LLM-as-judge evaluations")
        sys.exit(1)

    # Initialize LLM for evaluations
    model = os.getenv("TRULENS_MODEL", "claude-3-5-haiku-20241022")
    llm = ChatAnthropic(model=model, temperature=0.0, max_tokens=500)

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║   Langfuse Evaluator                                      ║
║                                                           ║
║   Running same evaluations as TruLens:                    ║
║   - Answer Relevance (LLM-as-judge)                       ║
║   - Coherence (LLM-as-judge)                              ║
║                                                           ║
║   Judge Model: {model:<40} ║
╚═══════════════════════════════════════════════════════════╝
    """)

    if args.list:
        list_traces(limit=args.limit)
        return

    # Fetch and evaluate traces
    print(f"Fetching traces from last {args.hours} hours (limit: {args.limit})...")

    traces = get_traces(hours=args.hours, limit=args.limit)

    if not traces:
        print("No traces found to evaluate.")
        return

    print(f"Found {len(traces)} traces. Starting evaluation...\n")

    evaluated = 0
    skipped = 0

    for i, trace in enumerate(traces, 1):
        trace_id = trace.get('id', '')[:20]
        trace_name = trace.get('name', '')
        print(f"[{i}/{len(traces)}] {trace_name[:25]:<25} ({trace_id}...) ", end="")

        result = evaluate_trace(
            llm, trace,
            skip_evaluated=not args.force
        )

        if result:
            print(f"✓ relevance={result['relevance']:.2f}, coherence={result['coherence']:.2f}")
            evaluated += 1
        else:
            print("⊘ skipped")
            skipped += 1

    print(f"\n{'='*60}")
    print(f"Evaluation complete!")
    print(f"   Evaluated: {evaluated}")
    print(f"   Skipped: {skipped}")
    print(f"   View results: {os.getenv('LANGFUSE_HOST', 'http://localhost:3001')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

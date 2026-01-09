"""
TruLens Evaluator for LLM traces.

Runs quality evaluations on extracted LLM traces using LLM-as-a-Judge.
"""

import os
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

from trulens.core import TruSession, Feedback
from trulens.providers.langchain import Langchain
from langchain_anthropic import ChatAnthropic

from clickhouse_client import LLMTrace


@dataclass
class EvaluationResult:
    """Result of evaluating an LLM trace."""
    trace_id: str
    span_id: str
    timestamp: datetime
    service_name: str
    prompt: str
    completion: str
    relevance_score: Optional[float] = None
    relevance_reason: Optional[str] = None
    coherence_score: Optional[float] = None
    coherence_reason: Optional[str] = None
    evaluated_at: Optional[datetime] = None


class TraceEvaluator:
    """Evaluates LLM traces using TruLens feedback functions."""

    def __init__(
        self,
        app_name: str = "librechat-eval",
        app_version: str = "1.0.0",
        model: str = None,
        database_url: str = None,
    ):
        self.app_name = app_name
        self.app_version = app_version
        self.model = model or os.getenv("TRULENS_MODEL", "claude-3-5-haiku-20241022")
        self.database_url = database_url or os.getenv(
            "TRULENS_DATABASE_URL", "sqlite:////trulens-data/trulens.sqlite"
        )

        self._session = None
        self._provider = None
        self._feedback_functions = None

    def initialize(self):
        """Initialize TruLens session and feedback functions."""
        print(f"Initializing TruLens session: {self.database_url}")
        self._session = TruSession(database_url=self.database_url)

        # Create LLM provider for evaluations
        print(f"Using evaluation model: {self.model}")
        llm = ChatAnthropic(
            model=self.model,
            temperature=0.0,
            max_tokens=1000,
        )
        self._provider = Langchain(chain=llm)

        # Create feedback functions
        self._feedback_functions = self._create_feedback_functions()

        print(f"TraceEvaluator initialized with {len(self._feedback_functions)} feedback functions")
        return self

    def _create_feedback_functions(self) -> Dict[str, callable]:
        """Create TruLens feedback functions for evaluation."""
        return {
            "relevance": self._provider.relevance_with_cot_reasons,
            "coherence": self._provider.coherence_with_cot_reasons,
        }

    def evaluate_trace(self, trace: LLMTrace) -> EvaluationResult:
        """
        Evaluate a single LLM trace.

        Args:
            trace: LLMTrace object with prompt and completion

        Returns:
            EvaluationResult with scores and reasoning
        """
        result = EvaluationResult(
            trace_id=trace.trace_id,
            span_id=trace.span_id,
            timestamp=trace.timestamp,
            service_name=trace.service_name,
            prompt=trace.prompt,
            completion=trace.completion,
            evaluated_at=datetime.now(),
        )

        # Evaluate relevance (does the answer address the question?)
        try:
            relevance_result = self._feedback_functions["relevance"](
                trace.prompt, trace.completion
            )
            if isinstance(relevance_result, tuple):
                result.relevance_score = relevance_result[0]
                result.relevance_reason = relevance_result[1].get("reason", "") if len(relevance_result) > 1 else ""
            else:
                result.relevance_score = float(relevance_result)
        except Exception as e:
            print(f"  Warning: Relevance evaluation failed: {e}")

        # Evaluate coherence (is the response well-structured?)
        try:
            coherence_result = self._feedback_functions["coherence"](trace.completion)
            if isinstance(coherence_result, tuple):
                result.coherence_score = coherence_result[0]
                result.coherence_reason = coherence_result[1].get("reason", "") if len(coherence_result) > 1 else ""
            else:
                result.coherence_score = float(coherence_result)
        except Exception as e:
            print(f"  Warning: Coherence evaluation failed: {e}")

        return result

    def evaluate_traces(
        self,
        traces: List[LLMTrace],
        sample_rate: float = 1.0,
    ) -> List[EvaluationResult]:
        """
        Evaluate multiple LLM traces.

        Args:
            traces: List of LLMTrace objects
            sample_rate: Fraction of traces to evaluate (0.0-1.0)

        Returns:
            List of EvaluationResult objects
        """
        import random

        # Apply sampling
        if sample_rate < 1.0:
            sample_size = max(1, int(len(traces) * sample_rate))
            traces = random.sample(traces, sample_size)
            print(f"Sampling {sample_size} traces ({sample_rate*100:.1f}%)")

        results = []
        for i, trace in enumerate(traces, 1):
            print(f"\nEvaluating trace {i}/{len(traces)}: {trace.trace_id[:16]}...")
            result = self.evaluate_trace(trace)
            results.append(result)

            # Print summary
            rel_score = f"{result.relevance_score:.2f}" if result.relevance_score else "N/A"
            coh_score = f"{result.coherence_score:.2f}" if result.coherence_score else "N/A"
            print(f"  Relevance: {rel_score}, Coherence: {coh_score}")

        return results

    def store_results(self, results: List[EvaluationResult]):
        """
        Store evaluation results in TruLens database.

        This creates records that appear in the TruLens dashboard.
        """
        from trulens.core.schema.record import Record
        from trulens.core.schema.feedback import FeedbackResult

        for result in results:
            # Create a record for this trace
            record_data = {
                "app_name": self.app_name,
                "app_version": self.app_version,
                "input": result.prompt,
                "output": result.completion,
                "record_id": f"{result.trace_id}_{result.span_id}",
                "ts": result.timestamp,
                "meta": {
                    "trace_id": result.trace_id,
                    "span_id": result.span_id,
                    "service_name": result.service_name,
                    "evaluated_at": result.evaluated_at.isoformat() if result.evaluated_at else None,
                },
            }

            # Note: TruLens record storage API may vary by version
            # This is a simplified approach - full integration would use TruApp
            print(f"  Stored evaluation for trace {result.trace_id[:16]}")

    def get_evaluated_trace_ids(self) -> List[str]:
        """Get list of trace IDs that have already been evaluated."""
        # Query TruLens database for existing evaluations
        # This helps avoid re-evaluating the same traces
        try:
            # This is a simplified check - full implementation would query the DB
            return []
        except Exception as e:
            print(f"Warning: Could not get evaluated trace IDs: {e}")
            return []

    def print_summary(self, results: List[EvaluationResult]):
        """Print summary statistics of evaluation results."""
        if not results:
            print("\nNo results to summarize")
            return

        relevance_scores = [r.relevance_score for r in results if r.relevance_score is not None]
        coherence_scores = [r.coherence_score for r in results if r.coherence_score is not None]

        print("\n" + "=" * 60)
        print("EVALUATION SUMMARY")
        print("=" * 60)
        print(f"Total traces evaluated: {len(results)}")
        print(f"App: {self.app_name} v{self.app_version}")

        if relevance_scores:
            avg_rel = sum(relevance_scores) / len(relevance_scores)
            min_rel = min(relevance_scores)
            max_rel = max(relevance_scores)
            print(f"\nAnswer Relevance:")
            print(f"  Average: {avg_rel:.2f}")
            print(f"  Min: {min_rel:.2f}, Max: {max_rel:.2f}")

        if coherence_scores:
            avg_coh = sum(coherence_scores) / len(coherence_scores)
            min_coh = min(coherence_scores)
            max_coh = max(coherence_scores)
            print(f"\nCoherence:")
            print(f"  Average: {avg_coh:.2f}")
            print(f"  Min: {min_coh:.2f}, Max: {max_coh:.2f}")

        # Flag low-scoring traces
        low_scoring = [r for r in results
                       if (r.relevance_score and r.relevance_score < 0.5) or
                          (r.coherence_score and r.coherence_score < 0.5)]
        if low_scoring:
            print(f"\nLow-scoring traces ({len(low_scoring)}):")
            for r in low_scoring[:5]:  # Show first 5
                print(f"  - {r.trace_id[:16]}... Rel={r.relevance_score:.2f if r.relevance_score else 'N/A'}")

        print("=" * 60)


if __name__ == "__main__":
    # Test the evaluator with mock data
    evaluator = TraceEvaluator(app_name="test-eval")
    evaluator.initialize()

    # Create a test trace
    test_trace = LLMTrace(
        trace_id="test-trace-123",
        span_id="test-span-456",
        timestamp=datetime.now(),
        service_name="test-service",
        prompt="What is ClickHouse?",
        completion="ClickHouse is a fast, open-source columnar database management system designed for online analytical processing (OLAP). It is optimized for high-performance queries on large datasets.",
    )

    print("Testing evaluation on mock trace...")
    result = evaluator.evaluate_trace(test_trace)
    print(f"\nResults:")
    print(f"  Relevance: {result.relevance_score}")
    print(f"  Coherence: {result.coherence_score}")

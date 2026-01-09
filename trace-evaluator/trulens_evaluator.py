"""
TruLens Evaluator for LLM traces.

Runs quality evaluations on extracted LLM traces using LLM-as-a-Judge
and stores results in TruLens database for dashboard viewing.

Uses TruVirtual to ingest pre-existing prompt/completion pairs as
virtual records for evaluation.

Emits OTEL spans for each evaluation with clear model attribution:
- gen_ai.request.model: The evaluator/judge model (e.g., claude-3-5-haiku)
- eval.source_model: The original generation model being evaluated
- eval.source_trace_id: Link back to the original trace
"""

import os
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime

from opentelemetry.trace import Status, StatusCode

from trulens.core import TruSession, Feedback
from trulens.apps.virtual import TruVirtual, VirtualRecord, VirtualApp
from trulens.providers.langchain import Langchain
from langchain_anthropic import ChatAnthropic

from clickhouse_client import LLMTrace
from instrumentation import get_tracer, create_span_link


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
    coherence_score: Optional[float] = None
    evaluated_at: Optional[datetime] = None


class TraceEvaluator:
    """Evaluates LLM traces using TruLens feedback functions."""

    def __init__(
        self,
        app_name: str = "trace-eval",
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
        self._feedbacks = None
        self._virtual_recorder = None

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
        self._feedbacks = self._create_feedbacks()

        # Create virtual app for recording traces
        virtual_app = VirtualApp()

        # Create virtual recorder - will be used to ingest all traces
        self._virtual_recorder = TruVirtual(
            app=virtual_app,
            app_name=self.app_name,
            app_version=self.app_version,
            feedbacks=self._feedbacks,
        )

        print(f"TraceEvaluator initialized with {len(self._feedbacks)} feedback functions")
        return self

    def _create_feedbacks(self) -> List[Feedback]:
        """Create TruLens Feedback objects for evaluation."""
        # Answer Relevance: Does the response address the question?
        # on_input() = main_input (prompt), on_output() = main_output (completion)
        f_relevance = Feedback(
            self._provider.relevance_with_cot_reasons,
            name="Answer Relevance"
        ).on_input().on_output()

        # Coherence: Is the response well-structured and coherent?
        f_coherence = Feedback(
            self._provider.coherence_with_cot_reasons,
            name="Coherence"
        ).on_output()

        return [f_relevance, f_coherence]

    def evaluate_trace(self, trace: LLMTrace) -> EvaluationResult:
        """
        Evaluate a single LLM trace and store in TruLens.

        Emits an OTEL span with:
        - Link to the original trace being evaluated
        - gen_ai.request.model: The judge model used for evaluation
        - eval.source_model: The original model that generated the response
        - eval.* scores: The evaluation results

        Args:
            trace: LLMTrace object with prompt and completion

        Returns:
            EvaluationResult with scores
        """
        tracer = get_tracer()

        # Create span link to original trace
        links = []
        span_link = create_span_link(trace.trace_id, trace.span_id)
        if span_link:
            links.append(span_link)

        # Start evaluation span with link to original trace
        with tracer.start_as_current_span(
            "llm.evaluation",
            links=links,
        ) as span:
            # Set model attribution attributes
            # gen_ai.request.model = the judge/evaluator model
            span.set_attribute("gen_ai.request.model", self.model)
            span.set_attribute("gen_ai.system", "anthropic")

            # eval.source_* = info about what's being evaluated
            span.set_attribute("eval.source_trace_id", trace.trace_id)
            span.set_attribute("eval.source_span_id", trace.span_id)
            span.set_attribute("eval.source_service", trace.service_name)
            if trace.model:
                span.set_attribute("eval.source_model", trace.model)

            # Store prompt/completion being evaluated (truncated for span size)
            span.set_attribute("eval.input", trace.prompt[:1000] if len(trace.prompt) > 1000 else trace.prompt)
            span.set_attribute("eval.output", trace.completion[:1000] if len(trace.completion) > 1000 else trace.completion)

            try:
                # Create a virtual record from the trace
                # calls={} is required but can be empty for simple input/output evaluation
                virtual_record = VirtualRecord(
                    main_input=trace.prompt,
                    main_output=trace.completion,
                    calls={},
                )

                # Add the record to TruVirtual - this runs feedback evaluations
                record = self._virtual_recorder.add_record(virtual_record)

                # Wait for feedback to complete and extract scores
                relevance_score = None
                coherence_score = None

                for feedback, result in record.wait_for_feedback_results().items():
                    if "Relevance" in feedback.name:
                        relevance_score = result.result
                    elif "Coherence" in feedback.name:
                        coherence_score = result.result

                # Add scores to span
                if relevance_score is not None:
                    span.set_attribute("eval.relevance_score", relevance_score)
                if coherence_score is not None:
                    span.set_attribute("eval.coherence_score", coherence_score)

                span.set_status(Status(StatusCode.OK))

                return EvaluationResult(
                    trace_id=trace.trace_id,
                    span_id=trace.span_id,
                    timestamp=trace.timestamp,
                    service_name=trace.service_name,
                    prompt=trace.prompt,
                    completion=trace.completion,
                    relevance_score=relevance_score,
                    coherence_score=coherence_score,
                    evaluated_at=datetime.now(),
                )

            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

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
            try:
                result = self.evaluate_trace(trace)
                results.append(result)

                # Print summary
                rel_score = f"{result.relevance_score:.2f}" if result.relevance_score is not None else "N/A"
                coh_score = f"{result.coherence_score:.2f}" if result.coherence_score is not None else "N/A"
                print(f"  Relevance: {rel_score}, Coherence: {coh_score}")
            except Exception as e:
                print(f"  Error evaluating trace: {e}")

        return results

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
        completion="ClickHouse is a fast, open-source columnar database management system designed for online analytical processing (OLAP).",
    )

    print("Testing evaluation on mock trace...")
    result = evaluator.evaluate_trace(test_trace)
    print(f"\nResults:")
    print(f"  Relevance: {result.relevance_score}")
    print(f"  Coherence: {result.coherence_score}")

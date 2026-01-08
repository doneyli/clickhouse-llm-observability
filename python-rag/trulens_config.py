"""TruLens Evaluation Configuration"""

import os
from typing import List, Optional
from trulens.core import Feedback
from trulens.providers.langchain import Langchain
from trulens.apps.app import instrument
from langchain_anthropic import ChatAnthropic


class TruLensConfig:
    def __init__(
        self,
        app_name: str = "clickhouse-rag-demo",
        app_version: str = "1.0.0",
        model: str = None,
    ):
        self.app_name = app_name
        self.app_version = app_version
        self.model = model or os.getenv("TRULENS_MODEL", "claude-3-5-haiku-20241022")


def create_feedback_functions(config: TruLensConfig = None) -> List[Feedback]:
    """
    Create TruLens feedback functions for RAG evaluation.

    These evaluate:
    - Answer Relevance: Does the answer address the question?
    - Coherence: Is the response well-structured?
    """
    config = config or TruLensConfig()

    # Use LangChain provider with Anthropic model
    llm = ChatAnthropic(model=config.model, temperature=0.0, max_tokens=1000)
    provider = Langchain(chain=llm)

    feedbacks = [
        # Answer Relevance (0-1, higher is better)
        Feedback(provider.relevance_with_cot_reasons, name="Answer Relevance")
            .on_input().on_output(),

        # Coherence (0-1, higher is better)
        Feedback(provider.coherence_with_cot_reasons, name="Coherence")
            .on_output(),
    ]

    return feedbacks


class InstrumentedRAGPipeline:
    """
    RAG Pipeline wrapper with TruLens instrumentation.

    The @instrument decorator marks methods for TruLens tracking.
    """

    def __init__(self, base_pipeline, config: TruLensConfig = None):
        self.pipeline = base_pipeline
        self.config = config or TruLensConfig()

    @instrument
    def retrieve(self, question: str) -> str:
        """Retrieve context - tracked by TruLens."""
        analysis = self.pipeline.analysis_chain.invoke({"question": question})
        return self.pipeline.retrieve_context(question, analysis)

    @instrument
    def generate(self, question: str, context: str) -> str:
        """Generate response - tracked by TruLens."""
        return self.pipeline.response_chain.invoke({
            "question": question,
            "analysis": "",
            "context": context
        })

    @instrument
    def query(self, question: str) -> str:
        """Full RAG query - main TruLens entry point."""
        context = self.retrieve(question)
        return self.generate(question, context)

    @property
    def context(self) -> str:
        """Expose context for groundedness evaluation."""
        return self.pipeline.context

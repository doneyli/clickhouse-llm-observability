"""Text-to-SQL Pipeline with ClickHouse MCP Integration"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser


@dataclass
class SQLConfig:
    model_name: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 2000


# Available databases at sql.clickhouse.com
CLICKHOUSE_DATABASES = """
Available databases include:
- uk_price_paid: UK property transactions
- github_events: GitHub activity data
- opensky: Flight tracking data
- stackoverflow: Stack Overflow posts
- reddit: Reddit posts and comments
- hackernews: Hacker News stories
- wikistat: Wikipedia page views
- youtube: YouTube video metadata
- food_prices: Global food price indices
- nyc_taxi: NYC taxi trip data
- ontime: US flight delay data
- cell_towers: OpenCellID cell tower locations
- crypto_prices: Cryptocurrency prices
"""


class ClickHouseSQLPipeline:
    """Text-to-SQL pipeline that queries ClickHouse via MCP."""

    def __init__(self, config: Optional[SQLConfig] = None):
        self.config = config or SQLConfig(
            model_name=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            temperature=float(os.getenv("TEMPERATURE", "0.7")),
        )
        self._setup_llm()
        self._setup_chains()
        self._context = ""

    def _setup_llm(self):
        self.llm = ChatAnthropic(
            model=self.config.model_name,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

    def _setup_chains(self):
        self.analysis_prompt = ChatPromptTemplate.from_template(
            f"You are a data analyst with access to ClickHouse at sql.clickhouse.com.\n\n"
            f"{CLICKHOUSE_DATABASES}\n\n"
            "Question: {question}\n\n"
            "Identify which database(s) and data would help answer this question."
        )

        self.analysis_chain = (
            self.analysis_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "query_analysis"}})

        self.response_prompt = ChatPromptTemplate.from_template(
            "Based on the analysis and context, answer the question.\n\n"
            "Question: {question}\n"
            "Analysis: {analysis}\n"
            "Context: {context}\n\n"
            "Provide a clear, data-driven response."
        )

        self.response_chain = (
            self.response_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "response_generation"}})

    def retrieve_context(self, question: str, analysis: str) -> str:
        """Retrieve context from ClickHouse via MCP."""
        try:
            from mcp_client import create_mcp_client
            mcp = create_mcp_client()
            self._context = mcp.get_context_for_question(question, analysis)
            return self._context
        except Exception as e:
            self._context = f"[MCP unavailable: {e}]"
            return self._context

    def query(self, question: str, callbacks: list = None) -> str:
        """Execute the full Text-to-SQL pipeline.

        Args:
            question: The user's question
            callbacks: Optional list of LangChain callbacks (e.g., Langfuse handler)
        """
        config = {"callbacks": callbacks} if callbacks else {}

        analysis = self.analysis_chain.invoke({"question": question}, config=config)
        context = self.retrieve_context(question, analysis)
        answer = self.response_chain.invoke({
            "question": question,
            "analysis": analysis,
            "context": context
        }, config=config)
        return answer

    @property
    def context(self) -> str:
        """Expose context for groundedness evaluation."""
        return self._context


def create_pipeline(config: Optional[SQLConfig] = None) -> ClickHouseSQLPipeline:
    return ClickHouseSQLPipeline(config)

"""Text-to-SQL Pipeline with ClickHouse MCP Integration"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langfuse import observe


@dataclass
class SQLConfig:
    model_name: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 2000


# Available databases at sql.clickhouse.com
CLICKHOUSE_DATABASES = """
Available databases include:
- amazon: Amazon product data
- bluesky: Bluesky social network data
- covid: COVID-19 data
- dns: DNS query data
- environmental: Environmental data
- forex: Foreign exchange data
- geo: Geographic data
- git: Git repository data
- github: GitHub events and activities
- hackernews: Hacker News posts and comments
- imdb: Internet Movie Database
- logs: Log data
- mta: Metropolitan Transportation Authority data
- noaa: National Oceanic and Atmospheric Administration data
- nyc_taxi: New York City taxi trip data
- nypd: New York Police Department data
- ontime: Airline on-time performance data
- pypi: Python Package Index data
- stackoverflow: Stack Overflow posts and data
- stock: Stock market data
- twitter: Twitter data
- uk: UK property and related data
- wiki: Wikipedia data
- youtube: YouTube video data
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

    @observe(name="retrieve-context")
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

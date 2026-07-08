"""Text-to-SQL Pipeline with ClickHouse MCP Integration"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langfuse_config import langfuse_span, get_managed_prompt


def _managed_or_fallback(name: str, fallback_template: str) -> ChatPromptTemplate:
    """Build a ChatPromptTemplate from a Langfuse-managed prompt (Deploy node),
    linking the prompt version to the generation, or fall back to the local
    template so the app runs even if Langfuse/the prompt is unavailable.

    Note: get_langchain_prompt() converts Langfuse {{var}} -> LangChain {var},
    so the chain's .invoke(...) variable names are unchanged. Setting .metadata
    AFTER construction is what makes the prompt link attach (passing metadata= to
    from_template does not propagate through the LangChain CallbackHandler)."""
    lf_prompt = get_managed_prompt(name)
    if lf_prompt is not None:
        try:
            tmpl = ChatPromptTemplate.from_template(lf_prompt.get_langchain_prompt())
            tmpl.metadata = {"langfuse_prompt": lf_prompt}
            return tmpl
        except Exception as e:  # pragma: no cover - defensive
            print(f"Managed prompt '{name}' unusable ({e}); using local fallback.")
    return ChatPromptTemplate.from_template(fallback_template)


@dataclass
class SQLConfig:
    model_name: str = "claude-sonnet-4-6"
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
            model_name=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
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
        # Prompts are Langfuse-managed (fetched by label=production at startup)
        # with the inline templates below as local fallbacks — the Deploy node.
        self.analysis_prompt = _managed_or_fallback(
            "text-to-sql-analysis",
            f"You are a data analyst with access to ClickHouse at sql.clickhouse.com.\n\n"
            f"{CLICKHOUSE_DATABASES}\n\n"
            "Question: {question}\n\n"
            "Identify which database(s) and data would help answer this question."
        )

        self.analysis_chain = (
            self.analysis_prompt | self.llm | StrOutputParser()
        ).with_config({"metadata": {"purpose": "query_analysis"}})

        self.response_prompt = _managed_or_fallback(
            "text-to-sql-response",
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
        with langfuse_span("retrieve-context"):
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

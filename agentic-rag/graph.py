"""
Agentic RAG — a CRAG-style LangGraph agent.

Flow:

    START
      |
    route ──► direct ───────────────────────────────► generate ─► reflect ─► END
      |                                                   ▲           │
      ├──► sql_tool ─────────────────────────────────────┤           │ (not grounded
      |                                                   │           │  & budget left)
      └──► retrieve ─► grade ─┬─ relevant ────────────────┘           ▼
              ▲               └─ not relevant ─► rewrite ─► (loop)   generate
              └───────────────────────────────────────────┘

Patterns demonstrated:
- Query routing (KB vs live SQL vs direct answer)
- Retrieval grading (is the retrieved context actually relevant?)
- Self-correction: query rewrite + re-retrieve (bounded)
- Tool use: ClickHouse SQL over public demo datasets
- Reflection: groundedness self-check + bounded regeneration

Each node is instrumented with a typed Langfuse observation so the trace renders
as an Agent Graph with RAG-aware semantics (retriever / tool / evaluator).
"""

import os
from typing import List, Optional, TypedDict

from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph, START, END

from clickhouse_store import ClickHouseVectorStore
from embeddings import embed_query
from sql_tool import run_select
import langfuse_config as lf

MAX_RETRIEVE_ATTEMPTS = 2  # initial + one rewrite
MAX_REFLECT_ATTEMPTS = 1   # one regeneration if ungrounded
TOP_K = 4

# Langfuse-managed generation prompt (created via scripts/seed-langfuse-prompt.py)
GEN_PROMPT_NAME = os.getenv("GEN_PROMPT_NAME", "agentic-rag-generation")


class AgentState(TypedDict, total=False):
    question: str
    route: str               # 'kb' | 'sql' | 'direct'
    query: str               # current (possibly rewritten) retrieval query
    chunks: List[dict]
    context: str
    relevant: bool
    retrieve_attempts: int
    sql: str
    sql_result: str
    answer: str
    grounded: bool
    reflect_attempts: int
    trace: List[str]         # human-readable step log for the API/CLI


def _llm() -> ChatAnthropic:
    # timeout + retries so a stalled API call fails fast and self-heals instead
    # of hanging the whole agent run indefinitely.
    return ChatAnthropic(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        temperature=float(os.getenv("TEMPERATURE", "0.3")),
        max_tokens=1200,
        default_request_timeout=float(os.getenv("LLM_TIMEOUT", "45")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )


def _ask(prompt: str) -> str:
    return _llm().invoke(prompt).content.strip()


class AgenticRAG:
    def __init__(self, store: Optional[ClickHouseVectorStore] = None):
        self.store = store or ClickHouseVectorStore()
        self.graph = self._build()

    # ----------------------------------------------------------------- nodes
    def route_node(self, state: AgentState) -> AgentState:
        q = state["question"]
        with lf.observe("route", as_type="agent", input=q) as obs:
            decision = _ask(
                "You route a user question to the best data source. Reply with ONE word:\n"
                "- 'kb'     : conceptual/how-to questions about ClickHouse, RAG, observability, Langfuse, OpenTelemetry\n"
                "- 'sql'    : questions needing live numbers from datasets (taxi rides, github stars, stackoverflow, etc.)\n"
                "- 'direct' : greetings or questions needing no retrieval\n\n"
                f"Question: {q}\nAnswer:"
            ).lower()
            route = "kb"
            if "sql" in decision:
                route = "sql"
            elif "direct" in decision:
                route = "direct"
            if obs:
                obs.update(output={"route": route})
        return {
            "route": route,
            "query": q,
            "retrieve_attempts": 0,
            "reflect_attempts": 0,
            "trace": [f"route → {route}"],
        }

    def retrieve_node(self, state: AgentState) -> AgentState:
        query = state["query"]
        attempts = state.get("retrieve_attempts", 0) + 1
        with lf.observe("retrieve", as_type="retriever", input=query) as obs:
            chunks = self.store.vector_search(embed_query(query), top_k=TOP_K)
            context = "\n\n---\n\n".join(c["chunk"] for c in chunks)
            if obs:
                obs.update(output={"hits": len(chunks),
                                   "titles": [c["doc_title"] for c in chunks]})
        log = state.get("trace", []) + [f"retrieve (attempt {attempts}) → {len(chunks)} chunks"]
        return {"chunks": chunks, "context": context,
                "retrieve_attempts": attempts, "trace": log}

    def grade_node(self, state: AgentState) -> AgentState:
        with lf.observe("grade-relevance", as_type="evaluator") as obs:
            verdict = _ask(
                "You grade whether the CONTEXT is relevant enough to answer the QUESTION. "
                "Reply with one word: 'yes' or 'no'.\n\n"
                f"QUESTION: {state['question']}\n\nCONTEXT:\n{state['context']}\n\nRelevant?"
            ).lower()
            relevant = verdict.startswith("y")
            if obs:
                obs.update(output={"relevant": relevant})
            # Emit a Langfuse Score on this evaluator observation so retrieval
            # quality is aggregatable/chartable. Span-level (not trace-level)
            # because grading can run multiple times when the agent self-corrects.
            attempt = state.get("retrieve_attempts", 0)
            lf.score_current_span(
                "retrieval_relevance",
                1.0 if relevant else 0.0,
                comment=f"attempt {attempt}: context graded {'relevant' if relevant else 'not relevant'}",
            )
        log = state.get("trace", []) + [f"grade → {'relevant' if relevant else 'not relevant'}"]
        return {"relevant": relevant, "trace": log}

    def rewrite_node(self, state: AgentState) -> AgentState:
        with lf.observe("rewrite-query", as_type="agent") as obs:
            new_q = _ask(
                "The previous search query did not retrieve relevant context. "
                "Rewrite it to improve retrieval: expand abbreviations, add synonyms, "
                "be specific. Reply with ONLY the rewritten query.\n\n"
                f"Original question: {state['question']}\n"
                f"Previous query: {state['query']}\nRewritten query:"
            )
            if obs:
                obs.update(output={"rewritten_query": new_q})
        log = state.get("trace", []) + [f"rewrite → '{new_q[:60]}'"]
        return {"query": new_q, "trace": log}

    def sql_node(self, state: AgentState) -> AgentState:
        with lf.observe("sql-tool", as_type="tool", input=state["question"]) as obs:
            sql = _ask(
                "Write a single ClickHouse SELECT to answer the question using the public "
                "demo datasets (e.g. nyc_taxi, github, stackoverflow, hackernews, imdb). "
                "Always add a LIMIT. Reply with ONLY the SQL.\n\n"
                f"Question: {state['question']}\nSQL:"
            )
            # strip markdown fences if present
            sql = sql.replace("```sql", "").replace("```", "").strip()
            result = run_select(sql)
            if obs:
                obs.update(output={"sql": sql, "rows_preview": result[:300]})
        log = state.get("trace", []) + [f"sql-tool → executed query"]
        return {"sql": sql, "sql_result": result,
                "context": f"SQL: {sql}\n\nResult:\n{result}", "trace": log}

    def generate_node(self, state: AgentState) -> AgentState:
        context = state.get("context", "")
        with lf.observe("generate", as_type="generation") as obs:
            if context:
                # Prompt management: pull the generation prompt from Langfuse
                # (versioned, label-routed). Link it to this generation so the
                # trace records which prompt version produced the answer. Falls
                # back to a local template if Langfuse is unavailable.
                prompt_obj = lf.get_prompt(GEN_PROMPT_NAME, label="production")
                text = None
                if prompt_obj is not None:
                    try:
                        text = prompt_obj.compile(context=context, question=state["question"])
                        if obs:
                            obs.update(prompt=prompt_obj)  # links prompt → trace
                    except Exception:
                        text = None
                if text is None:
                    text = (
                        "Answer the question using ONLY the context. If the context is insufficient, "
                        "say what's missing. Be concise and accurate.\n\n"
                        f"Context:\n{context}\n\nQuestion: {state['question']}\n\nAnswer:"
                    )
                answer = _ask(text)
            else:
                answer = _ask(f"Answer concisely.\n\nQuestion: {state['question']}\n\nAnswer:")
            if obs:
                obs.update(output=answer)
        log = state.get("trace", []) + ["generate → drafted answer"]
        return {"answer": answer, "trace": log}

    def reflect_node(self, state: AgentState) -> AgentState:
        attempts = state.get("reflect_attempts", 0) + 1
        context = state.get("context", "")
        # Direct answers have no context to ground against — accept as-is.
        if not context:
            return {"grounded": True, "reflect_attempts": attempts,
                    "trace": state.get("trace", []) + ["reflect → skipped (no context)"]}
        with lf.observe("reflect-groundedness", as_type="evaluator") as obs:
            verdict = _ask(
                "Is every claim in the ANSWER supported by the CONTEXT? "
                "Reply with one word: 'yes' or 'no'.\n\n"
                f"CONTEXT:\n{context}\n\nANSWER:\n{state['answer']}\n\nGrounded?"
            ).lower()
            grounded = verdict.startswith("y")
            if obs:
                obs.update(output={"grounded": grounded})
        lf.score_current_trace("groundedness", 1.0 if grounded else 0.0,
                               comment="agent self-reflection")
        log = state.get("trace", []) + [f"reflect → {'grounded' if grounded else 'NOT grounded'}"]
        return {"grounded": grounded, "reflect_attempts": attempts, "trace": log}

    # ----------------------------------------------------------------- edges
    def _route_edge(self, state: AgentState) -> str:
        return {"kb": "retrieve", "sql": "sql_tool", "direct": "generate"}[state["route"]]

    def _grade_edge(self, state: AgentState) -> str:
        if state["relevant"]:
            return "generate"
        if state.get("retrieve_attempts", 0) < MAX_RETRIEVE_ATTEMPTS:
            return "rewrite"
        return "generate"  # give up correcting; answer with caveat

    def _reflect_edge(self, state: AgentState) -> str:
        if state["grounded"] or state.get("reflect_attempts", 0) >= MAX_REFLECT_ATTEMPTS:
            return END
        return "generate"

    # ----------------------------------------------------------------- build
    def _build(self):
        g = StateGraph(AgentState)
        g.add_node("route", self.route_node)
        g.add_node("retrieve", self.retrieve_node)
        g.add_node("grade", self.grade_node)
        g.add_node("rewrite", self.rewrite_node)
        g.add_node("sql_tool", self.sql_node)
        g.add_node("generate", self.generate_node)
        g.add_node("reflect", self.reflect_node)

        g.add_edge(START, "route")
        g.add_conditional_edges("route", self._route_edge,
                                {"retrieve": "retrieve", "sql_tool": "sql_tool", "generate": "generate"})
        g.add_edge("retrieve", "grade")
        g.add_conditional_edges("grade", self._grade_edge,
                                {"rewrite": "rewrite", "generate": "generate"})
        g.add_edge("rewrite", "retrieve")
        g.add_edge("sql_tool", "generate")
        g.add_edge("generate", "reflect")
        g.add_conditional_edges("reflect", self._reflect_edge,
                                {"generate": "generate", END: END})
        return g.compile()

    # ----------------------------------------------------------------- run
    def run(self, question: str, session_id: Optional[str] = None) -> dict:
        session_id = session_id or lf.new_session_id()
        handler = lf.get_handler()
        config = {"callbacks": [handler]} if handler else {}
        with lf.trace_context("agentic-rag", session_id=session_id):
            final = self.graph.invoke({"question": question}, config=config)
        lf.flush()
        return {
            "question": question,
            "answer": final.get("answer", ""),
            "route": final.get("route"),
            "grounded": final.get("grounded"),
            "steps": final.get("trace", []),
            "session_id": session_id,
        }


def create_agent() -> AgenticRAG:
    return AgenticRAG()

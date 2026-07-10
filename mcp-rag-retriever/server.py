"""
MCP server exposing ClickHouse-native vector retrieval as tools.

Lets LibreChat agents (and any MCP client) reach the SAME agentic-rag pipeline
the standalone demo uses, in two granularities:

  * retrieve_kb / list_documents — raw semantic search over the kb_chunks table
    (ClickHouse native vector_similarity HNSW index). Reuses the agentic-rag
    store/embeddings modules (mounted read-only at /agentic-rag) so there is a
    single source of truth for retrieval logic.
  * agentic_rag_answer — runs the FULL self-correcting LangGraph
    (route -> retrieve -> grade -> self-correct -> generate -> reflect) by
    calling the agentic-rag FastAPI service. This is what produces a fully-scored
    `agentic-rag` Langfuse trace (retrieval_relevance, groundedness, and the
    managed faithfulness / context-relevance / answer-relevance judges), so a
    LibreChat-driven answer is evaluated identically to a seeded one.

Transport: SSE on :8000 (matches the other mcp-clickhouse SSE servers), so
LibreChat connects via  http://mcp-rag-retriever:8000/sse
"""

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/agentic-rag")

from mcp.server.fastmcp import FastMCP

from clickhouse_store import ClickHouseVectorStore
from embeddings import embed_query

# Full agentic-rag graph lives in the `agentic-rag` service (FastAPI on :8000).
AGENTIC_RAG_URL = os.getenv("AGENTIC_RAG_URL", "http://agentic-rag:8000")

mcp = FastMCP("rag-retriever", host="0.0.0.0", port=8000)
_store = ClickHouseVectorStore()


@mcp.tool()
def retrieve_kb(query: str, top_k: int = 4, doc_title: str = "") -> list:
    """Semantic search over the knowledge base using ClickHouse native vector search.

    Args:
        query: Natural-language query to embed and match.
        top_k: Number of chunks to return (default 4).
        doc_title: Optional exact document-title filter (hybrid: vector + metadata).

    Returns:
        A list of {doc_title, chunk, distance} ordered by cosine distance ascending.
    """
    hits = _store.vector_search(
        embed_query(query), top_k=top_k, title_filter=doc_title or None
    )
    return [
        {"doc_title": h["doc_title"], "chunk": h["chunk"], "distance": round(h["distance"], 4)}
        for h in hits
    ]


@mcp.tool()
def list_documents() -> list:
    """List the distinct document titles available in the knowledge base."""
    result = _store.client.query(
        f"SELECT DISTINCT doc_title FROM {_store.config.qualified_table} ORDER BY doc_title"
    )
    return [row[0] for row in result.result_rows]


@mcp.tool()
def agentic_rag_answer(question: str, session_id: str = "") -> dict:
    """Answer a knowledge-base question with the FULL self-correcting agentic-RAG graph.

    Runs the complete LangGraph corrective-RAG pipeline in the agentic-rag
    service — route -> retrieve -> grade -> self-correct (rewrite + re-retrieve)
    -> generate -> reflect. Unlike `retrieve_kb` (which only returns raw chunks
    for you to reason over yourself), this executes the graded, self-correcting
    loop server-side and emits a fully-scored `agentic-rag` Langfuse trace
    (retrieval_relevance, groundedness, faithfulness, context-relevance,
    answer-relevance).

    Use THIS tool to answer knowledge/concept/how-to questions; use `retrieve_kb`
    / `list_documents` only when the user explicitly wants to inspect raw
    retrieval.

    Args:
        question: The natural-language question to answer from the knowledge base.
        session_id: Optional session id to group related turns in one Langfuse session.

    Returns:
        {question, answer, route, grounded, steps, session_id} from the graph,
        or {error: ...} if the agentic-rag service is unreachable.
    """
    payload = json.dumps(
        {"question": question, "session_id": session_id or None}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENTIC_RAG_URL}/query",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # The graph can self-correct (multiple LLM calls), so allow a generous timeout.
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {
            "error": f"agentic-rag service unreachable at {AGENTIC_RAG_URL}: {e}. "
            "Ensure the `agentic-rag` container is running (docker compose --profile demo up -d agentic-rag)."
        }


if __name__ == "__main__":
    mcp.run(transport="sse")

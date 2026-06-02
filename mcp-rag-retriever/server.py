"""
MCP server exposing ClickHouse-native vector retrieval as a tool.

Lets LibreChat agents (and any MCP client) run the SAME retrieval the LangGraph
agentic-rag service uses — semantic search over the kb_chunks table backed by
ClickHouse's native vector_similarity HNSW index.

Reuses the agentic-rag store/embeddings modules (mounted read-only at
/agentic-rag) so there is a single source of truth for retrieval logic.

Transport: SSE on :8000 (matches the other mcp-clickhouse SSE servers), so
LibreChat connects via  http://mcp-rag-retriever:8000/sse
"""

import sys

sys.path.insert(0, "/agentic-rag")

from mcp.server.fastmcp import FastMCP

from clickhouse_store import ClickHouseVectorStore
from embeddings import embed_query

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


if __name__ == "__main__":
    mcp.run(transport="sse")

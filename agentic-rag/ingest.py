"""
Ingest the demo knowledge base into ClickHouse-native vector storage.

Reuses the existing vector-rag corpus (single source of truth) so the Agentic
RAG demo and the naive vector-rag demo answer from identical documents — making
the "naive vs agentic" comparison fair.

Run:
    docker compose --profile demo run --rm agentic-rag python ingest.py

Idempotent: truncates and re-inserts on each run.
"""

import sys

# The vector-rag corpus is mounted read-only at /shared-corpus (see compose).
sys.path.insert(0, "/shared-corpus")

from langchain_text_splitters import RecursiveCharacterTextSplitter

from clickhouse_store import ClickHouseVectorStore
from embeddings import embed_documents

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _load_corpus():
    """Load (chunk_text, doc_title) pairs from the shared vector-rag corpus."""
    try:
        from documents import get_documents, get_document_metadata
    except ImportError:
        print("ERROR: vector-rag corpus not found on /shared-corpus.")
        print("Ensure ./vector-rag is mounted (see docker-compose 'agentic-rag' volumes).")
        raise

    documents = get_documents()
    metadata = get_document_metadata()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )

    pairs = []
    for doc, meta in zip(documents, metadata):
        for chunk in splitter.split_text(doc):
            pairs.append((chunk, meta.get("title", "")))
    return pairs


def main():
    print("Agentic RAG — ClickHouse vector ingest")
    print("=" * 60)

    store = ClickHouseVectorStore()
    print(f"Connecting to ClickHouse at {store.config.host}:{store.config.port} ...")
    store.ensure_schema()
    print(f"Schema ready: {store.config.qualified_table} (HNSW cosineDistance index)")

    pairs = _load_corpus()
    chunks = [p[0] for p in pairs]
    print(f"Embedding {len(chunks)} chunks with all-MiniLM-L6-v2 ...")
    vectors = embed_documents(chunks)

    rows = [
        {"id": i, "doc_title": title, "chunk": chunk, "embedding": vec}
        for i, ((chunk, title), vec) in enumerate(zip(pairs, vectors))
    ]

    store.reset()
    store.insert_chunks(rows)
    total = store.count()
    print(f"Inserted {total} chunks.")

    # Verification: native ANN query proving the index path works.
    print("\nVerification — nearest neighbors for: 'How does ClickHouse store data?'")
    from embeddings import embed_query

    hits = store.vector_search(embed_query("How does ClickHouse store data?"), top_k=3)
    for h in hits:
        print(f"  dist={h['distance']:.4f}  [{h['doc_title']}]  {h['chunk'][:70]}...")

    print("\nIngest complete.")


if __name__ == "__main__":
    main()

"""
ClickHouse-native vector store for the Agentic RAG demo.

Uses ClickHouse's native approximate vector search (the `vector_similarity`
HNSW index, GA-adjacent in 25.8+, paired with the 26.2 GA text index and
QBit data type) instead of a separate vector database. This keeps vectors,
metadata, and (via Langfuse) observability data all in ClickHouse.

Reference: https://clickhouse.com/docs/engines/table-engines/mergetree-family/annindexes
- Index: TYPE vector_similarity('hnsw', <distance>, <dims>[, <quantization>, ...])
- Distances: L2Distance | cosineDistance | dotProduct
- Query:   ORDER BY cosineDistance(embedding, <ref>) ASC LIMIT N
- On 26.x no experimental flag is required (compatibility >= '25.1').
"""

import os
from dataclasses import dataclass
from typing import List, Dict, Optional

import clickhouse_connect


EMBED_DIM = 384  # all-MiniLM-L6-v2


@dataclass
class StoreConfig:
    host: str = os.getenv("CH_VECTORS_HOST", "clickhouse-vectors")
    port: int = int(os.getenv("CH_VECTORS_PORT", "8123"))
    user: str = os.getenv("CH_VECTORS_USER", "default")
    password: str = os.getenv("CH_VECTORS_PASSWORD", "")
    database: str = os.getenv("CH_VECTORS_DB", "agentic_rag")
    table: str = "kb_chunks"

    @property
    def qualified_table(self) -> str:
        return f"{self.database}.{self.table}"


class ClickHouseVectorStore:
    """Thin wrapper around clickhouse-connect for native vector search."""

    def __init__(self, config: Optional[StoreConfig] = None):
        self.config = config or StoreConfig()
        # Connect to the `default` database first so we can create ours.
        self.client = clickhouse_connect.get_client(
            host=self.config.host,
            port=self.config.port,
            username=self.config.user,
            password=self.config.password,
        )

    # ------------------------------------------------------------------ schema
    def ensure_schema(self) -> None:
        """Create the database + table with a native HNSW vector index.

        The `vector_similarity` index uses cosineDistance, which pairs well with
        L2-normalized embeddings. GRANULARITY is left at the ClickHouse default
        (100M) per the docs' recommendation to prefer large granularity.
        """
        cfg = self.config
        self.client.command(f"CREATE DATABASE IF NOT EXISTS {cfg.database}")
        self.client.command(
            f"""
            CREATE TABLE IF NOT EXISTS {cfg.qualified_table} (
                id          UInt64,
                doc_title   String,
                chunk       String,
                embedding   Array(Float32),
                INDEX vec_idx embedding
                    TYPE vector_similarity('hnsw', 'cosineDistance', {EMBED_DIM})
            )
            ENGINE = MergeTree
            ORDER BY id
            """
        )

    # ------------------------------------------------------------------ ingest
    def count(self) -> int:
        return int(self.client.command(f"SELECT count() FROM {self.config.qualified_table}"))

    def reset(self) -> None:
        """Idempotent re-ingest: clear existing rows."""
        self.client.command(f"TRUNCATE TABLE IF EXISTS {self.config.qualified_table}")

    def insert_chunks(self, rows: List[Dict]) -> None:
        """Insert chunk rows. Each row: {id, doc_title, chunk, embedding}."""
        data = [[r["id"], r["doc_title"], r["chunk"], r["embedding"]] for r in rows]
        self.client.insert(
            self.config.qualified_table,
            data,
            column_names=["id", "doc_title", "chunk", "embedding"],
        )

    # ------------------------------------------------------------------ search
    @staticmethod
    def _vec_literal(vec: List[float]) -> str:
        # Inline numeric array literal — safe for floats, avoids param typing quirks.
        return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

    def vector_search(
        self,
        query_embedding: List[float],
        top_k: int = 4,
        title_filter: Optional[str] = None,
    ) -> List[Dict]:
        """Native ANN search via the HNSW vector_similarity index.

        Optionally pre-filter by document title — this is the ClickHouse
        advantage: combine vector similarity with ordinary SQL/metadata filters
        (and, with the 26.2 text index, full-text) in a single query.
        """
        cfg = self.config
        params: Dict = {}
        where = ""
        if title_filter:
            where = "WHERE doc_title = {title_filter:String}"
            params["title_filter"] = title_filter
        sql = f"""
            SELECT
                id,
                doc_title,
                chunk,
                cosineDistance(embedding, {self._vec_literal(query_embedding)}) AS distance
            FROM {cfg.qualified_table}
            {where}
            ORDER BY distance ASC
            LIMIT {int(top_k)}
        """
        result = self.client.query(sql, parameters=params)
        cols = result.column_names
        return [dict(zip(cols, row)) for row in result.result_rows]

    def keyword_search(self, terms: List[str], top_k: int = 4) -> List[Dict]:
        """Lexical fallback used by hybrid retrieval (token match on chunk).

        Works without the text index; with the 26.2 GA `text` index this same
        predicate is index-accelerated.
        """
        cfg = self.config
        if not terms:
            return []
        params = {f"t{i}": t for i, t in enumerate(terms)}
        conds = " OR ".join(
            f"positionCaseInsensitive(chunk, {{t{i}:String}}) > 0" for i in range(len(terms))
        )
        sql = f"""
            SELECT id, doc_title, chunk
            FROM {cfg.qualified_table}
            WHERE {conds}
            LIMIT {int(top_k)}
        """
        result = self.client.query(sql, parameters=params)
        cols = result.column_names
        return [dict(zip(cols, row)) for row in result.result_rows]

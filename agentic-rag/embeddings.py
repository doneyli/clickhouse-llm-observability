"""Shared embedding model for the Agentic RAG demo.

Uses the same model as the existing vector-rag demo (all-MiniLM-L6-v2, 384-dim,
L2-normalized) so the two demos are directly comparable.
"""

import os
from functools import lru_cache
from typing import List

EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")


@lru_cache(maxsize=1)
def _model():
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=EMBED_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def embed_query(text: str) -> List[float]:
    return _model().embed_query(text)


def embed_documents(texts: List[str]) -> List[List[float]]:
    return _model().embed_documents(texts)

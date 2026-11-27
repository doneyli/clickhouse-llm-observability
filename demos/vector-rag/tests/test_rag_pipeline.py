"""rag_pipeline.py with the heavy dependencies stubbed: prompt choice (managed, fallback,
unusable managed), the config, chunking and indexing, retrieval and the generate/query
plumbing. LangChain, ChromaDB, sentence-transformers and torch are replaced by small fakes
in sys.modules, so `pip install pytest` is all the tests need. No model calls."""
from __future__ import annotations

import importlib
import sys
import types

import pytest

import documents


# --- Fakes for the imports at the top of rag_pipeline.py --------------------------------

class Runnable:
    """Enough of LangChain's runnable protocol for `prompt | llm | parser` and .with_config."""

    def __init__(self, parts=None):
        self.parts = parts or [self]
        self.config = None
        self.invocations = []

    def __or__(self, other):
        return Runnable(self.parts + [other])

    def with_config(self, config):
        self.config = config
        return self

    def invoke(self, inputs, config=None):
        self.invocations.append((inputs, config))
        return f"answer to {inputs['question']}"


class ChatPromptTemplate(Runnable):
    def __init__(self, template):
        super().__init__()
        self.template = template
        self.metadata = None

    @classmethod
    def from_template(cls, template):
        if "BROKEN" in template:
            raise ValueError("unbalanced braces")
        return cls(template)


class ChatAnthropic(Runnable):
    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs


class StrOutputParser(Runnable):
    pass


class HuggingFaceEmbeddings:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class Doc:
    def __init__(self, page_content):
        self.page_content = page_content


class Retriever:
    def __init__(self, store, search_kwargs):
        self.store, self.search_kwargs = store, search_kwargs

    def invoke(self, question):
        return [Doc(t) for t in self.store.texts[: self.search_kwargs["k"]]]


class Chroma:
    last = None

    @classmethod
    def from_texts(cls, texts, embedding, metadatas, collection_name):
        store = cls()
        store.texts, store.embedding, store.metadatas, store.collection_name = (
            texts, embedding, metadatas, collection_name)
        cls.last = store
        return store

    def as_retriever(self, search_kwargs):
        return Retriever(self, search_kwargs)


class RecursiveCharacterTextSplitter:
    def __init__(self, chunk_size, chunk_overlap):
        self.chunk_size, self.chunk_overlap = chunk_size, chunk_overlap

    def split_text(self, text):
        step = self.chunk_size - self.chunk_overlap
        return [text[i:i + self.chunk_size] for i in range(0, len(text), step)]


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


@pytest.fixture
def rp(monkeypatch):
    """rag_pipeline imported against the fakes, with Langfuse switched off."""
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "ANTHROPIC_MODEL", "TEMPERATURE"):
        monkeypatch.delenv(k, raising=False)
    fakes = {
        "langchain_anthropic": _module("langchain_anthropic", ChatAnthropic=ChatAnthropic),
        "langchain_huggingface": _module("langchain_huggingface", HuggingFaceEmbeddings=HuggingFaceEmbeddings),
        "langchain_chroma": _module("langchain_chroma", Chroma=Chroma),
        "langchain_core": _module("langchain_core"),
        "langchain_core.prompts": _module("langchain_core.prompts", ChatPromptTemplate=ChatPromptTemplate),
        "langchain_core.output_parsers": _module("langchain_core.output_parsers", StrOutputParser=StrOutputParser),
        "langchain_text_splitters": _module("langchain_text_splitters",
                                            RecursiveCharacterTextSplitter=RecursiveCharacterTextSplitter),
    }
    for name, mod in fakes.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for name in ("rag_pipeline", "langfuse_config"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module("rag_pipeline")


class ManagedPrompt:
    def __init__(self, text):
        self.text = text

    def get_langchain_prompt(self):
        return self.text


# --- Prompt choice ------------------------------------------------------------------------

def test_no_managed_prompt_uses_the_inline_fallback(rp, monkeypatch):
    monkeypatch.setattr(rp, "get_managed_prompt", lambda name: None)
    tmpl = rp._managed_or_fallback("vector-rag-generation", "local {context} {question}")
    assert tmpl.template == "local {context} {question}"
    assert tmpl.metadata is None  # nothing to link


def test_managed_prompt_is_used_and_linked_to_the_generation(rp, monkeypatch):
    managed = ManagedPrompt("managed {context} {question}")
    asked = []
    monkeypatch.setattr(rp, "get_managed_prompt", lambda name: asked.append(name) or managed)
    tmpl = rp._managed_or_fallback("vector-rag-generation", "local")
    assert asked == ["vector-rag-generation"]
    assert tmpl.template == "managed {context} {question}"
    assert tmpl.metadata == {"langfuse_prompt": managed}  # what links the version in Act 1 and 2


def test_unusable_managed_prompt_falls_back(rp, monkeypatch, capsys):
    monkeypatch.setattr(rp, "get_managed_prompt", lambda name: ManagedPrompt("BROKEN {"))
    tmpl = rp._managed_or_fallback("vector-rag-generation", "local {question}")
    assert tmpl.template == "local {question}" and tmpl.metadata is None
    assert "using local fallback" in capsys.readouterr().out


# --- Config and indexing --------------------------------------------------------------

def test_rag_config_defaults(rp):
    c = rp.RAGConfig()
    assert (c.model_name, c.embedding_model) == ("claude-sonnet-4-6", "all-MiniLM-L6-v2")
    assert (c.temperature, c.max_tokens) == (0.7, 1500)
    assert (c.chunk_size, c.chunk_overlap, c.top_k) == (500, 50, 3)


def test_model_and_temperature_come_from_the_environment(rp, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-model")
    monkeypatch.setenv("TEMPERATURE", "0.1")
    p = rp.create_pipeline()
    assert p.llm.kwargs == {"model": "claude-test-model", "temperature": 0.1, "max_tokens": 1500}


def test_indexing_chunks_every_document_and_keeps_its_title(rp):
    p = rp.create_pipeline()
    store = Chroma.last
    assert store.collection_name == "rag_demo"
    assert store.embedding is p.embeddings
    assert p.embeddings.kwargs == {"model_name": "all-MiniLM-L6-v2", "model_kwargs": {"device": "cpu"},
                                   "encode_kwargs": {"normalize_embeddings": True}}
    assert len(store.texts) == len(store.metadatas) >= len(documents.DOCUMENTS)
    # every chunk carries the title of the document it was cut from
    by_title = {d["title"]: f"{d['title']}\n\n{d['content']}" for d in documents.DOCUMENTS}
    for text, meta in zip(store.texts, store.metadatas):
        assert text in by_title[meta["title"]]
    assert {m["title"] for m in store.metadatas} == set(by_title)
    assert p.retriever.search_kwargs == {"k": 3}


def test_chain_is_prompt_then_model_then_parser_tagged_with_its_purpose(rp):
    p = rp.create_pipeline()
    kinds = [type(part).__name__ for part in p.response_chain.parts]
    assert kinds == ["ChatPromptTemplate", "ChatAnthropic", "StrOutputParser"]
    assert p.response_chain.config == {"metadata": {"purpose": "rag_generation"}}


# --- Retrieval and generation ------------------------------------------------------------

def test_retrieve_joins_the_top_k_chunks_and_keeps_them_for_inspection(rp):
    p = rp.create_pipeline()
    ctx = p.retrieve("What is ClickHouse?")
    parts = ctx.split("\n\n---\n\n")
    assert len(parts) == 3
    assert parts == Chroma.last.texts[:3]
    assert p.context == ctx


def test_query_passes_question_context_and_callbacks_to_the_chain(rp):
    p = rp.create_pipeline()
    handler = object()
    assert p.query("What is RAG?", callbacks=[handler]) == "answer to What is RAG?"
    inputs, config = p.response_chain.invocations[-1]
    assert inputs == {"question": "What is RAG?", "context": p.context}
    assert config == {"callbacks": [handler]}


def test_generate_without_callbacks_sends_an_empty_config(rp):
    p = rp.create_pipeline()
    p.generate("q?", "ctx")
    assert p.response_chain.invocations[-1] == ({"question": "q?", "context": "ctx"}, {})

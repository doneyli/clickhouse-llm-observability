"""documents.py and main.py's question list: the corpus the demo indexes and asks about.
Pure Python, no services."""
from __future__ import annotations

import ast
from pathlib import Path

import documents

DEMO_DIR = Path(__file__).resolve().parent.parent


def test_corpus_has_ten_titled_documents():
    assert len(documents.DOCUMENTS) == 10
    titles = [d["title"] for d in documents.DOCUMENTS]
    assert len(set(titles)) == len(titles)
    assert all(d["title"].strip() and d["content"].strip() for d in documents.DOCUMENTS)


def test_get_documents_puts_the_title_above_the_content():
    texts = documents.get_documents()
    assert len(texts) == len(documents.DOCUMENTS)
    for text, doc in zip(texts, documents.DOCUMENTS):
        assert text == f"{doc['title']}\n\n{doc['content']}"


def test_metadata_lines_up_with_the_documents():
    meta = documents.get_document_metadata()
    assert meta == [{"title": d["title"]} for d in documents.DOCUMENTS]


def _demo_questions() -> list:
    """main.py's DEMO_QUESTIONS, read without importing main (which builds the pipeline)."""
    tree = ast.parse((DEMO_DIR / "main.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEMO_QUESTIONS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("DEMO_QUESTIONS not found in main.py")


def test_batch_run_asks_ten_distinct_questions():
    questions = _demo_questions()
    assert len(questions) == 10  # DEMO_SCRIPT.md Act 1 and the README say ten
    assert len(set(questions)) == len(questions)
    assert all(q.endswith("?") for q in questions)

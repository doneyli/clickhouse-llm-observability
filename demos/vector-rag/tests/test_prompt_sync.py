"""The inline fallback prompt in rag_pipeline.py and the seeded `vector-rag-generation` text in
scripts/seed-app-prompts.py must say the same thing. When they match, seeding the prompt changes
nothing until someone edits it in Langfuse (Act 2), and an unseeded clone answers the same way.
Reads both files; imports neither the app's dependencies nor Langfuse."""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
SEEDER = DEMO_DIR.parent.parent / "scripts" / "seed-app-prompts.py"


def _fallback_template() -> str:
    """The second argument of _managed_or_fallback("vector-rag-generation", ...)."""
    tree = ast.parse((DEMO_DIR / "rag_pipeline.py").read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_managed_or_fallback"
                and node.args and getattr(node.args[0], "value", None) == "vector-rag-generation"):
            return node.args[1].value
    raise AssertionError("_managed_or_fallback('vector-rag-generation', ...) not found")


def _seeder():
    spec = importlib.util.spec_from_file_location("seed_app_prompts", SEEDER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # module level reads env vars only; main() is not called
    return mod


def langfuse_to_langchain(text: str) -> str:
    """What get_langchain_prompt() does to a text prompt's variables: {{var}} -> {var}."""
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", r"{\1}", text)


def test_seeded_prompt_matches_the_inline_fallback():
    seeded = _seeder().VECTOR_RAG_GENERATION
    assert langfuse_to_langchain(seeded).strip() == _fallback_template().strip()


def test_prompt_uses_exactly_the_variables_the_chain_passes():
    # VectorRAGPipeline.generate() invokes the chain with {"question", "context"}.
    assert set(re.findall(r"\{(\w+)\}", _fallback_template())) == {"context", "question"}
    assert set(re.findall(r"\{\{(\w+)\}\}", _seeder().VECTOR_RAG_GENERATION)) == {"context", "question"}


def test_seeder_ships_it_under_the_production_label():
    entries = [e for e in _seeder().PROMPTS if e[0] == "vector-rag-generation"]
    assert len(entries) == 1
    label = entries[0][3] if len(entries[0]) > 3 else "production"
    assert label == "production"  # get_managed_prompt() fetches label="production"

"""
Langfuse instrumentation for the Agentic RAG demo (v3 SDK).

Two layers of instrumentation:

1. The LangChain/LangGraph CallbackHandler — passed to graph.invoke(). With the
   LangGraph integration, Langfuse renders the Agent Graph automatically.

2. Explicit typed observations (`agent`, `retriever`, `tool`, `evaluator`) via
   `observe()` below. Using a non-`span`/`generation` observation type is what
   makes Langfuse interpret the trace as agentic (and is RAG-aware: `retriever`
   for vector search, `tool` for the SQL tool, `evaluator` for grading/reflection).

Everything is a no-op when Langfuse keys are absent, so the agent still runs.
"""

import os
import uuid
from contextlib import contextmanager
from typing import Optional

LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)


def is_langfuse_enabled() -> bool:
    return LANGFUSE_ENABLED


def get_client():
    if not LANGFUSE_ENABLED:
        return None
    try:
        from langfuse import get_client as _gc
        return _gc()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse client unavailable: {e}")
        return None


def get_handler():
    """LangChain/LangGraph callback handler (drives the Agent Graph)."""
    if not LANGFUSE_ENABLED:
        return None
    try:
        from langfuse.langchain import CallbackHandler
        return CallbackHandler()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse handler unavailable: {e}")
        return None


def new_session_id() -> str:
    return f"agentic-rag-{uuid.uuid4().hex[:8]}"


@contextmanager
def trace_context(name: str, session_id: Optional[str] = None, tags=None, user_id=None):
    """Set trace name / session / tags for everything emitted within the block."""
    if not LANGFUSE_ENABLED:
        yield
        return
    try:
        from langfuse import propagate_attributes
        with propagate_attributes(
            trace_name=name,
            session_id=session_id,
            user_id=user_id,
            tags=tags or ["agentic-rag", "demo"],
        ):
            yield
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace context failed: {e}")
        yield


@contextmanager
def observe(name: str, as_type: str = "span", input=None):
    """Create a typed observation (agent | retriever | tool | evaluator | ...).

    Yields the observation handle (or None). Call `.update(output=...)` on it,
    or use the returned object's helpers. The non-default `as_type` values are
    what trigger Langfuse's agentic graph + RAG-aware semantics.
    """
    client = get_client()
    if client is None:
        yield None
        return
    try:
        with client.start_as_current_observation(as_type=as_type, name=name, input=input) as obs:
            yield obs
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse observation '{name}' failed: {e}")
        yield None


def score_current_trace(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach an evaluation score to the active trace (e.g. groundedness — one per run)."""
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse score '{name}' failed: {e}")


def score_current_span(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach an evaluation score to the active observation/span.

    Use for step-level evaluator verdicts that can occur more than once per trace
    (e.g. retrieval_relevance, scored on each grade-relevance observation), so a
    self-correcting run shows attempt 1 = 0 and attempt 2 = 1.
    """
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_span(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse span score '{name}' failed: {e}")


def get_prompt(name: str, label: str = "production"):
    """Fetch a managed prompt from Langfuse (prompt management).

    Returns the Langfuse prompt object (has .compile(**vars) and links to traces)
    or None if Langfuse is unavailable / the prompt doesn't exist — callers fall
    back to a local template so the agent always runs.
    """
    client = get_client()
    if client is None:
        return None
    try:
        return client.get_prompt(name, label=label)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse get_prompt('{name}') unavailable, using local fallback: {e}")
        return None


def flush():
    client = get_client()
    if client is None:
        return
    try:
        # Use flush() (non-destructive), NOT shutdown(). get_client() returns a
        # process-global singleton and run() calls this after every request; in
        # the FastAPI server the agent is reused, so shutdown() would tear down
        # the singleton and silently drop traces for every subsequent request.
        if hasattr(client, "flush"):
            client.flush()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse flush failed: {e}")

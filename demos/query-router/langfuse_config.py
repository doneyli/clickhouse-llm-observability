"""
Langfuse instrumentation for the Query Router demo (v3 SDK).

Adapted from demos/agentic-rag/langfuse_config.py — the front-door router is a
thin, LangChain-free service (raw `anthropic` SDK + `httpx`), so this module
drops the LangChain CallbackHandler and keeps only the typed-observation
helpers the router needs:

  * observe()            — typed observations (generation | agent | event | ...)
  * score_current_span() — runtime scores on the active observation (router_confidence)
  * score_current_trace()— trace-level scores (handlers may set these when nested)
  * get_prompt()         — managed router prompt (Deploy node), local fallback if absent
  * trace_context()      — sets the stable trace name / session / tags for a run
  * flush()

Everything is a no-op when Langfuse keys are absent, so the router still runs
(and still dispatches to the handler services) with tracing disabled.
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


def new_session_id() -> str:
    return f"query-router-{uuid.uuid4().hex[:8]}"


@contextmanager
def trace_context(name: str, session_id: Optional[str] = None, tags=None, user_id=None):
    """Set trace name / session / tags for everything emitted within the block.

    The router owns the trace: a stable, low-cardinality, verb-first name
    (`route-and-dispatch`) with the question in the trace input, so filtering
    and dashboards work. Nested handlers join this trace and must NOT overwrite
    these attributes (see demos/agentic-rag/graph.py::run trace_context branch).
    """
    if not LANGFUSE_ENABLED:
        yield
        return
    try:
        from langfuse import propagate_attributes
        with propagate_attributes(
            trace_name=name,
            session_id=session_id,
            user_id=user_id,
            tags=tags or ["query-router", "demo"],
        ):
            yield
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace context failed: {e}")
        yield


@contextmanager
def observe(name: str, as_type: str = "span", input=None):
    """Create a typed observation (generation | agent | event | retriever | ...).

    Yields the observation handle (or None). Call `.update(output=...)` on it.
    Using a non-`span`/`generation` type is what triggers Langfuse's agentic
    graph semantics (e.g. `agent` for the dispatch subtree).

    `as_type="event"` is special-cased: v3's start_as_current_observation does
    not accept "event", so we emit a point-in-time `create_event` nested under
    the current span (used for the escalate-to-human HITL signal).
    """
    client = get_client()
    if client is None:
        yield None
        return
    if as_type == "event":
        try:
            ev = client.create_event(name=name, input=input)
            yield ev
        except Exception as e:  # pragma: no cover - defensive
            print(f"Langfuse event '{name}' failed: {e}")
            yield None
        return
    try:
        with client.start_as_current_observation(as_type=as_type, name=name, input=input) as obs:
            yield obs
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse observation '{name}' failed: {e}")
        yield None


def score_current_trace(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach an evaluation score to the active trace."""
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse score '{name}' failed: {e}")


def score_current_span(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach an evaluation score to the active observation/span.

    Used for `router_confidence` — a RUNTIME score (known at execution time),
    scored on the router's own `route-query` generation so it is chartable and
    filterable for dataset curation (sort by confidence ascending).
    """
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_span(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse span score '{name}' failed: {e}")


def get_prompt(name: str, label: str = "production"):
    """Fetch a managed prompt from Langfuse (prompt management / Deploy node).

    Returns the Langfuse prompt object (has .compile(**vars) and links to
    traces) or None if Langfuse is unavailable / the prompt doesn't exist —
    callers fall back to a local template so the router always runs.
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
        # flush() (non-destructive), NOT shutdown(): get_client() returns a
        # process-global singleton reused by the FastAPI server across requests,
        # so shutdown() would drop traces for every subsequent request.
        if hasattr(client, "flush"):
            client.flush()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse flush failed: {e}")

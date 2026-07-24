"""
Langfuse Integration Configuration (v4 SDK)

When LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set,
traces are sent to Langfuse (which uses ClickHouse as its OLAP backend).

Supports session tracking via propagate_attributes.
"""

import os
import uuid
from typing import Optional, List
from contextlib import contextmanager

# Check if Langfuse is configured
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)

# Default session ID for demo runs (can be overridden)
_current_session_id = None


def is_langfuse_enabled() -> bool:
    """Check if Langfuse is configured and available."""
    return LANGFUSE_ENABLED


def get_langfuse_client():
    """
    Get Langfuse client for direct API access (v4 SDK).
    Returns None if Langfuse is not configured.
    """
    if not LANGFUSE_ENABLED:
        return None

    try:
        from langfuse import get_client
        client = get_client()
        return client
    except ImportError:
        print("Langfuse package not installed")
        return None
    except Exception as e:
        print(f"Failed to initialize Langfuse client: {e}")
        return None


def get_managed_prompt(name: str, label: str = "production"):
    """Fetch a Langfuse-managed prompt (the Deploy node of the AI Engineering loop).

    Returns the Langfuse prompt object (has ``.get_langchain_prompt()`` and links
    to traces) or ``None`` if Langfuse is unavailable / the prompt isn't seeded —
    callers fall back to a local template so the app always runs. Editing this
    prompt in the Langfuse UI (or promoting a new version to ``production``)
    changes behaviour on the next run with no code change.
    """
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return client.get_prompt(name, label=label)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse get_prompt('{name}') unavailable, using local fallback: {e}")
        return None


def set_session_id(session_id: str):
    """Set the current session ID for subsequent traces."""
    global _current_session_id
    _current_session_id = session_id


def get_session_id() -> str:
    """Get the current session ID, creating one if needed."""
    global _current_session_id
    if _current_session_id is None:
        _current_session_id = f"text-to-sql-{uuid.uuid4().hex[:8]}"
    return _current_session_id


@contextmanager
def langfuse_session(session_id: Optional[str] = None):
    """
    Context manager for Langfuse session tracking.

    Usage:
        with langfuse_session("my-session-123"):
            # All traces within this block will have the session_id
            handler = get_langfuse_handler()
            chain.invoke(..., callbacks=[handler])
    """
    if not LANGFUSE_ENABLED:
        yield
        return

    try:
        from langfuse import get_client, propagate_attributes

        sid = session_id or get_session_id()
        client = get_client()

        with client.start_as_current_observation(as_type="span", name="session-root"):
            with propagate_attributes(session_id=sid):
                yield
    except Exception as e:
        print(f"Failed to create Langfuse session: {e}")
        yield


@contextmanager
def langfuse_trace(trace_name="text-to-sql", tags=None):
    """Context manager that sets trace name and tags for all Langfuse traces within.

    ``propagate_attributes`` only stamps *attributes* (name/tags) onto whatever
    span is active when a new observation starts — it does not itself keep one
    trace_id alive across sequential, independent top-level calls. The pipeline
    makes several such calls in a row (``analysis_chain.invoke`` ->
    ``retrieve_context`` -> ``response_chain.invoke``); with no span already
    open at each call site, every one of them minted its own ROOT span (and
    therefore its own trace_id) — same trace name/tags, three separate traces
    in Langfuse instead of one. Opening one root span here (mirroring
    ``langfuse_session()`` below) keeps a parent active for the whole block, so
    every LangChain-driven and manually-instrumented step nests under it as a
    single trace.
    """
    if not LANGFUSE_ENABLED:
        yield
        return

    try:
        from langfuse import get_client, propagate_attributes
        client = get_client()
        with client.start_as_current_observation(as_type="span", name=trace_name):
            with propagate_attributes(trace_name=trace_name, tags=tags or ["text-to-sql", "demo"]):
                yield
    except Exception as e:
        print(f"Failed to set Langfuse trace context: {e}")
        yield


@contextmanager
def langfuse_span(name: str):
    """Context manager that creates a child span nested under the current trace.

    Mirrors the v3 pattern used by langfuse_session(); no-op when Langfuse is
    not configured. Use to instrument non-LangChain steps (e.g. MCP calls) so
    they appear as spans within the active trace.
    """
    if not LANGFUSE_ENABLED:
        yield
        return

    try:
        from langfuse import get_client

        client = get_client()
        with client.start_as_current_observation(as_type="span", name=name):
            yield
    except Exception as e:
        print(f"Failed to create Langfuse span '{name}': {e}")
        yield


@contextmanager
def langfuse_observe(name: str, as_type: str = "span", input=None):
    """Create a typed observation (agent-graph semantics) nested under the trace.

    ``as_type`` values other than ``span``/``generation`` (e.g. ``tool``,
    ``evaluator``) are what make Langfuse render an agentic graph. Yields the
    observation handle so callers can ``.update(output=..., prompt=..., metadata=...)``
    or ``None`` when Langfuse is not configured (the app still runs untraced).

    Mirrors ``demos/agentic-rag/langfuse_config.py:observe`` — the evaluator-optimizer
    loop needs the same generation/tool/evaluator observation types.
    """
    client = get_langfuse_client()
    if client is None:
        yield None
        return
    try:
        with client.start_as_current_observation(as_type=as_type, name=name, input=input) as obs:
            yield obs
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse observation '{name}' failed: {e}")
        yield None


def score_current_span(name: str, value, comment: Optional[str] = None, data_type: str = "NUMERIC"):
    """Attach a score to the active observation/span.

    Use for step-level verdicts that can repeat within one trace (e.g.
    ``sql_critic_score`` on each per-iteration ``critique-sql`` observation) so a
    multi-iteration run shows the score climbing across iterations — the same
    convention as ``retrieval_relevance`` in ``demos/agentic-rag/graph.py``.
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.score_current_span(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse span score '{name}' failed: {e}")


def score_current_trace(name: str, value, comment: Optional[str] = None, data_type: str = "NUMERIC"):
    """Attach an evaluation score to the active trace (once per run).

    Use for app-computed cross-iteration summaries the loop holds the history for
    (``converged``, ``iterations_to_accept``, ``sql_quality_delta``) — a Langfuse
    code evaluator sees only one matched observation and cannot read sibling
    iterations, so these must be pushed by the loop.
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace score '{name}' failed: {e}")


def get_langfuse_handler():
    """
    Get Langfuse callback handler for LangChain with session support.
    Returns None if Langfuse is not configured.

    Use within a langfuse_trace() context to set trace name and tags.
    """
    if not LANGFUSE_ENABLED:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler()
        return handler

    except ImportError:
        print("Langfuse LangChain integration not available")
        return None
    except Exception as e:
        print(f"Failed to create Langfuse handler: {e}")
        return None


def score_trace(
    trace_id: str,
    relevance_score: Optional[float] = None,
    coherence_score: Optional[float] = None,
    comment: Optional[str] = None
):
    """
    Add evaluation scores to a Langfuse trace.

    Args:
        trace_id: The Langfuse trace ID to score
        relevance_score: Answer relevance score (0.0-1.0)
        coherence_score: Coherence score (0.0-1.0)
        comment: Optional comment/reasoning for the scores
    """
    client = get_langfuse_client()
    if client is None:
        return

    try:
        if relevance_score is not None:
            client.score(
                trace_id=trace_id,
                name="relevance",
                value=relevance_score,
                data_type="NUMERIC",
                comment=comment
            )

        if coherence_score is not None:
            client.score(
                trace_id=trace_id,
                name="coherence",
                value=coherence_score,
                data_type="NUMERIC",
                comment=comment
            )

        if hasattr(client, 'flush'):
            client.flush()
    except Exception as e:
        print(f"Failed to score trace {trace_id}: {e}")


def flush():
    """Flush any pending Langfuse events and shut down cleanly."""
    if not LANGFUSE_ENABLED:
        return

    try:
        from langfuse import get_client
        client = get_client()
        if client and hasattr(client, 'shutdown'):
            client.shutdown()
        elif client and hasattr(client, 'flush'):
            client.flush()
    except Exception as e:
        print(f"Failed to flush Langfuse: {e}")

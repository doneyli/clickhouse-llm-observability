"""
Langfuse Integration Configuration (v3 API)

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
    Get Langfuse client for direct API access (v3 API).
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


def get_langfuse_handler():
    """
    Get Langfuse callback handler for LangChain with session support.
    Returns None if Langfuse is not configured.

    The handler automatically picks up session_id from propagate_attributes context.
    For explicit session control, wrap calls in langfuse_session() context manager.
    """
    if not LANGFUSE_ENABLED:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        # Create handler - v3 API uses different initialization
        # Session/user context is set via propagate_attributes
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
    """Flush any pending Langfuse events."""
    if not LANGFUSE_ENABLED:
        return

    try:
        from langfuse import get_client
        client = get_client()
        if client and hasattr(client, 'flush'):
            client.flush()
    except Exception as e:
        print(f"Failed to flush Langfuse: {e}")

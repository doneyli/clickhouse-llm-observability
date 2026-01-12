"""
Langfuse Integration Configuration

Provides dual instrumentation alongside OpenLLMetry/TruLens.
When LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set,
traces are sent to both ClickStack (via OpenLLMetry) and Langfuse.
"""

import os
from typing import Optional, List

# Check if Langfuse is configured
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)

_langfuse_client = None
_langfuse_handler = None


def is_langfuse_enabled() -> bool:
    """Check if Langfuse is configured and available."""
    return LANGFUSE_ENABLED


def get_langfuse_client():
    """
    Get Langfuse client for direct API access (scoring, etc.).
    Returns None if Langfuse is not configured.
    """
    global _langfuse_client

    if not LANGFUSE_ENABLED:
        return None

    if _langfuse_client is None:
        try:
            from langfuse import Langfuse
            _langfuse_client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                host=os.getenv("LANGFUSE_HOST", "http://localhost:3001")
            )
            print(f"✅ Langfuse client initialized: {os.getenv('LANGFUSE_HOST')}")
        except ImportError:
            print("⚠️  Langfuse package not installed")
            return None
        except Exception as e:
            print(f"⚠️  Failed to initialize Langfuse client: {e}")
            return None

    return _langfuse_client


def get_langfuse_handler(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[dict] = None
):
    """
    Get Langfuse callback handler for LangChain.
    Returns None if Langfuse is not configured.

    Note: Langfuse SDK v3 uses environment variables for authentication:
    - LANGFUSE_PUBLIC_KEY
    - LANGFUSE_SECRET_KEY
    - LANGFUSE_HOST (defaults to cloud, set to local for self-hosted)

    Args:
        user_id: Optional user identifier for the trace
        session_id: Optional session identifier for grouping traces
        tags: Optional list of tags for filtering
        metadata: Optional metadata dict to attach to trace
    """
    if not LANGFUSE_ENABLED:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        # Langfuse SDK v3 uses environment variables for auth
        # CallbackHandler() reads from LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
        # Note: v3 API uses no constructor params - auth from env vars
        handler = CallbackHandler()
        return handler

    except ImportError:
        print("⚠️  Langfuse package not installed")
        return None
    except Exception as e:
        print(f"⚠️  Failed to create Langfuse handler: {e}")
        return None


def score_trace(
    trace_id: str,
    relevance_score: Optional[float] = None,
    coherence_score: Optional[float] = None,
    groundedness_score: Optional[float] = None,
    comment: Optional[str] = None
):
    """
    Add evaluation scores to a Langfuse trace.

    Args:
        trace_id: The Langfuse trace ID to score
        relevance_score: Answer relevance score (0.0-1.0)
        coherence_score: Coherence score (0.0-1.0)
        groundedness_score: Groundedness score (0.0-1.0) - RAG specific
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

        if groundedness_score is not None:
            client.score(
                trace_id=trace_id,
                name="groundedness",
                value=groundedness_score,
                data_type="NUMERIC",
                comment=comment
            )

        client.flush()
    except Exception as e:
        print(f"⚠️  Failed to score trace {trace_id}: {e}")


def flush():
    """Flush any pending Langfuse events."""
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
        except Exception as e:
            print(f"⚠️  Failed to flush Langfuse: {e}")

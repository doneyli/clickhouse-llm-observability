"""
Langfuse instrumentation for the Cluster Health Investigator (v3 SDK).

Cloned from demos/agentic-rag/langfuse_config.py, with:
- session prefix `cluster-health-`,
- default tags ["cluster-health", "demo"],
- an `update_current_observation(metadata=..., output=...)` helper (null-safe),
- a `create_score(...)` passthrough for post-hoc per-observation scoring
  (scripts/score_delegations.py attaches `delegation_quality` to worker spans).

Two layers of instrumentation, exactly as agentic-rag:
1. The LangGraph CallbackHandler (drives the Agent Graph — including the dynamic
   Send fan-out, so `worker (N/N)` renders in the Aggregated view).
2. Explicit typed observations (`agent`, `tool`, `evaluator`, `generation`) via
   `observe()` — what makes Langfuse read the trace as agentic and lets the
   orchestrator span carry the plan JSON as its output.

Everything is a no-op when Langfuse keys are absent, so the investigator still
runs (and still prints its terminal step log).
"""

import os
import uuid
from contextlib import contextmanager
from typing import Optional

LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)

SESSION_PREFIX = "cluster-health-"
DEFAULT_TAGS = ["cluster-health", "demo"]


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
    return f"{SESSION_PREFIX}{uuid.uuid4().hex[:8]}"


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
            tags=tags or list(DEFAULT_TAGS),
        ):
            yield
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace context failed: {e}")
        yield


@contextmanager
def observe(name: str, as_type: str = "span", input=None):
    """Create a typed observation (agent | tool | evaluator | generation | ...).

    Yields the observation handle (or None). Call `.update(output=..., metadata=...)`
    on it. The non-default `as_type` values trigger Langfuse's agentic graph.
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


def update_current_observation(metadata=None, output=None, input=None):
    """Null-safe update of the active observation (e.g. worker identity metadata).

    Worker identity (analysis_type / focus) goes in METADATA, never the span
    name, so the Aggregated Agent Graph collapses all workers into `worker (N/N)`
    instead of N distinct nodes.
    """
    client = get_client()
    if client is None:
        return
    try:
        kwargs = {}
        if metadata is not None:
            kwargs["metadata"] = metadata
        if output is not None:
            kwargs["output"] = output
        if input is not None:
            kwargs["input"] = input
        if kwargs:
            # v4 removed `update_current_observation`; the typed accessors
            # replaced it. `update_current_span` is the right one here — every
            # call site in this demo is inside a span, not a generation.
            # Verified against langfuse 4.14.4.
            client.update_current_span(**kwargs)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse update_current_observation failed: {e}")


def score_current_trace(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach an evaluation score to the active trace (e.g. worker_count)."""
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse score '{name}' failed: {e}")


def score_current_span(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach a score to the active observation/span (step-level verdicts)."""
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_span(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse span score '{name}' failed: {e}")


def create_score(trace_id: str, name: str, value, observation_id: Optional[str] = None,
                 comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach a score to a specific trace/observation via the Scores API.

    Used out-of-band (scripts/score_delegations.py) to push per-worker
    `delegation_quality` onto individual worker observations after a run.
    """
    client = get_client()
    if client is None:
        return
    try:
        client.create_score(
            trace_id=trace_id,
            observation_id=observation_id,
            name=name,
            value=value,
            comment=comment,
            data_type=data_type,
        )
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse create_score '{name}' failed: {e}")


def get_prompt(name: str, label: str = "production"):
    """Fetch a managed prompt from Langfuse (prompt management).

    Returns the Langfuse prompt object (has .compile(**vars) and links to traces)
    or None if Langfuse is unavailable / the prompt doesn't exist — callers fall
    back to a local template so the investigator always runs.
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
        # flush() (non-destructive), NOT shutdown(): get_client() is a
        # process-global singleton reused across runs in batch/live-demo mode.
        if hasattr(client, "flush"):
            client.flush()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse flush failed: {e}")

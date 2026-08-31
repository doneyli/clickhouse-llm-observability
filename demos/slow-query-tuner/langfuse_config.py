"""
Langfuse instrumentation for the Slow Query Tuner demo (v3 SDK).

Cloned from demos/agentic-rag/langfuse_config.py — same house conventions:

- Typed observations (`agent` / `generation` / `tool` / `evaluator`) via
  `observe()`; the non-`span`/`generation` types are what make Langfuse render
  the Agent Graph.
- `trace_context()` sets a stable, low-cardinality trace name + session_id +
  tags via `propagate_attributes`, so a run that pauses and resumes in a fresh
  process reuses one `session_id` and stitches into a single Langfuse session.
- Everything is a NO-OP when Langfuse keys are absent, so the loop still runs
  (graceful degradation — house convention).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Optional

LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)

DEFAULT_TAGS = ["slow-query-tuner", "demo"]


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
    """Session id for a tuning run. Reused verbatim on --resume so the paused
    and resumed traces land in one Langfuse session."""
    return f"qtune-{uuid.uuid4().hex[:8]}"


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
            tags=tags or DEFAULT_TAGS,
        ):
            yield
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace context failed: {e}")
        yield


@contextmanager
def observe(name: str, as_type: str = "span", input=None):
    """Create a typed observation (agent | generation | tool | evaluator | ...).

    Yields the observation handle (or None). Call `.update(output=..., prompt=...,
    metadata=..., level=...)` on it. The non-default `as_type` values are what
    trigger Langfuse's agentic Agent Graph rendering.
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


def update_current_trace(**kwargs):
    """Set the trace's input/output/metadata.

    The name is kept for its call sites in agent_loop.py, but note it no longer
    maps to a client method of the same name: SDK v4 removed
    `update_current_trace`, and trace-level input/output is derived from the root
    observation instead. The body writes to the current span accordingly.
    """
    client = get_client()
    if client is None:
        return
    try:
        # v4 removed `update_current_trace`. Trace-level input/output is now
        # DERIVED from the root observation, so setting it on the current span —
        # which is the root when the agent loop calls this — is the equivalent.
        # Verified against langfuse 4.14.4: update_current_trace absent,
        # update_current_span present.
        client.update_current_span(**kwargs)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse update_current_trace failed: {e}")


def score_current_trace(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach a trace-level evaluation score (one per run: turns_used, etc.)."""
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace score '{name}' failed: {e}")


def score_current_span(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach a span-level score to the active observation.

    Use for step-level verdicts that can repeat within a run (semantics_preserved
    on each candidate run-query span, improvement_delta on each assess-progress).
    """
    client = get_client()
    if client is None:
        return
    try:
        client.score_current_span(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse span score '{name}' failed: {e}")


def get_prompt(name: str, label: str = "production"):
    """Fetch a managed prompt from Langfuse (Prompt management).

    Returns the Langfuse prompt object (`.compile(**vars)`, links to traces) or
    None if Langfuse is unavailable / the prompt isn't seeded — callers fall back
    to a local template so the agent always runs.
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
    """Flush (never shutdown — get_client() is a process-global singleton)."""
    client = get_client()
    if client is None:
        return
    try:
        if hasattr(client, "flush"):
            client.flush()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse flush failed: {e}")

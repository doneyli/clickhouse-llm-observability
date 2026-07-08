"""Langfuse observability factory for all agents and crews.

Returns a LangChain CallbackHandler list. Metadata flows on every wrapped LLM
call.

Callers should ALWAYS:
1) build callbacks via `make_observability_callbacks(...)` and pass to agents
2) wrap the top-level orchestrator invocation in `with_observability_context(...)`
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from src.config import Backend, load_config

# Module-level import preserved so tests can `patch("src.observability.CallbackHandler", ...)`.
# This is fine because `langfuse` is in the base `dependencies` list of pyproject.toml.
try:
    from langfuse.langchain import CallbackHandler
except ImportError:
    CallbackHandler = None  # type: ignore[assignment,misc]

# One demo_run_id per process so all live runs in a session are filterable together
_DEMO_RUN_ID = str(uuid.uuid4())


@lru_cache(maxsize=1)
def _get_customer_name() -> str:
    return load_config().customer.display_name


def _base_metadata(agent_name: str, extra: dict[str, Any] | None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "agent_name": agent_name,
        "customer": _get_customer_name(),
        "demo_run_id": _DEMO_RUN_ID,
    }
    if extra:
        meta.update(extra)
    return meta


def make_observability_callbacks(
    *,
    agent_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    backend: Backend | None = None,
) -> list[Any]:
    """Return the LangChain callback list for Langfuse.

    Langfuse v4: returns [CallbackHandler()] with NO per-trace kwargs. v4
    removed `user_id`, `session_id`, `tags`, `metadata` from the constructor;
    those flow via `config['metadata']` on `graph.invoke(...)` using the
    reserved keys `langfuse_user_id`, `langfuse_session_id`, `langfuse_tags`.
    Use `make_observability_run_metadata()` + `make_observability_run_config()`
    to build that config dict.
    """
    # Parameters are accepted for callsite continuity but the per-trace
    # values flow through config['metadata'] in v4. Reading them here would
    # invite the v3 anti-pattern back; explicitly discard so callers see no
    # silent behavior change.
    del user_id, session_id, tags, extra_metadata, agent_name, backend

    if CallbackHandler is None:
        raise RuntimeError(
            "BACKEND=langfuse but `langfuse` is not installed. "
            "Reinstall the demo dependencies."
        )
    return [CallbackHandler()]


def make_observability_run_metadata(
    *,
    agent_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Build the metadata dict to pass as `config['metadata']` to `graph.invoke()`.

    Langfuse v4 reads the reserved keys `langfuse_user_id`,
    `langfuse_session_id`, `langfuse_tags` at the root chain start
    (parent_run_id is None) and attaches them to the trace. All other keys
    pass through verbatim.
    """
    del backend
    base = _base_metadata(agent_name, extra_metadata)
    md: dict[str, Any] = dict(base)
    if user_id is not None:
        md["langfuse_user_id"] = user_id
    if session_id is not None:
        md["langfuse_session_id"] = session_id
    if tags:
        md["langfuse_tags"] = list(tags)
    return md


def make_observability_run_config(
    *,
    agent_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Build the full RunnableConfig-shaped dict for `graph.invoke(state, config=...)`.

    Returns `{"callbacks": [...], "metadata": {...}, "tags": [...]}`.

    This is the load-bearing call: the CallbackHandler instance + per-trace
    metadata + tags all attach at the root invocation. LangGraph / LangChain
    propagates `config['callbacks']` and `config['metadata']` to every nested
    LLM call, so the handler only needs to be attached once at the root.
    """
    callbacks = make_observability_callbacks(
        agent_name=agent_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags,
        extra_metadata=extra_metadata,
        backend=backend,
    )
    metadata = make_observability_run_metadata(
        agent_name=agent_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags,
        extra_metadata=extra_metadata,
        backend=backend,
    )
    return {
        "callbacks": callbacks,
        "metadata": metadata,
        "tags": list(tags or []),
    }


@contextmanager
def with_observability_context(
    *,
    agent_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    backend: Backend | None = None,
) -> Iterator[None]:
    """Context manager for per-call attribution.

    Langfuse: no-op (metadata flows through callbacks).
    """
    del agent_name, user_id, session_id, tags, extra_metadata, backend
    yield


# -- Backwards-compatible alias ----------------------------------------------
# Older call sites import `make_langfuse_handler`. Preserve as a thin shim
# returning the single handler. Prefer `make_observability_callbacks(...)` +
# `make_observability_run_config(...)` in new code.
def make_langfuse_handler(
    *,
    agent_name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> Any | None:
    """Deprecated. Use make_observability_run_config() in new code.

    Returns a bare CallbackHandler() for Langfuse; per-trace metadata must
    be set via `config['metadata']` on the LangChain/LangGraph invocation.
    """
    callbacks = make_observability_callbacks(
        agent_name=agent_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags,
    )
    return callbacks[0] if callbacks else None

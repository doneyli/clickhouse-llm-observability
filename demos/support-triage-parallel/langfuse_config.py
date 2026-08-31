"""
Langfuse instrumentation for the Support Triage Parallel demo (v3 SDK).

This is the per-demo Langfuse wiring module (cloned from
``demos/text-to-sql/langfuse_config.py`` and extended with the typed
``observe()`` context manager from ``demos/agentic-rag/langfuse_config.py``).

The whole module is a **no-op when Langfuse keys are absent** — every helper
degrades gracefully so the demo still runs (and the pipeline still fans out /
votes) without a Langfuse project. ``observe()`` yields a ``_NullObs`` in that
case, so callers can always do ``obs.update(...)`` unconditionally.

Design notes for the parallelization pattern:
- ``observe(name, as_type=...)`` uses the low-level v3 SDK
  ``start_as_current_observation``. Opening a parent observation and then
  ``asyncio.gather``-ing child branches *inside* that context makes the branches
  auto-nest as siblings (OTel context propagates into each asyncio task), which
  is what renders the concurrent Timeline in Langfuse.
- Branch names stay **low-cardinality** (``vote-candidate`` on all N samples);
  the run index lives in ``metadata`` (``sample_index``) so branches stay
  filterable — per the tracing best practices.
"""

import os
import uuid
from contextlib import contextmanager
from typing import Optional

# Langfuse is enabled only when both keys are present (repo convention).
LANGFUSE_ENABLED = bool(
    os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
)


def is_langfuse_enabled() -> bool:
    """True when Langfuse keys are configured. Everything is a no-op otherwise."""
    return LANGFUSE_ENABLED


def get_langfuse_client():
    """Return the v3 Langfuse client (process-global singleton), or None."""
    if not LANGFUSE_ENABLED:
        return None
    try:
        from langfuse import get_client
        return get_client()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse client unavailable: {e}")
        return None


class _NullObs:
    """No-op observation handle used when Langfuse is disabled.

    Supports ``.update(...)`` (returns self), the context-manager protocol, and
    ``.score(...)`` so calling code never needs to branch on whether Langfuse is
    configured.
    """

    def update(self, *args, **kwargs):
        return self

    def score(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def new_session_id() -> str:
    """One session per demo run — ``triage-<uuid8>`` (repo naming convention)."""
    return f"triage-{uuid.uuid4().hex[:8]}"


@contextmanager
def trace_context(name: str = "triage-support-ticket", session_id: Optional[str] = None,
                  tags=None, user_id=None):
    """Set trace name / session / tags for every observation emitted within.

    ``name`` is a stable, low-cardinality trace name — the ticket goes in the
    trace *input*, never in the name (repo convention).
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
            tags=tags or ["support-triage-parallel", "demo"],
        ):
            yield
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace context failed: {e}")
        yield


@contextmanager
def observe(name: str, as_type: str = "span", input=None, metadata=None, **kwargs):
    """Typed observation context manager (span | generation | tool | guardrail | ...).

    Yields the live observation handle (or a ``_NullObs`` when Langfuse is off),
    so callers can always call ``obs.update(output=..., usage_details=...)``.

    Extra kwargs (e.g. ``model=`` or ``prompt=`` for generations) are forwarded
    to ``start_as_current_observation``. If the SDK rejects an ``as_type`` it
    doesn't recognise, we retry once as a plain ``span`` so tracing never breaks
    the run.
    """
    client = get_langfuse_client()
    if client is None:
        yield _NullObs()
        return
    try:
        with client.start_as_current_observation(
            as_type=as_type, name=name, input=input, metadata=metadata, **kwargs
        ) as obs:
            yield obs
    except TypeError:
        # Unknown as_type or unsupported kwarg for this SDK build — fall back to
        # a plain span so the observation still lands.
        try:
            with client.start_as_current_observation(
                as_type="span", name=name, input=input, metadata=metadata
            ) as obs:
                yield obs
        except Exception as e:  # pragma: no cover - defensive
            print(f"Langfuse observation '{name}' failed: {e}")
            yield _NullObs()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse observation '{name}' failed: {e}")
        yield _NullObs()


def score_current_trace(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach a score to the active trace (e.g. ``consensus_confidence`` — one per run)."""
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.score_current_trace(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse trace score '{name}' failed: {e}")


def score_current_span(name: str, value, comment: Optional[str] = None, data_type="NUMERIC"):
    """Attach a score to the active observation/span.

    Use for step-level verdicts that can repeat within a trace (e.g.
    ``sql_validity_rate`` on validate-candidates, ``policy_flagged`` on the guard
    branch), so the score sits on the exact step that produced it.
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        client.score_current_span(name=name, value=value, comment=comment, data_type=data_type)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse span score '{name}' failed: {e}")


def get_managed_prompt(name: str, label: str = "production"):
    """Fetch a Langfuse-managed prompt (the Deploy node of the AI Engineering loop).

    Returns the prompt object (``.compile(**vars)`` + links to traces) or None if
    Langfuse is unavailable / the prompt isn't seeded — callers fall back to a
    local template so the app always runs. Promoting a new version to
    ``production`` in the UI changes behaviour on the next run with no redeploy.
    """
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return client.get_prompt(name, label=label)
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse get_prompt('{name}') unavailable, using local fallback: {e}")
        return None


def render_prompt(_name: str, _fallback: str, **variables):
    """Resolve a prompt to compiled text + an optional link handle.

    Positional args are underscore-prefixed so any template variable name
    (``question``, ``branch_outputs``, …) can be passed as a keyword without
    colliding. Returns ``(text, prompt_obj_or_None)``:
    - Managed prompt present -> ``prompt_obj.compile(**variables)`` + the object
      (pass it as ``prompt=`` so the generation links the version).
    - Otherwise -> the local ``_fallback`` template with ``{{var}}`` markers
      substituted (kept in sync by hand with scripts/seed_prompts.py).
    """
    prompt_obj = get_managed_prompt(_name)
    if prompt_obj is not None:
        try:
            return prompt_obj.compile(**variables), prompt_obj
        except Exception as e:  # pragma: no cover - defensive
            print(f"Managed prompt '{_name}' unusable ({e}); using local fallback.")
    text = _fallback
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text, None


def flush():
    """Flush pending Langfuse events (non-destructive — never shutdown the singleton)."""
    client = get_langfuse_client()
    if client is None:
        return
    try:
        if hasattr(client, "flush"):
            client.flush()
    except Exception as e:  # pragma: no cover - defensive
        print(f"Langfuse flush failed: {e}")

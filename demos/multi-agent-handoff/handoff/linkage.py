"""
Linkage modes: what crosses the wire on an A2A handoff so the two sides stay linked (SPEC §6).

    none           nothing. The remote agent starts an unrelated trace.
    traceparent    W3C traceparent only. Same trace id; the remote side has no session/user.
    baggage-claim  traceparent + a W3C baggage header, but no propagate_attributes(as_baggage).
                   Diagnostic row of the matrix: the header then carries only the SDK's own
                   `langfuse_trace_id` claim.
    baggage        traceparent + baggage with session, user, trace name and environment,
                   via propagate_attributes(as_baggage=True) scoped to this one call. Default.
    session        no trace headers. Session and user travel as A2A message metadata
                   (application level); the remote agent starts its own trace in the same
                   session and records where it came from.

Headers are injected for the A2A call only (a2a-sdk `ClientCallContext.service_parameters`),
never by global HTTP instrumentation: the Anthropic SDK uses httpx too, and the baggage
would ride along on every model call.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

from handoff import config

MODES = ("none", "traceparent", "baggage-claim", "baggage", "session")
DEFAULT_MODE = "baggage"


@dataclass
class TurnContext:
    """Trace-level attributes of the turn a handoff belongs to."""

    session_id: Optional[str]
    user_id: Optional[str]
    trace_name: str = "support-turn"
    environment: Optional[str] = None
    version: Optional[str] = None  # the prompt versions the turn runs (prompts.version_tag)


@contextmanager
def outbound(mode: str, turn: TurnContext) -> Iterator[tuple[dict, dict]]:
    """Yield (headers, message_metadata) for one A2A call made inside this block."""
    if mode not in MODES:
        raise ValueError(f"unknown linkage mode {mode!r}; one of {MODES}")
    if mode == "none":
        yield {}, {}
    elif mode == "traceparent":
        yield config.inject_trace_context(baggage=False), {}
    elif mode == "baggage-claim":
        yield config.inject_trace_context(baggage=True), {}
    elif mode == "baggage":
        from langfuse import propagate_attributes

        attrs = {k: v for k, v in {"session_id": turn.session_id, "user_id": turn.user_id,
                                   "trace_name": turn.trace_name,
                                   "environment": turn.environment or config.current_environment(),
                                   "version": turn.version}.items() if v}
        # Never tags: a list in baggage arrives as one garbage string.
        with propagate_attributes(as_baggage=True, **attrs):
            yield config.inject_trace_context(baggage=True), {}
    else:  # session
        lf = config.get_langfuse(config.SERVICE or "orchestrator")
        yield {}, {k: v for k, v in {
            "session_id": turn.session_id, "user_id": turn.user_id,
            "environment": turn.environment or config.current_environment(),
            "parent_trace_id": lf.get_current_trace_id(),
            "parent_observation_id": lf.get_current_observation_id()}.items() if v}


def turn_from_baggage() -> TurnContext:
    """The turn as it arrived in baggage on this (remote) side, for an onward hop."""
    from opentelemetry import baggage

    b = baggage.get_all()
    return TurnContext(session_id=b.get("langfuse_session_id"), user_id=b.get("langfuse_user_id"),
                       trace_name=str(b.get("langfuse_trace_name") or "support-turn"),
                       environment=b.get("langfuse_environment"), version=b.get("langfuse_version"))

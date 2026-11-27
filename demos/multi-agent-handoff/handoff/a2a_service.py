"""
Serving one agent over A2A 1.0, inside the caller's trace.

`build_app(agent_name, handler)` returns a Starlette app with the agent card at
`/.well-known/agent-card.json` and the JSON-RPC endpoint at `/`. For every request the
executor:

  1. extracts `traceparent` / `tracestate` / `baggage` from the inbound HTTP headers and
     attaches them BEFORE anything is traced, so the agent's observations are children of
     the caller's `handoff` observation (and carry the caller's session from baggage);
  2. in `session` linkage, where no trace headers come, starts a new trace in the caller's
     session from the A2A message metadata and records the parent trace id;
  3. runs the handler inside one `agent` observation, and maps its reply to an A2A task
     state: completed (with a `reply` artifact), input-required (it keeps the conversation),
     or failed. An exception becomes a failed task, never a hung caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from handoff import config  # noqa: F401  (must precede a2a imports)

from a2a.helpers.proto_helpers import new_data_part, new_task, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, TaskState
from google.protobuf import json_format
from opentelemetry import context as otel_context
from starlette.applications import Starlette

from handoff.linkage import TurnContext

log = logging.getLogger("a2a-service")


@dataclass
class AgentRequest:
    text: str
    data: dict
    metadata: dict
    task_id: str
    context_id: str
    continuation: bool  # a later turn on a task this agent already holds (transfer)
    linkage: str
    turn: TurnContext
    linked_by: str


@dataclass
class AgentReply:
    text: str
    state: str = "completed"  # completed | input-required | failed
    data: dict = field(default_factory=dict)
    output: Optional[dict] = None  # what the agent observation records as output


Handler = Callable[[AgentRequest], Awaitable[AgentReply]]


def agent_card(name: str, description: str, skills: list[dict], url: str, version: str) -> AgentCard:
    return AgentCard(
        name=name, description=description, version=version,
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC", protocol_version="1.0")],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[AgentSkill(id=s["id"], name=s["name"], description=s["description"],
                           tags=s.get("tags", []), examples=s.get("examples", [])) for s in skills],
    )


class TracedAgentExecutor(AgentExecutor):
    def __init__(self, name: str, handler: Handler, team: str):
        self.name, self.handler, self.team = name, handler, team
        self.lf = config.get_langfuse(name)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        from langfuse import propagate_attributes

        headers = (context.call_context.state.get("headers") if context.call_context else None) or {}
        extracted, arrived = config.extract_trace_context(headers)
        token = otel_context.attach(extracted) if extracted is not None else None

        continuation = context.current_task is not None
        task = context.current_task or new_task(context.task_id, context.context_id,
                                                TaskState.TASK_STATE_SUBMITTED, history=[context.message])
        if not continuation:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        meta = json_format.MessageToDict(context.message.metadata) if context.message else {}
        texts, data = [], {}
        for p in (context.message.parts if context.message else []):
            if p.HasField("text"):
                texts.append(p.text)
            elif p.HasField("data"):
                value = json_format.MessageToDict(p.data)
                if isinstance(value, dict):
                    data.update(value)

        linked_by = " + ".join(arrived) if arrived else ("a2a metadata" if meta.get("session_id") else "none")
        if extracted is not None:
            from handoff.linkage import turn_from_baggage

            turn = turn_from_baggage()
        else:
            turn = TurnContext(session_id=meta.get("session_id"), user_id=meta.get("user_id"),
                               trace_name=f"{self.name}-task", environment=meta.get("environment"))
        req = AgentRequest(text="\n".join(texts), data=data, metadata=meta, task_id=task.id,
                           context_id=task.context_id, continuation=continuation,
                           linkage=str(meta.get("handoff_linkage") or "baggage"), turn=turn, linked_by=linked_by)
        # Application-level session linkage: only when no trace context came over the wire.
        trace_attrs = {}
        if extracted is None and meta.get("session_id"):
            trace_attrs = {k: v for k, v in {"session_id": turn.session_id, "user_id": turn.user_id,
                                             "trace_name": turn.trace_name,
                                             "environment": turn.environment}.items() if v}
        try:
            await updater.start_work()
            with propagate_attributes(**trace_attrs), self.lf.start_as_current_observation(
                    as_type="agent", name=self.name, input={"message": req.text, "handoff": data or None},
                    metadata={"role": "agent", "agent": self.name, "team": self.team, "service": self.name,
                              "a2a_task_id": task.id, "a2a_context_id": task.context_id,
                              "a2a_continuation": str(continuation).lower(), "linked_by": linked_by,
                              **({"parent_trace_id": meta["parent_trace_id"]} if meta.get("parent_trace_id") else {})},
            ) as obs:
                reply = await self.handler(req)
                obs.update(output=reply.output or {"reply": reply.text, **({"handoff": reply.data} if reply.data else {})},
                           level="ERROR" if reply.state == "failed" else "DEFAULT")
            parts = [new_text_part(reply.text)] + ([new_data_part(reply.data)] if reply.data else [])
            if reply.state == "input-required":
                await updater.requires_input(message=updater.new_agent_message(parts))
            elif reply.state == "failed":
                await updater.failed(message=updater.new_agent_message(parts))
            else:
                await updater.add_artifact(parts, name="reply")
                await updater.complete()
        except Exception as exc:  # noqa: BLE001 — surface as a failed task, never hang the caller
            log.exception("%s failed", self.name)
            await updater.failed(message=updater.new_agent_message(
                [new_text_part(f"{self.name} failed: {type(exc).__name__}: {exc}"[:500])]))
        finally:
            config.flush()
            if token is not None:
                otel_context.detach(token)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def build_app(name: str, handler: Handler, *, description: str, skills: list[dict], team: str,
              url: str) -> Starlette:
    card = agent_card(name, description, skills, url, config.RELEASE)
    request_handler = DefaultRequestHandler(agent_executor=TracedAgentExecutor(name, handler, team),
                                            task_store=InMemoryTaskStore(), agent_card=card)
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(request_handler, rpc_url="/")
    return Starlette(routes=routes)

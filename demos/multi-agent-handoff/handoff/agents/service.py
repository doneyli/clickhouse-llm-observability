"""
The A2A-service side of a real agent: turns an inbound A2A request into one agent turn.

The executor (handoff/a2a_service.py) has already attached the caller's trace context and
opened this agent's `agent` observation; this handler runs the agent loop inside it. The
agent's own delegations (`ask_billing` from tech-support) go out over A2A with the same
linkage mode the request came in with: the peer hop.
"""

from __future__ import annotations

from handoff.a2a_service import AgentReply, AgentRequest
from handoff.agents.base import AgentInput, run_agent
from handoff.tools.crm import RunState
from handoff.transport import A2ATransport

_HISTORY: dict[str, list] = {}  # A2A context id → this agent's messages in that conversation
_TRANSPORTS: dict[str, A2ATransport] = {}


def make_handler(name: str):
    async def handle(req: AgentRequest) -> AgentReply:
        kind = str(req.metadata.get("handoff_kind") or ("continue" if req.continuation else "transfer"))
        transport = _TRANSPORTS.setdefault(req.linkage, A2ATransport(req.linkage))
        customer_id = str(req.data.get("customer_id") or req.metadata.get("customer_id") or "")
        history = _HISTORY.get(req.context_id, []) if kind != "delegate" else []
        conversation_id = str(req.metadata.get("conversation_id") or (req.turn.session_id if req.turn else "")
                              or req.context_id)
        out = await run_agent(name, AgentInput(
            message=req.text, kind=kind, customer_id=customer_id, turn=req.turn,
            payload=req.data if kind in ("transfer", "delegate") else {}, history=history,
            frm=str(req.metadata.get("handoff_from", "")), hop=int(req.metadata.get("handoff_hop") or 1),
            prompt_label=req.metadata.get("prompt_label")), transport=transport,
            state=RunState(conversation_id=conversation_id),
            open_observation=False)
        if kind != "delegate":
            _HISTORY[req.context_id] = out.history
        data = {"effects": out.effects, "path": out.path, "prompt_version": out.prompt_version,
                "tools": out.tools, "handoffs": out.handoffs}
        if out.transfer:
            data["transfer"] = out.transfer
        if out.handback:
            data["handback"] = out.handback
        output = {"reply": out.text, "state": out.state,
                  **({"transfer": {"to": out.transfer["to"], "reason": out.transfer["reason"]}} if out.transfer else {}),
                  **({"handback": out.handback} if out.handback else {})}
        return AgentReply(text=out.text, state=out.state, data=data, output=output)

    return handle

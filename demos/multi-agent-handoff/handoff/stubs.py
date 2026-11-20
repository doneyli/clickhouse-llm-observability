"""
Deterministic stand-ins for the agents (no LLM), used by the linkage matrix and the tests.

They reproduce the hero scenario's *shape*: tech-support checks an outage, then delegates
to billing over A2A (a second hop, a third process); billing computes a credit.
"""

from __future__ import annotations

from handoff import config
from handoff.a2a_service import AgentReply, AgentRequest

_transport = None


def _tool(name: str, input: dict, output: dict) -> None:
    lf = config.get_langfuse(config.SERVICE or "stub")
    with lf.start_as_current_observation(as_type="tool", name=name, input=input) as t:
        t.update(output=output)


async def tech_support(req: AgentRequest) -> AgentReply:
    global _transport
    from handoff.transport import A2ATransport

    _tool("check_outage", {"zip": "94107"}, {"outage": True, "hours": 9})
    _transport = _transport or A2ATransport()
    res = await _transport.handoff(frm="tech-support", to="billing", kind="delegate",
                                   reason="credit for a confirmed outage", message="Quote the outage credit.",
                                   payload={"line_id": "L-1042-1", "outage_hours": 9}, turn=req.turn, hop=2,
                                   linkage=req.linkage)
    return AgentReply(text=f"Outage confirmed. Billing: {res.text}")


async def billing(req: AgentRequest) -> AgentReply:
    _tool("compute_credit", {"line_id": "L-1042-1", "outage_hours": 9}, {"credit": 2.5})
    return AgentReply(text="A credit of $2.50 is approved.")


HANDLERS = {"tech-support": tech_support, "billing": billing}

"""Deterministic trajectory and conversation-boundary checks, without models or services."""

from types import SimpleNamespace

import pytest

from handoff import conversation, orchestrator
from handoff.agents.base import AgentOutput
from handoff.linkage import TurnContext
from handoff.orchestrator import Conversation, Orchestrator
from handoff.transport import HandoffResult


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [1, 4])
async def test_delegation_before_transfer_keeps_trajectory_order_and_uses_the_hop_budget(monkeypatch, cap):
    calls = []

    async def handoff(**kwargs):
        calls.append(kwargs)
        return HandoffResult(state="completed", text="tech reply",
                             data={"tools": ["tech-support:check_outage"]})

    async def agent(name, inp, *, on_transfer, **kwargs):
        # A legal concierge turn: ask about a bill, then transfer the remaining technical issue.
        out = AgentOutput(text="", path=["billing"],
                          tools=["concierge:get_customer", "billing:get_invoice"],
                          handoffs=[{"kind": "delegate", "from": "concierge", "to": "billing", "t": 1}],
                          transfer={"to": "tech-support", "payload": {"line_id": "L-1042-2"}})
        out.text, out.state = await on_transfer(out.transfer, out)
        return out

    monkeypatch.setattr(orchestrator, "run_agent", agent)
    monkeypatch.setattr(orchestrator, "MAX_HANDOFFS", cap)
    orch = Orchestrator.__new__(Orchestrator)
    orch.prompt_label, orch.prompt_versions = None, {}
    orch.roster = None
    orch.transport = SimpleNamespace(handoff=handoff)
    conv = Conversation(customer_id="cust-1042", topology="local")
    path, hops = [], []
    await orch._concierge(conv, "bill and outage", TurnContext(conv.session_id, conv.customer_id), path, hops)

    assert path == (["concierge", "billing", "tech-support"] if cap > 1 else ["concierge", "billing"])
    assert conv.turn_tools[:2] == ["concierge:get_customer", "billing:get_invoice"]
    assert [h["to"] for h in hops] == (["billing", "tech-support"] if cap > 1 else ["billing"])
    assert conv.cap_hit == (cap == 1)
    if cap > 1:
        assert conv.turn_tools[2:] == ["tech-support:check_outage"]
        assert len(calls) == 1
    else:
        assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,ended_by", [("done", "customer-done"), ("gave-up", "customer-gave-up"),
                                             (None, "max-turns")])
async def test_customer_assesses_the_last_allowed_support_reply_without_running_an_extra_turn(
        monkeypatch, verdict, ended_by):
    assessed = []
    closed = []

    class Agent:
        def __init__(self, **kwargs):
            pass

        async def turn(self, conv, message):
            conv.turns += 1
            conv.transcript.extend([{"role": "customer", "text": message},
                                    {"role": "billing", "text": "Your bill is $80."}])
            return SimpleNamespace(handoffs=[], tools=[], replied_by="billing", owner_after="concierge",
                                   trace_id="trace-one")

        async def aclose(self):
            closed.append(True)

    async def simulate(persona, transcript, **kwargs):
        assessed.append(list(transcript))
        return conversation.Simulated(text="another question" if verdict is None else "", verdict=verdict)

    monkeypatch.setattr(conversation, "Orchestrator", Agent)
    monkeypatch.setattr(conversation, "simulate", simulate)
    result = await conversation.run_conversation(topology="single", linkage="baggage", label=None,
                                                persona={"customer_id": "cust-1042", "opening": "My bill?"},
                                                max_turns=1, scenario="bill", traced_simulator=False)
    assert result.ended_by == ended_by
    assert result.turns == 1
    assert len(assessed) == 1 and assessed[0][-1]["text"] == "Your bill is $80."
    assert closed == [True]

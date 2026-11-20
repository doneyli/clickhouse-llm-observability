"""
The hub: one customer turn = one `support-turn` trace; one conversation = one session.

The orchestrator owns conversation state: who owns the conversation now (`owner`), the
open A2A task with each specialist, and the concierge's history. Per turn:

  owner == concierge  → run the concierge in-process. If it transfers, the hub performs
                        the transfer inside the concierge's observation.
  owner == specialist → `handoff(kind=continue)` on the open task, straight to the owner.

A specialist's reply decides what happens next: `input-required` keeps it as the owner;
`completed` with a hand-back returns the conversation to the concierge; `completed` with
a transfer request makes the hub transfer onward, up to MAX_HANDOFFS per turn (the F3
loop guard). The root observation records the turn's `agent_path` and `handoff_count`,
so an evaluator that sees only the root can still check the trajectory.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from handoff import config
from handoff.agents.base import AgentInput, Roster, run_agent
from handoff.agents.registry import SPECIALISTS
from handoff.linkage import DEFAULT_MODE, TurnContext
from handoff.tools.crm import RunState
from handoff.transport import A2ATransport, HandoffResult, LocalTransport

MAX_HANDOFFS = int(config.setting("HANDOFF_MAX_HANDOFFS", "4"))
FALLBACK_REPLY = "I'm sorry, I couldn't get that sorted right now. I've flagged it for a person to follow up."


@dataclass
class Conversation:
    customer_id: str
    topology: str
    scenario: str = "adhoc"
    session_id: str = field(default_factory=lambda: f"conv-{uuid.uuid4().hex[:8]}")
    owner: str = "concierge"
    tasks: dict = field(default_factory=dict)       # agent → {"task_id", "context_id"}
    histories: dict = field(default_factory=dict)   # in-process agent → message history
    note: str = ""                                  # what the concierge should know on its next turn
    turns: int = 0
    transfers: int = 0
    effects: dict = field(default_factory=lambda: {"credits": [], "technicians": []})
    transcript: list = field(default_factory=list)


@dataclass
class TurnResult:
    reply: str
    replied_by: str
    agent_path: list
    handoffs: list
    owner_after: str
    trace_id: str
    effects: dict


class Orchestrator:
    def __init__(self, topology: str = "a2a", linkage: str = DEFAULT_MODE, prompt_label: Optional[str] = None):
        if topology not in ("single", "local", "a2a"):
            raise ValueError(topology)
        self.topology, self.linkage, self.prompt_label = topology, linkage, prompt_label
        self.lf = config.get_langfuse("orchestrator")
        self.roster = Roster.from_registry()
        self._local_hist: dict = {}
        if topology == "a2a":
            self.transport = A2ATransport(linkage)
        else:
            self.transport = LocalTransport({a: self._local_agent(a) for a in SPECIALISTS})

    async def aclose(self) -> None:
        await self.transport.aclose()

    # --- in-process specialists (local topology) -------------------------------------------

    def _local_agent(self, name: str):
        async def call(*, message: str, payload: dict, turn: TurnContext, task_id: str, context_id: str,
                       frm: str, kind: str) -> HandoffResult:
            key = (turn.session_id, name)
            out = await run_agent(name, AgentInput(
                message=message, kind=kind, customer_id=str(payload.get("customer_id", "")), turn=turn,
                payload=payload if kind != "continue" else {}, history=self._local_hist.get(key, []) if kind != "delegate" else [],
                frm=frm, prompt_label=self.prompt_label), transport=self.transport, state=RunState())
            if kind != "delegate":
                self._local_hist[key] = out.history
            return HandoffResult(state=out.state, text=out.text, task_id=f"local-{name}",
                                 context_id=f"local-{turn.session_id}-{name}",
                                 data={"effects": out.effects, "path": out.path, "transfer": out.transfer,
                                       "handback": out.handback})
        return call

    # --- the turn -------------------------------------------------------------------------

    async def turn(self, conv: Conversation, message: str) -> TurnResult:
        from langfuse import propagate_attributes

        conv.turns += 1
        path: list = []
        handoffs: list = []
        turn_ctx = TurnContext(session_id=conv.session_id, user_id=conv.customer_id, trace_name="support-turn")
        owner_before = conv.owner
        with propagate_attributes(session_id=conv.session_id, user_id=conv.customer_id, trace_name="support-turn",
                                  tags=[f"topology:{self.topology}", f"scenario:{conv.scenario}"]):
            with self.lf.start_as_current_observation(
                    as_type="agent", name="support-turn", input={"message": message},
                    metadata={"role": "orchestrator", "topology": self.topology,
                              "linkage": self.linkage if self.topology == "a2a" else "in-process",
                              "customer_id": conv.customer_id, "turn": str(conv.turns),
                              "owner_before": owner_before, "scenario": conv.scenario}) as root:
                trace_id = self.lf.get_current_trace_id() or ""
                if self.topology == "single":
                    reply, replied_by = await self._single(conv, message, turn_ctx, path)
                elif conv.owner == "concierge":
                    reply, replied_by = await self._concierge(conv, message, turn_ctx, path, handoffs)
                else:
                    reply, replied_by = await self._continue(conv, message, turn_ctx, path, handoffs)
                count = 0 if self.topology == "single" else len(path) - (1 if path[:1] == ["concierge"] else 0)
                root.update(output={"reply": reply, "replied_by": replied_by},
                            metadata={"agent_path": ">".join(path), "handoff_count": str(count),
                                      "owner_after": conv.owner, "replied_by": replied_by})
        conv.transcript += [{"role": "customer", "text": message}, {"role": replied_by, "text": reply}]
        return TurnResult(reply=reply, replied_by=replied_by, agent_path=path, handoffs=handoffs,
                          owner_after=conv.owner, trace_id=trace_id, effects=conv.effects)

    async def _single(self, conv, message, turn_ctx, path):
        path.append("single")
        state = RunState()
        out = await run_agent("single", AgentInput(message=message, kind="customer", customer_id=conv.customer_id,
                                                   turn=turn_ctx, history=conv.histories.get("single", []),
                                                   prompt_label=self.prompt_label),
                              transport=None, state=state)
        conv.histories["single"] = out.history
        self._merge(conv, out.effects)
        return out.text, "single"

    async def _concierge(self, conv, message, turn_ctx, path, handoffs):
        path.append("concierge")
        replied = {"by": "concierge"}

        async def on_transfer(request: dict):
            text, by = await self._transfer_chain(conv, "concierge", request, message, turn_ctx, path, handoffs)
            replied["by"] = by
            return text, "completed"

        user = f"{conv.note}\n\n{message}".strip() if conv.note else message
        conv.note = ""
        out = await run_agent("concierge", AgentInput(message=user, kind="customer", customer_id=conv.customer_id,
                                                      turn=turn_ctx, history=conv.histories.get("concierge", []),
                                                      prompt_label=self.prompt_label),
                              transport=self.transport, state=RunState(), roster=self.roster, on_transfer=on_transfer)
        conv.histories["concierge"] = out.history
        path += [p for p in out.path if p]
        handoffs += [{"kind": "delegate", "from": "concierge", "to": p} for p in out.path if p in SPECIALISTS]
        self._merge(conv, out.effects)
        return out.text, replied["by"]

    async def _continue(self, conv, message, turn_ctx, path, handoffs):
        owner = conv.owner
        ref = conv.tasks.get(owner, {})
        res = await self.transport.handoff(frm="router", to=owner, kind="continue", reason="customer reply",
                                           message=message, payload={"customer_id": conv.customer_id}, turn=turn_ctx,
                                           task_id=ref.get("task_id", ""), context_id=ref.get("context_id", ""), hop=1)
        handoffs.append({"kind": "continue", "from": "router", "to": owner, "state": res.state})
        path.append(owner)
        return await self._after(conv, owner, res, message, turn_ctx, path, handoffs)

    async def _transfer_chain(self, conv, frm, request, message, turn_ctx, path, handoffs):
        to = request["to"]
        if len(handoffs) >= MAX_HANDOFFS:
            conv.owner = "concierge"
            return FALLBACK_REPLY, "router"
        ref = conv.tasks.get(to, {})
        res = await self.transport.handoff(frm=frm, to=to, kind="transfer", reason=request.get("reason", ""),
                                           message=request.get("message") or message,
                                           payload={"customer_id": conv.customer_id, **(request.get("payload") or {})},
                                           turn=turn_ctx, context_id=ref.get("context_id", ""), hop=len(handoffs) + 1)
        handoffs.append({"kind": "transfer", "from": frm, "to": to, "state": res.state})
        conv.transfers += 1
        path.append(to)
        return await self._after(conv, to, res, message, turn_ctx, path, handoffs)

    async def _after(self, conv, agent, res: HandoffResult, message, turn_ctx, path, handoffs):
        """Apply a specialist's reply to the conversation state."""
        conv.tasks[agent] = {"task_id": res.task_id, "context_id": res.context_id}
        self._merge(conv, res.data.get("effects") or {})
        path += [p for p in (res.data.get("path") or []) if p]
        if res.state == "failed":
            conv.owner = "concierge"
            return FALLBACK_REPLY, "router"
        if res.state == "input-required":
            conv.owner = agent
            return res.text, agent
        conv.tasks[agent]["task_id"] = ""  # the task ended; a later transfer opens a new one in the same context
        onward = res.data.get("transfer")
        if onward:
            return await self._transfer_chain(conv, agent, onward, message, turn_ctx, path, handoffs)
        conv.owner = "concierge"
        handback = res.data.get("handback") or {}
        conv.note = f"[{agent} handed the customer back to you. Summary: {handback.get('summary', res.text)[:400]}]"
        return res.text, agent

    @staticmethod
    def _merge(conv, effects: dict) -> None:
        for k in ("credits", "technicians"):
            for e in effects.get(k) or []:
                if e not in conv.effects[k]:
                    conv.effects[k].append(e)

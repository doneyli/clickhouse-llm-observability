"""
How a handoff reaches the target agent: in-process (`local`) or over A2A (`a2a`).

Both transports record the handoff the same way: one `tool` observation named `handoff`
whose input is the payload sent and whose output is what came back (SPEC §6). The A2A
transport injects trace headers *inside* that observation, so the remote agent's spans
hang under it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from handoff import config
from handoff.linkage import DEFAULT_MODE, TurnContext, outbound


@dataclass
class HandoffResult:
    state: str  # completed | input-required | failed
    text: str
    data: dict = field(default_factory=dict)
    task_id: str = ""
    context_id: str = ""


def _handoff_observation(*, frm: str, to: str, kind: str, reason: str, message: str, payload: dict,
                         hop: int, linkage: str, transport: str):
    from handoff.agents.registry import AGENTS, roster_text

    lf = config.get_langfuse(config.SERVICE or "orchestrator")
    # An online evaluator sees only this observation, so what it needs to judge the handoff is
    # written here: the target's required fields (code evaluator) and the roster (LLM judge).
    return lf.start_as_current_observation(
        as_type="tool", name="handoff",
        input={"to": to, "kind": kind, "reason": reason, "message": message, "payload": payload},
        metadata={"role": "handoff", "handoff_kind": kind, "handoff_from": frm, "handoff_to": to,
                  "handoff_hop": str(hop), "handoff_reason": reason[:200], "linkage": linkage,
                  "transport": transport,
                  "required_fields": ",".join(AGENTS[to].required_fields) if to in AGENTS else "",
                  "roster": roster_text()})


def _record(obs, res: HandoffResult, extra: Optional[dict] = None) -> None:
    obs.update(output={"state": res.state, "reply": res.text, **({"data": res.data} if res.data else {})},
               level="ERROR" if res.state == "failed" else "DEFAULT",
               status_message=res.text[:200] if res.state == "failed" else None,
               metadata={"a2a_state": res.state, **(extra or {})})


class A2ATransport:
    name = "a2a"

    def __init__(self, linkage: str = DEFAULT_MODE):
        from handoff.a2a_client import A2AClientPool

        self.linkage = linkage
        self.pool = A2AClientPool()

    async def handoff(self, *, frm: str, to: str, kind: str, reason: str, message: str, payload: dict,
                      turn: TurnContext, task_id: str = "", context_id: str = "", hop: int = 1,
                      linkage: Optional[str] = None, conversation_id: str = "",
                      prompt_label: Optional[str] = None) -> HandoffResult:
        from handoff.a2a_client import A2AError

        mode = linkage or self.linkage
        url = config.agent_url(to)
        with _handoff_observation(frm=frm, to=to, kind=kind, reason=reason, message=message, payload=payload,
                                  hop=hop, linkage=mode, transport=self.name) as obs:
            try:
                with outbound(mode, turn) as (headers, meta):
                    # Business data, not tracing: the case the work belongs to travels with the
                    # message in every linkage mode, so the ledger works even when traces split.
                    meta = {**meta, "handoff_linkage": mode, "handoff_kind": kind, "handoff_from": frm,
                            "handoff_hop": str(hop), "customer_id": str(payload.get("customer_id", "")),
                            "conversation_id": conversation_id or turn.session_id or "",
                            **({"prompt_label": prompt_label} if prompt_label else {})}
                    r = await self.pool.send(url, text=message, data=payload, task_id=task_id,
                                             context_id=context_id, headers=headers, metadata=meta)
                res = HandoffResult(state=r.state, text=r.text, data=r.data, task_id=r.task_id,
                                    context_id=r.context_id)
                _record(obs, res, {"a2a_task_id": r.task_id, "a2a_context_id": r.context_id,
                                   "a2a_agent_version": r.agent_version,
                                   "linked_by": " + ".join(sorted(headers)) or ("a2a metadata" if mode == "session" else "none")})
            except A2AError as exc:
                res = HandoffResult(state="failed", text=f"{to} unavailable: {exc}")
                _record(obs, res)
            return res

    async def aclose(self) -> None:
        await self.pool.aclose()


LocalHandler = Callable[..., Awaitable[HandoffResult]]


class LocalTransport:
    """In-process handoff: the target agent runs inside the `handoff` observation."""

    name = "local"

    def __init__(self, agents: dict[str, LocalHandler]):
        self.agents = agents
        self.linkage = "in-process"

    async def handoff(self, *, frm: str, to: str, kind: str, reason: str, message: str, payload: dict,
                      turn: TurnContext, task_id: str = "", context_id: str = "", hop: int = 1,
                      linkage: Optional[str] = None, conversation_id: str = "",
                      prompt_label: Optional[str] = None) -> HandoffResult:
        with _handoff_observation(frm=frm, to=to, kind=kind, reason=reason, message=message, payload=payload,
                                  hop=hop, linkage="in-process", transport=self.name) as obs:
            try:
                res = await self.agents[to](message=message, payload=payload, turn=turn, task_id=task_id,
                                            context_id=context_id, frm=frm, kind=kind,
                                            conversation_id=conversation_id or turn.session_id or "")
            except Exception as exc:  # noqa: BLE001 — same contract as a failed A2A task
                res = HandoffResult(state="failed", text=f"{to} failed: {type(exc).__name__}: {exc}"[:300])
            _record(obs, res, {"a2a_task_id": res.task_id, "a2a_context_id": res.context_id})
            return res

    async def aclose(self) -> None:
        return None

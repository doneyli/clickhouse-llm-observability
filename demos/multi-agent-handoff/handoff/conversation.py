"""
Multi-turn conversations driven by a simulated customer (SPEC §7, `handoff-conversations`).

A simulator (Haiku 5.5) plays the customer: a persona, a goal, and the facts the customer
knows. It reads the chat so far and returns structured output: a status (`continue`,
`done` when the goal is met or a clear answer is accepted, `give_up`) and the next message.
An explicit status field ends conversations far more reliably than asking for a DONE token. Each customer message is one
orchestrator turn: in production one `support-turn` trace per turn, all in one session;
in an experiment, the whole conversation is one item, so a multi-turn problem becomes a
single-item experiment.

Conversation-level scores (C3) are written on the session: turns used, transfers, how it
ended, and, when the expected outcome is known, whether the ledger matches it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from handoff import config
from handoff.agents.base import anthropic_client
from handoff.orchestrator import Conversation, Orchestrator

SIMULATOR_MODEL = config.setting("HANDOFF_SIMULATOR_MODEL", "claude-haiku-5-5")

SIMULATOR = """You are role-playing a customer in a support chat with Northwind Mobile, a phone and
home-internet provider. Stay in character.

Persona: {persona}
Your goal: {goal}
What you know (use it when relevant; never invent other account details): {facts}

Write only your next chat message: one or two short sentences, no quotes, no stage directions.
Answer the agent's questions truthfully from what you know.
Pursue only your goal. Do not raise new topics or ask for details beyond it (exact appointment times,
phone numbers, itemized statements) unless the goal or what you know says so.

Set status:
- "done" as soon as your goal is met, or the agent clearly explained why it cannot be and you accept that
  (message: an empty string)
- "give_up" if the agent keeps failing or repeats itself (message: an empty string)
- "continue" otherwise, with your next message"""

REPLY_FORMAT = {"type": "json_schema", "schema": {
    "type": "object", "additionalProperties": False, "required": ["status", "message"],
    "properties": {"status": {"type": "string", "enum": ["continue", "done", "give_up"]},
                   "message": {"type": "string"}}}}


@dataclass
class Simulated:
    text: str = ""
    verdict: Optional[str] = None  # None | "done" | "gave-up"


async def simulate(persona: dict, transcript: list[dict], *, traced: bool) -> Simulated:
    chat = "\n".join(f"{'You' if m['role'] == 'customer' else 'Agent'}: {m['text']}" for m in transcript)
    system = SIMULATOR.format(persona=persona["persona"], goal=persona["goal"], facts=persona["facts"])
    user = f"The chat so far:\n\n{chat}\n\nWrite your next message."

    async def call():
        # A one-line role-play reply needs no reasoning. Haiku 5.5 thinks by default and thinking tokens
        # count against max_tokens, so with thinking on a small budget can end with no text at all.
        return await anthropic_client().messages.create(
            model=SIMULATOR_MODEL, max_tokens=1024, system=system, messages=[{"role": "user", "content": user}],
            thinking={"type": "disabled"}, output_config={"effort": "low", "format": REPLY_FORMAT})

    if traced:  # experiments: the simulator's cost is part of the run; tagged so no rule ever judges it
        lf = config.get_langfuse(config.SERVICE or "orchestrator")
        with lf.start_as_current_observation(as_type="generation", name="customer-simulator", model=SIMULATOR_MODEL,
                                             input=[{"role": "system", "content": system},
                                                    {"role": "user", "content": user}],
                                             metadata={"role": "simulator"}) as gen:
            resp = await call()
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            gen.update(output=text, usage_details={"input": resp.usage.input_tokens,
                                                   "output": resp.usage.output_tokens})
    else:
        resp = await call()
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        reply = json.loads(text)
    except ValueError:
        return Simulated(text="")  # treated as an empty reply by the caller
    if reply.get("status") == "done":
        return Simulated(verdict="done")
    if reply.get("status") == "give_up":
        return Simulated(verdict="gave-up")
    return Simulated(text=str(reply.get("message") or ""))


@dataclass
class ConversationResult:
    session_id: str
    turns: int
    ended_by: str  # customer-done | customer-gave-up | customer-silent | max-turns
    transcript: list
    replied_by: list  # who answered each turn
    owners: list      # who owned the conversation after each turn
    handoffs: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    effects: dict = field(default_factory=dict)
    trace_ids: list = field(default_factory=list)

    @property
    def transfers(self) -> int:
        return sum(1 for h in self.handoffs if h.get("kind") == "transfer")

    @property
    def first_handoff(self) -> Optional[str]:
        return next((h["to"] for h in self.handoffs if h.get("kind") != "continue"), None)

    def record(self, topology: str) -> dict:
        """The run record the evaluators read (same shape as a routing run, plus the conversation)."""
        path = ["concierge"] if topology != "single" else ["single"]
        for h in self.handoffs:
            if h.get("kind") != "continue" and (not path or path[-1] != h["to"]):
                path.append(h["to"])
        return {"reply": "\n".join(m["text"] for m in self.transcript if m["role"] != "customer"),
                "transcript": self.transcript, "turns": self.turns, "ended_by": self.ended_by,
                "replied_by": self.replied_by, "owners": self.owners, "agent_path": path,
                "first_handoff": self.first_handoff, "transfers": self.transfers,
                "handoffs": [{k: h.get(k) for k in ("kind", "from", "to", "payload", "message", "state")}
                             for h in self.handoffs],
                "tools": self.tools, "effects": self.effects, "topology": topology, "session_id": self.session_id}


async def run_conversation(*, topology: str, linkage: str, label: Optional[str], persona: dict, max_turns: int,
                           scenario: str, traced_simulator: bool) -> ConversationResult:
    orch = Orchestrator(topology=topology, linkage=linkage, prompt_label=label)
    conv = Conversation(customer_id=persona["customer_id"], topology=topology, scenario=scenario)
    res = ConversationResult(session_id=conv.session_id, turns=0, ended_by="max-turns", transcript=[],
                             replied_by=[], owners=[])
    message = persona["opening"]
    try:
        for t in range(max_turns):
            r = await orch.turn(conv, message)
            res.handoffs += r.handoffs
            res.tools += r.tools
            res.replied_by.append(r.replied_by)
            res.owners.append(r.owner_after)
            res.trace_ids.append(r.trace_id)
            if t == max_turns - 1:
                break
            nxt = await simulate(persona, conv.transcript, traced=traced_simulator)
            if not nxt.verdict and not nxt.text.strip():  # an empty reply: ask once more, then stop
                nxt = await simulate(persona, conv.transcript, traced=traced_simulator)
                if not nxt.verdict and not nxt.text.strip():
                    nxt = Simulated(verdict="silent")
            if nxt.verdict:
                res.ended_by = f"customer-{nxt.verdict}"
                break
            message = nxt.text
    finally:
        await orch.aclose()
    res.turns, res.transcript, res.effects = conv.turns, list(conv.transcript), conv.effects
    return res


def score_session(result: ConversationResult, expected: Optional[dict] = None) -> list[tuple]:
    """C3: conversation-level scores on the session (there is no session-target judge)."""
    from handoff.evals import resolution_correct

    lf = config.get_langfuse(config.SERVICE or "orchestrator")
    scores = [("turns-to-resolution", float(result.turns), "NUMERIC", f"ended by {result.ended_by}"),
              ("transfers", float(result.transfers), "NUMERIC", " > ".join(result.owners)),
              ("conversation-ended-by", result.ended_by, "CATEGORICAL", None)]
    if expected:
        ev = resolution_correct(output=result.record("a2a"), expected_output=expected)[0]
        scores.append(("resolution-correct", ev.value, "BOOLEAN", ev.comment))
    for name, value, dtype, comment in scores:
        lf.create_score(session_id=result.session_id, name=name, value=value, data_type=dtype, comment=comment,
                        environment=config.current_environment())
    return scores

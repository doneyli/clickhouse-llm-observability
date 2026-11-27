"""
The agent loop shared by every agent: a traced tool-use loop on the Anthropic SDK.

Each model call is one `generation` observation (`<agent>-llm`) with model, usage, the
messages the model saw, and what it returned. Each domain tool call is one `tool`
observation. Handoff tools are handled here too:

  ask_<agent>          delegate: the transport sends the question, the answer comes back as
                       the tool result, and this agent carries on;
  transfer_to_<agent>  ends this agent's turn with a transfer request for the hub;
  hand_back            ends this agent's turn and returns the conversation to the concierge.

The Anthropic SDK's own OpenTelemetry spans and header propagation are switched off:
its spans carry no content by default, and its propagation would put the caller's
session/user baggage on every request to the model API.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from handoff import config, prompts
from handoff.agents.registry import AGENTS, AgentSpec
from handoff.linkage import TurnContext
from handoff.tools import schemas
from handoff.tools.crm import RunState

MAX_STEPS = 8
_client = None


def anthropic_client():
    global _client
    if _client is None:
        import anthropic

        key = config.setting("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(f"ANTHROPIC_API_KEY missing from {config.DEMO_DIR / '.env'}")
        _client = anthropic.AsyncAnthropic(api_key=key, max_retries=3,
                                           open_telemetry={"traces": False, "propagation": False})
    return _client


def tool_name(agent: str) -> str:
    return agent.replace("-", "_")


@dataclass
class Roster:
    """What each agent owns, as the routing agent sees it (from registry or A2A agent cards)."""

    entries: dict[str, dict]  # name → {"description", "required_fields", "skills", "version"}

    @classmethod
    def from_registry(cls) -> "Roster":
        return cls({n: {"description": a.description, "required_fields": a.required_fields,
                        "skills": [s["description"] for s in a.skills], "version": config.RELEASE}
                    for n, a in AGENTS.items() if n not in ("concierge", "single")})

    @classmethod
    async def from_cards(cls, names) -> "Roster":
        """A2A discovery: each specialist's `/.well-known/agent-card.json`, registry as fallback."""
        import httpx

        entries = cls.from_registry().entries
        async with httpx.AsyncClient(timeout=5) as client:
            for n in names:
                try:
                    card = (await client.get(f"{config.agent_url(n)}/.well-known/agent-card.json")).json()
                except (httpx.HTTPError, ValueError):
                    continue  # agent down: keep the registry entry; the handoff itself will fail cleanly
                entries[n] = {"description": card.get("description", ""),
                              "skills": [sk.get("description", "") for sk in card.get("skills", [])],
                              "version": card.get("version", ""), "source": "agent-card",
                              "required_fields": AGENTS[n].required_fields}
        return cls(entries)

    @property
    def source(self) -> str:
        return "agent-card" if any(e.get("source") == "agent-card" for e in self.entries.values()) else "registry"

    def text(self, names: list[str]) -> str:
        lines = []
        for n in names:
            e = self.entries.get(n)
            if e:
                skills = "; ".join(e["skills"])
                lines.append(f"- {n}: {e['description']}" + (f" ({skills})" if skills else ""))
        return "\n".join(lines)


@dataclass
class AgentInput:
    message: str
    kind: str  # customer | transfer | continue | delegate
    customer_id: str
    turn: TurnContext
    payload: dict = field(default_factory=dict)
    history: list = field(default_factory=list)  # this agent's prior messages in this conversation
    frm: str = ""
    hop: int = 0
    prompt_label: Optional[str] = None


@dataclass
class AgentOutput:
    text: str
    state: str = "completed"  # completed | input-required | failed
    transfer: Optional[dict] = None  # {"to", "reason", "message", "payload"}
    handback: Optional[dict] = None  # {"summary", "outcome"}
    effects: dict = field(default_factory=lambda: {"credits": [], "technicians": []})
    history: list = field(default_factory=list)
    path: list = field(default_factory=list)  # agents reached by this run's delegations
    prompt_version: str = "fallback"
    tools: list = field(default_factory=list)  # "agent:tool" in call order, delegations included
    handoffs: list = field(default_factory=list)  # this run's delegations (and theirs), with payloads


def _handoff_schema(target: str, kind: str) -> dict:
    spec = AGENTS[target]
    props: dict[str, Any] = {"reason": {"type": "string", "description": "Why this agent, in one sentence."}}
    if kind == "transfer":
        props["issue_summary"] = {"type": "string",
                                  "description": "Everything the next agent needs, so the customer never repeats it."}
        desc = f"Transfer the conversation to {target}: {spec.description}"
    else:
        props["question"] = {"type": "string", "description": "The question or request for the other agent."}
        desc = f"Ask {target} one question and get the answer back (you keep the conversation): {spec.description}"
    for f in spec.required_fields:
        props.setdefault(f, {"type": "string"})
    props.update({"outage_id": {"type": "string"}, "symptom": {"type": "string"}})
    required = ["reason", "issue_summary" if kind == "transfer" else "question"] + [
        f for f in spec.required_fields if f not in ("issue_summary",)]
    prefix = "transfer_to_" if kind == "transfer" else "ask_"
    return {"name": f"{prefix}{tool_name(target)}", "description": desc,
            "input_schema": {"type": "object", "properties": props, "required": required}}


HAND_BACK = {"name": "hand_back", "description": (
    "Return the customer to the concierge once their issue with you is resolved and they need nothing else "
    "from you. Write your closing message to the customer in `closing_message`."),
    "input_schema": {"type": "object", "properties": {
        "summary": {"type": "string"}, "outcome": {"type": "string"},
        "closing_message": {"type": "string"}}, "required": ["summary", "outcome", "closing_message"]}}


def _tools_for(spec: AgentSpec, kind: str) -> list[dict]:
    tools = [schemas.schema(t) for t in spec.tools]
    if kind != "delegate":
        tools += [_handoff_schema(t, "transfer") for t in spec.transfers]
        if spec.can_hand_back and kind in ("transfer", "continue"):
            tools.append(HAND_BACK)
    tools += [_handoff_schema(t, "ask") for t in spec.asks]
    return tools


def _target(name: str, prefix: str) -> str:
    raw = name[len(prefix):]
    return next(a for a in AGENTS if tool_name(a) == raw)


def _skipped(uses: list) -> list[dict]:
    return [{"type": "tool_result", "tool_use_id": u["id"], "content": "not run: the conversation moved on"}
            for u in uses]


def slim(payload: dict) -> dict:
    """F2: a 'token-saving' refactor that drops the line id from every handoff payload."""
    if os.environ.get("HANDOFF_SLIM_CONTEXT") == "1":
        return {k: v for k, v in payload.items() if k != "line_id"}
    return payload


_KEEP = {"text": ("type", "text"), "tool_use": ("type", "id", "name", "input"),
         "thinking": ("type", "thinking", "signature"), "redacted_thinking": ("type", "data")}


def _blocks(content) -> list[dict]:
    """Response blocks as dicts the API accepts back in history (thinking keeps its signature)."""
    out = []
    for b in content:
        d = b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else dict(b)
        keep = _KEEP.get(d.get("type"))
        out.append({k: v for k, v in d.items() if k in keep} if keep else d)
    return out


async def run_agent(name: str, inp: AgentInput, *, transport, state: RunState,
                    roster: Optional[Roster] = None, open_observation: bool = True,
                    on_transfer=None) -> AgentOutput:
    """One agent turn. `transport` performs ask_* delegations (local or A2A).

    `on_transfer(request) -> (reply, state)`: the hub's transfer routine. When given (the
    concierge, in the orchestrator's process), the transfer runs inside this agent's
    observation, so the trace shows the concierge handing off. Without it (a remote
    specialist), the transfer request is returned for the hub to perform.
    """
    spec = AGENTS[name]
    lf = config.get_langfuse(config.SERVICE or name)
    template, prompt_client = prompts.get(name, inp.prompt_label)
    roster = roster or Roster.from_registry()
    system = prompts.compile_text(template, customer_id=inp.customer_id, mode=prompts.MODES[inp.kind],
                                  roster=roster.text(spec.transfers + [a for a in spec.asks if a not in spec.transfers]))
    prompt_version = str(getattr(prompt_client, "version", "fallback"))
    user = inp.message if inp.message.strip() else "(empty message)"  # the API rejects empty user content
    if inp.payload and inp.kind in ("transfer", "delegate"):
        user = f"[handoff from {inp.frm}]\n{json.dumps(inp.payload, indent=1)}\n\n{inp.message}"
    messages = list(inp.history) + [{"role": "user", "content": user}]
    tools = _tools_for(spec, inp.kind)
    out = AgentOutput(text="", prompt_version=prompt_version)

    async def loop() -> None:
        for _ in range(MAX_STEPS):
            with lf.start_as_current_observation(
                    as_type="generation", name=f"{name}-llm", model=spec.model, prompt=prompt_client,
                    input=[{"role": "system", "content": system}, *messages],
                    metadata={"agent": name, "kind": inp.kind}) as gen:
                resp = await anthropic_client().messages.create(
                    model=spec.model, max_tokens=1024, system=system, messages=messages, tools=tools)
                blocks = _blocks(resp.content)
                u = resp.usage
                gen.update(output={"role": "assistant", "content": blocks},
                           usage_details={"input": u.input_tokens, "output": u.output_tokens,
                                          **({"cache_read_input_tokens": u.cache_read_input_tokens}
                                             if getattr(u, "cache_read_input_tokens", None) else {})},
                           metadata={"stop_reason": resp.stop_reason})
            messages.append({"role": "assistant", "content": blocks})
            text = "\n".join(b["text"] for b in blocks if b["type"] == "text").strip()
            uses = [b for b in blocks if b["type"] == "tool_use"]
            if not uses:
                out.text = text
                out.state = "input-required" if inp.kind in ("transfer", "continue") else "completed"
                return
            results = []
            for i, tu in enumerate(uses):
                tname, args = tu["name"], dict(tu.get("input") or {})
                if tname.startswith("transfer_to_"):
                    target = _target(tname, "transfer_to_")
                    out.transfer = {"to": target, "reason": args.pop("reason", ""),
                                    "message": args.get("issue_summary", ""), "payload": slim(args)}
                    out.text = text
                    out.state = "completed"
                    messages.append({"role": "user", "content": results + [
                        {"type": "tool_result", "tool_use_id": tu["id"], "content": f"transferred to {target}"}]
                        + _skipped(uses[i + 1:])})
                    if on_transfer is not None:
                        out.text, out.state = await on_transfer(out.transfer)
                    return
                if tname == "hand_back":
                    out.handback = {"summary": args.get("summary", ""), "outcome": args.get("outcome", "")}
                    out.text = args.get("closing_message") or text
                    out.state = "completed"
                    messages.append({"role": "user", "content": results + [
                        {"type": "tool_result", "tool_use_id": tu["id"], "content": "handed back"}]
                        + _skipped(uses[i + 1:])})
                    return
                if tname.startswith("ask_"):
                    target = _target(tname, "ask_")
                    reason = args.pop("reason", "")
                    question = args.pop("question", "")
                    payload = slim({"customer_id": inp.customer_id, **args})
                    started = time.time()
                    res = await transport.handoff(frm=name, to=target, kind="delegate", reason=reason,
                                                  message=question, payload=payload,
                                                  turn=inp.turn, hop=inp.hop + 1,
                                                  conversation_id=state.conversation_id or "",
                                                  prompt_label=inp.prompt_label)
                    out.handoffs.append({"kind": "delegate", "from": name, "to": target, "payload": payload,
                                         "message": question, "state": res.state, "t": started})
                    out.handoffs += res.data.get("handoffs") or []
                    out.tools += res.data.get("tools") or []
                    out.path.append(target)
                    for k in ("credits", "technicians"):
                        out.effects[k] += (res.data.get("effects") or {}).get(k, [])
                    out.path += [p for p in (res.data.get("path") or []) if p]
                    content = res.text if res.state != "failed" else f"ERROR: {res.text}"
                else:
                    out.tools.append(f"{name}:{tname}")
                    with lf.start_as_current_observation(as_type="tool", name=tname, input=args) as t:
                        result = schemas.call(tname, state, args)
                        t.update(output=result, level="WARNING" if isinstance(result, dict) and "error" in result else "DEFAULT")
                    content = json.dumps(result)
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": content})
            messages.append({"role": "user", "content": results})
        out.text = "Sorry — I couldn't finish that. Let me get a person to help."
        out.state = "failed"

    if open_observation:
        with lf.start_as_current_observation(
                as_type="agent", name=name, input={"message": inp.message, "handoff": inp.payload or None},
                metadata={"role": "agent", "agent": name, "team": spec.team, "service": config.SERVICE or name,
                          "kind": inp.kind, "prompt_version": prompt_version}) as obs:
            await loop()
            obs.update(output=_output(out), level="ERROR" if out.state == "failed" else "DEFAULT")
    else:
        await loop()
    out.effects["credits"] += state.credits
    out.effects["technicians"] += state.technicians
    out.history = messages
    return out


def _output(out: AgentOutput) -> dict:
    d: dict[str, Any] = {"reply": out.text, "state": out.state}
    if out.transfer:
        d["transfer"] = {"to": out.transfer["to"], "reason": out.transfer["reason"]}
    if out.handback:
        d["handback"] = out.handback
    return d

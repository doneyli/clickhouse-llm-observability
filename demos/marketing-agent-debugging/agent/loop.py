"""
The marketing agent: a Claude tool-use loop where the MODEL decides which tool to call
next, instrumented with the Langfuse Python SDK v4.

Trace shape (one trace per agent run; multi-turn chats share a session):

  marketing-agent                  agent      input = the marketer's task, output = final reply
  ├─ plan-next-step                generation one per model call: tools + full messages +
  │                                           summarized thinking; linked instructions version
  ├─ get_visibility_report         tool       sibling of the generation that requested it
  ├─ retrieve_brand_context        retriever  passages the model saw; index records in metadata
  ├─ check_claims                  tool
  │  └─ verify-claims              generation the fact-checker's own model call
  ├─ plan-next-step                generation ...
  └─ check-publishing-policy       guardrail  fact-check-before-publish + step limit

Scores on the trace: reviewer-decision (categorical + comment), agent-steps (numeric),
and publishing-policy (boolean, on the guardrail observation).

The generation input uses the OpenAI chat shape ({"tools": [...], "messages": [...]})
because that is what Langfuse parses for "available tools", tool-call cards and the
`thinking` field — so every step shows the tool menu the model chose from.
"""

from __future__ import annotations

import contextvars
import json
import random
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from langfuse import propagate_attributes

from . import config
from .instructions import fallback_text, prompt_name
from .review import policy_check, review, summarize_calls
from .tools import (FIXES, RETRIEVER_TOOLS, TOOL_DEFINITIONS, RunState, ToolContext, ToolOutcome,
                    run_tool)
from .world import get_customer

OPENAI_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["input_schema"]}} for t in TOOL_DEFINITIONS]

_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {"claim": {"type": "string"},
                       "verdict": {"type": "string", "enum": ["supported", "contradicted", "unsupported"]},
                       "evidence": {"type": "string"}},
        "required": ["claim", "verdict", "evidence"], "additionalProperties": False}}},
    "required": ["results"], "additionalProperties": False,
}

_VERIFY_SYSTEM = ("You are a fact-checker. For each claim decide, using ONLY the evidence passages "
                  "given for it: 'supported' if the evidence states it, 'contradicted' if the evidence "
                  "states something incompatible, 'unsupported' otherwise. Quote the evidence briefly.")


@dataclass
class RunResult:
    trace_id: str
    url: str
    final_text: str
    steps: int
    step_limit_hit: bool
    duration_s: float
    decision: Optional[str]
    decision_comment: Optional[str]
    instructions_version: Optional[int]
    state: RunState = field(repr=False, default_factory=RunState)


def _usage(u) -> Dict[str, int]:
    usage = {"input": u.input_tokens, "output": u.output_tokens}
    for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        val = getattr(u, key, None)
        if val:
            usage[key] = val
    return usage


def _assistant_trace_message(blocks) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"role": "assistant",
                           "content": "\n".join(b.text for b in blocks if b.type == "text")}
    tool_calls = [{"id": b.id, "type": "function",
                   "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                  for b in blocks if b.type == "tool_use"]
    if tool_calls:
        msg["tool_calls"] = tool_calls
    thinking = [{"type": "thinking", "content": b.thinking}
                for b in blocks if b.type == "thinking" and getattr(b, "thinking", "")]
    if thinking:
        msg["thinking"] = thinking
    return msg


def _make_claim_verifier(lf, client) -> Callable:
    def verify(claims: List[str], evidence: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        blocks = []
        for i, (claim, passages) in enumerate(zip(claims, evidence), 1):
            ev = "\n".join(f"  - [{p['title']}] {p['content']}" for p in passages) or "  (no evidence found)"
            blocks.append(f"Claim {i}: {claim}\nEvidence:\n{ev}")
        user = "\n\n".join(blocks)
        with lf.start_as_current_observation(
                as_type="generation", name="verify-claims", model=config.FACTCHECK_MODEL,
                input=[{"role": "system", "content": _VERIFY_SYSTEM}, {"role": "user", "content": user}],
                model_parameters={"effort": "low", "output_format": "json_schema"}) as gen:
            resp = client.messages.create(
                model=config.FACTCHECK_MODEL, max_tokens=8000, system=_VERIFY_SYSTEM,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": _VERIFY_SCHEMA}})
            text = next((b.text for b in resp.content if b.type == "text"), "{}")
            data = json.loads(text)
            gen.update(output=data, model=resp.model, usage_details=_usage(resp.usage))
        return data.get("results", [])
    return verify


def _traced_tool(lf, ctx: ToolContext, tool_use) -> ToolOutcome:
    as_type = "retriever" if tool_use.name in RETRIEVER_TOOLS else "tool"
    with lf.start_as_current_observation(as_type=as_type, name=tool_use.name, input=tool_use.input,
                                         metadata={"tool_call_id": tool_use.id}) as obs:
        try:
            outcome = run_tool(ctx, tool_use.name, dict(tool_use.input))
        except Exception as e:  # a backend failure: show it in the trace AND tell the model
            outcome = ToolOutcome({"error": f"{type(e).__name__}: {e}"}, level="ERROR",
                                  status_message=f"{type(e).__name__}: {e}"[:200], is_error=True)
        update: Dict[str, Any] = {"output": outcome.result,
                                  "metadata": {"tool_call_id": tool_use.id, **outcome.metadata}}
        if outcome.level:
            update.update(level=outcome.level, status_message=outcome.status_message)
        obs.update(**update)
    return outcome


def _run_tools(lf, ctx: ToolContext, tool_uses: list) -> List[ToolOutcome]:
    # Default harness behaviour: execute a turn's tool calls one after another, even when the
    # model asked for several at once. `--fix parallel-tools` runs them concurrently.
    if "parallel-tools" in ctx.fixes and len(tool_uses) > 1:
        with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _traced_tool, lf, ctx, tu) for tu in tool_uses]
            return [f.result() for f in futures]
    return [_traced_tool(lf, ctx, tu) for tu in tool_uses]


def _call_model(client, system_text: str, messages: list):
    kwargs: Dict[str, Any] = dict(
        model=config.AGENT_MODEL, max_tokens=16000, system=system_text, messages=messages,
        tools=TOOL_DEFINITIONS, thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": config.AGENT_EFFORT}, cache_control={"type": "ephemeral"})
    if config.AGENT_FALLBACKS != "off":
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks=config.AGENT_FALLBACKS)
    return client.beta.messages.create(**kwargs)


def run_agent(customer_id: str, task: str, *, task_kind: str = "adhoc", user_id: Optional[str] = None,
              session_id: Optional[str] = None, history: Optional[List[Dict[str, str]]] = None,
              fixes: Iterable[str] = (), prompt_label: str = "production", prompt_version: Optional[int] = None,
              surface: str = "ai-marketer-chat", do_review: bool = True, seed: Optional[int] = None,
              on_event: Callable[[str], None] = lambda _msg: None) -> RunResult:
    fixes = set(fixes)
    unknown = fixes - FIXES
    if unknown:
        raise ValueError(f"unknown fix(es) {sorted(unknown)}; choose from {sorted(FIXES)}")
    lf, client = config.get_langfuse(), config.get_anthropic()
    customer = get_customer(customer_id)
    user_id = user_id or customer.marketers[0]

    if prompt_version is not None:
        prompt = lf.get_prompt(prompt_name(customer_id), version=prompt_version,
                               fallback=fallback_text(customer_id))
    else:
        prompt = lf.get_prompt(prompt_name(customer_id), label=prompt_label, cache_ttl_seconds=20,
                               fallback=fallback_text(customer_id))
    system_text = prompt.compile()
    linked_prompt = None if prompt.is_fallback else prompt
    instructions_version = None if prompt.is_fallback else prompt.version

    state = RunState()
    ctx = ToolContext(customer=customer, fixes=fixes, state=state,
                      claim_verifier=_make_claim_verifier(lf, client), rng=random.Random(seed))
    history = list(history or [])
    api_messages: List[Dict[str, Any]] = [dict(m) for m in history] + [{"role": "user", "content": task}]
    trace_messages: List[Dict[str, Any]] = ([{"role": "system", "content": system_text}]
                                            + [dict(m) for m in history] + [{"role": "user", "content": task}])

    tags = [f"customer:{customer_id}", f"task:{task_kind}", f"surface:{surface}"]
    metadata = {"customer": customer_id, "task_type": task_kind,
                "instructions": f"{prompt_name(customer_id)} v{instructions_version or 'fallback'}",
                "fixes": ",".join(sorted(fixes)) or "none"}

    started = time.monotonic()
    final_text, steps, step_limit_hit = "", 0, False
    level = status = None
    with lf.start_as_current_observation(as_type="agent", name="marketing-agent", input=task) as root:
        with propagate_attributes(user_id=user_id, session_id=session_id, tags=tags, metadata=metadata,
                                  version=config.HARNESS_VERSION, trace_name="marketing-agent"):
            for step in range(1, config.MAX_STEPS + 1):
                steps = step
                on_event(f"step {step}: thinking…")
                with lf.start_as_current_observation(
                        as_type="generation", name="plan-next-step", model=config.AGENT_MODEL,
                        input={"tools": OPENAI_TOOLS, "messages": list(trace_messages)},
                        model_parameters={"thinking": "adaptive", "effort": config.AGENT_EFFORT,
                                          "max_tokens": 16000},
                        metadata={"step": step}, prompt=linked_prompt) as gen:
                    try:
                        resp = _call_model(client, system_text, api_messages)
                    except Exception as e:
                        gen.update(level="ERROR", status_message=f"{type(e).__name__}: {e}"[:300])
                        root.update(level="ERROR", status_message=f"model call failed: {type(e).__name__}")
                        raise
                    out_msg = _assistant_trace_message(resp.content)
                    gen_update: Dict[str, Any] = dict(output=out_msg, model=resp.model,
                                                      usage_details=_usage(resp.usage),
                                                      metadata={"step": step, "stop_reason": resp.stop_reason})
                    if resp.stop_reason in ("refusal", "max_tokens"):
                        gen_update.update(level="WARNING", status_message=f"stop_reason={resp.stop_reason}")
                    gen.update(**gen_update)

                api_messages.append({"role": "assistant", "content": resp.content})
                trace_messages.append(out_msg)
                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                if resp.stop_reason != "tool_use" or not tool_uses:
                    final_text = out_msg["content"]
                    if resp.stop_reason in ("refusal", "max_tokens"):
                        level, status = "WARNING", f"ended with stop_reason={resp.stop_reason}"
                    break

                for tu in tool_uses:
                    on_event(f"step {step}: → {tu.name}({json.dumps(tu.input)[:110]})")
                outcomes = _run_tools(lf, ctx, tool_uses)
                api_messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(o.result),
                     **({"is_error": True} if o.is_error else {})}
                    for tu, o in zip(tool_uses, outcomes)]})
                for tu, o in zip(tool_uses, outcomes):
                    trace_messages.append({"role": "tool", "tool_call_id": tu.id, "name": tu.name,
                                           "content": o.result})
            else:
                step_limit_hit = True
                level, status = "WARNING", f"step limit ({config.MAX_STEPS}) reached before a final answer"
                final_text = (f"I stopped after {config.MAX_STEPS} steps without finishing. "
                              f"Tool calls so far: {summarize_calls(state)}.")

            with lf.start_as_current_observation(
                    as_type="guardrail", name="check-publishing-policy",
                    input={"tool_calls": [c["name"] for c in state.calls], "step_limit_hit": step_limit_hit}) as guard:
                policy = policy_check(state, step_limit_hit)
                guard.update(output=policy, **({"level": "WARNING", "status_message": "; ".join(policy["violations"])}
                                               if not policy["passed"] else {}))
                guard_id = guard.id

            root_update: Dict[str, Any] = {"output": final_text,
                                           "metadata": {"tool_calls": summarize_calls(state) or "none",
                                                        "steps": steps}}
            if level:
                root_update.update(level=level, status_message=status)
            root.update(**root_update)
            trace_id = root.trace_id

    duration = time.monotonic() - started
    decision = comment = None
    if do_review:
        decision, comment = review(customer_id, state, final_text, step_limit_hit=step_limit_hit,
                                   duration_s=duration, task_kind=task_kind)
        lf.create_score(trace_id=trace_id, name="reviewer-decision", value=decision,
                        data_type="CATEGORICAL", comment=comment)
    lf.create_score(trace_id=trace_id, name="agent-steps", value=steps, data_type="NUMERIC")
    if policy["published_items"] or step_limit_hit:
        # On the guardrail observation that produced it: filter the guardrail's rows in the
        # Observations table (`name:check-publishing-policy scores.publishing-policy:false`).
        lf.create_score(trace_id=trace_id, observation_id=guard_id, name="publishing-policy",
                        value=1 if policy["passed"] else 0, data_type="BOOLEAN",
                        comment="; ".join(policy["violations"]) or "fact-checked before publishing")
    on_event(f"done in {duration:.0f}s, {steps} model calls" + (f", reviewer: {decision}" if decision else ""))
    return RunResult(trace_id=trace_id, url=config.trace_url(trace_id), final_text=final_text, steps=steps,
                     step_limit_hit=step_limit_hit, duration_s=duration, decision=decision,
                     decision_comment=comment, instructions_version=instructions_version, state=state)


def new_session_id(customer_id: str) -> str:
    return f"{customer_id}-chat-{uuid.uuid4().hex[:8]}"

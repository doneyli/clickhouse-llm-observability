"""Claude calls with structured output, each recorded as a Langfuse generation."""
from __future__ import annotations

import json
from typing import Any, Optional

import anthropic

from .config import MODEL_PRICES

_LEVER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "ch_type", "values"],
    "properties": {
        "name": {"type": "string"},
        "ch_type": {"type": "string", "enum": ["String", "UInt8", "UInt16", "UInt32", "Int32",
                                               "Int64", "Float64", "Date", "Array(String)"]},
        "values": {"type": "array", "items": {"type": "string"}},
    },
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reasoning", "lookups", "sql_template", "levers", "metric_name", "objective"],
    "properties": {
        "reasoning": {"type": "string"},
        "lookups": {"type": "array", "items": {"type": "string"}},
        "sql_template": {"type": "string"},
        "levers": {"type": "array", "items": _LEVER},
        "metric_name": {"type": "string"},
        "objective": {"type": "string", "enum": ["maximize", "minimize"]},
    },
}

_PAIR = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "value"],
    "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
}

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reasoning", "sql_template", "levers", "metric_name", "objective",
                 "summary", "best_scenario", "metric_value", "answer_values"],
    "properties": {
        "decision": {"type": "string", "enum": ["answer", "refine"]},
        "reasoning": {"type": "string"},
        "sql_template": {"type": "string"},
        "levers": {"type": "array", "items": _LEVER},
        "metric_name": {"type": "string"},
        "objective": {"type": "string", "enum": ["maximize", "minimize"]},
        "summary": {"type": "string"},
        "best_scenario": {"type": "array", "items": _PAIR},
        "metric_value": {"type": "number"},
        "answer_values": {"type": "array", "items": _PAIR},
    },
}


class LLMError(RuntimeError):
    pass


def cost_usd(model: str, usage: Any) -> dict[str, float]:
    p_in, p_out, p_cache = MODEL_PRICES.get(model, (0.0, 0.0, 0.0))
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cread = getattr(usage, "cache_read_input_tokens", 0) or 0
    cwrite = getattr(usage, "cache_creation_input_tokens", 0) or 0
    c_in = (inp * p_in + cwrite * p_in * 1.25 + cread * p_cache) / 1e6
    c_out = out * p_out / 1e6
    return {"input": round(c_in, 6), "output": round(c_out, 6), "total": round(c_in + c_out, 6)}


class LLM:
    """Counts calls and spend for one agent run (budget enforcement + scores)."""

    def __init__(self, model: str, effort: str, client: Optional[anthropic.AsyncAnthropic] = None):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.AsyncAnthropic()
        self.calls = 0
        self.cost_usd = 0.0
        self.llm_ms = 0.0

    async def structured(self, lf: Any, *, name: str, system: str, user: str,
                         schema: dict[str, Any], prompt: Any = None) -> dict[str, Any]:
        import time

        messages = [{"role": "user", "content": user}]
        with lf.start_as_current_observation(
            as_type="generation", name=name, model=self.model,
            input=[{"role": "system", "content": system}, *messages],
            model_parameters={"effort": self.effort},
            prompt=prompt,
        ) as gen:
            usage = {"input": 0, "output": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
            cost = {"input": 0.0, "output": 0.0, "total": 0.0}
            refusals: list[str] = []
            # A refusal on this workload is a classifier false positive (flight-delay
            # tables), and server-side fallbacks are already on: retry once, record it.
            for attempt in range(2):
                t0 = time.perf_counter()
                try:
                    resp = await self.client.beta.messages.create(
                        model=self.model,
                        max_tokens=16000,
                        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                        messages=messages,
                        output_config={"effort": self.effort,
                                       "format": {"type": "json_schema", "schema": schema}},
                        betas=["server-side-fallback-2026-07-01"],
                        fallbacks="default",
                    )
                except anthropic.APIError as e:
                    gen.update(level="ERROR", status_message=f"{type(e).__name__}: {e}"[:500])
                    raise
                finally:
                    self.llm_ms += (time.perf_counter() - t0) * 1000.0
                self.calls += 1
                c = cost_usd(resp.model or self.model, resp.usage)
                self.cost_usd += c["total"]
                cost = {k: round(cost[k] + c[k], 6) for k in cost}
                usage["input"] += resp.usage.input_tokens or 0
                usage["output"] += resp.usage.output_tokens or 0
                usage["cache_read_input_tokens"] += getattr(resp.usage, "cache_read_input_tokens", 0) or 0
                usage["cache_creation_input_tokens"] += getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
                if resp.stop_reason != "refusal":
                    break
                details = getattr(resp, "stop_details", None)
                refusals.append(str(getattr(details, "category", None) or "unknown"))
            if resp.stop_reason == "refusal":
                gen.update(level="ERROR", status_message=f"refusal ({', '.join(refusals)})",
                           usage_details=usage, cost_details=cost, metadata={"refusals": refusals})
                raise LLMError(f"{name}: model refused twice ({', '.join(refusals)})")
            if resp.stop_reason == "max_tokens":
                gen.update(level="ERROR", status_message="max_tokens", usage_details=usage, cost_details=cost)
                raise LLMError(f"{name}: hit max_tokens")
            text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                gen.update(level="ERROR", status_message=f"bad JSON: {e}", output=text[:2000],
                           usage_details=usage, cost_details=cost)
                raise LLMError(f"{name}: response was not valid JSON") from e
            gen.update(output=data, usage_details=usage, cost_details=cost,
                       level="WARNING" if refusals else "DEFAULT",
                       status_message=f"retried after refusal ({refusals[0]})" if refusals else None,
                       metadata={"stop_reason": resp.stop_reason, "served_model": resp.model,
                                 "refusals": refusals})
            return data

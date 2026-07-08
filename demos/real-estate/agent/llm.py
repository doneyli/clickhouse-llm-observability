"""
Provider-agnostic LLM layer so the SAME agent can run on Anthropic (Claude) or
OpenAI (GPT). This is what powers the "compare Claude vs GPT" experiment: only
the model id changes; the tools, prompts and evaluators stay identical.

`call_llm(model, ...)` returns a normalized result regardless of provider:
    {
      "text": str,                       # assistant text (may be "")
      "tool_calls": [{"id","name","input"}],
      "usage": {"input_tokens","output_tokens"},
      "stop_reason": "tool_use" | "end_turn",
      "assistant_msg": <provider-native assistant message to append back>,
    }
The append_* helpers keep the message history in each provider's native format.
"""

import json
from typing import Any, Dict, List, Optional

from .config import get_anthropic, get_openai
from .tools import ANTHROPIC_TOOLS


def provider_of(model: str) -> str:
    m = (model or "").lower()
    if m.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    return "anthropic"


# Published USD-per-token prices for models this Langfuse instance may not
# auto-price (Claude is in the built-in price list; gpt-4o often isn't). Used to
# emit cost_details so the € cost shows on GPT traces too — important for the
# Claude-vs-GPT cost comparison. (input_rate, output_rate) per token.
_PRICES = {
    "gpt-4o": (2.5e-6, 10.0e-6),
    "gpt-4o-mini": (0.15e-6, 0.6e-6),
}


def _cost_details(model, usage):
    p = _PRICES.get(model)
    if not p:
        return None  # let Langfuse infer from its price list (e.g. Claude)
    return {"input": usage["input_tokens"] * p[0], "output": usage["output_tokens"] * p[1]}


def _openai_tools() -> List[Dict[str, Any]]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in ANTHROPIC_TOOLS]


def tools_for(model: str) -> List[Dict[str, Any]]:
    return _openai_tools() if provider_of(model) == "openai" else ANTHROPIC_TOOLS


def call_llm(model: str, system: str, messages: List[Dict[str, Any]],
             tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 1500) -> Dict[str, Any]:
    if provider_of(model) == "anthropic":
        client = get_anthropic()
        kwargs: Dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        tool_calls = [{"id": b.id, "name": b.name, "input": b.input}
                      for b in resp.content if getattr(b, "type", None) == "tool_use"]
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return {
            "text": text, "tool_calls": tool_calls, "usage": usage,
            "cost_details": _cost_details(model, usage),
            "stop_reason": resp.stop_reason,
            "assistant_msg": {"role": "assistant", "content": resp.content},
        }

    # ---- OpenAI ----
    client = get_openai()
    oai_messages = [{"role": "system", "content": system}] + messages
    # o-series reasoning models reject `max_tokens` and require `max_completion_tokens`.
    token_param = "max_completion_tokens" if model.lower().startswith(("o1", "o3", "o4")) else "max_tokens"
    kwargs = {"model": model, token_param: max_tokens, "messages": oai_messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    text = (msg.content or "").strip()
    tool_calls = []
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        assistant_msg["tool_calls"] = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "input": args})
            assistant_msg["tool_calls"].append({
                "id": tc.id, "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            })
    usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
    return {
        "text": text, "tool_calls": tool_calls, "usage": usage,
        "cost_details": _cost_details(model, usage),
        "stop_reason": "tool_use" if tool_calls else "end_turn",
        "assistant_msg": assistant_msg,
    }


def append_assistant(messages: List[Dict[str, Any]], result: Dict[str, Any]) -> None:
    messages.append(result["assistant_msg"])


def append_tool_results(model: str, messages: List[Dict[str, Any]],
                        tool_outputs: List[Dict[str, str]]) -> None:
    """tool_outputs: list of {"id": tool_call_id, "content": json_string}."""
    if provider_of(model) == "anthropic":
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": o["id"], "content": o["content"]}
            for o in tool_outputs]})
    else:
        for o in tool_outputs:
            messages.append({"role": "tool", "tool_call_id": o["id"], "content": o["content"]})

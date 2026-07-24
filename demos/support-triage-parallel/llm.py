"""
Thin Anthropic call layer for the Support Triage Parallel demo.

Uses the **raw Anthropic SDK** (not LangChain) because this demo fans out many
concurrent LLM calls with ``asyncio.gather`` — the async client is the natural
fit, and each call updates the Langfuse *generation* observation it runs inside
(model + token usage + output) so per-branch cost lands in the trace. This is
what makes the "N× cost fan-out" Monitor story real.

``anthropic`` is imported **lazily** inside the call functions so the pure-logic
modules (and their unit tests) import without the package installed.
"""

import os
from typing import Any, Dict, Optional

_async_client = None
_sync_client = None


def _get_async_client():
    global _async_client
    if _async_client is None:
        from anthropic import AsyncAnthropic
        _async_client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from env
    return _async_client


def _get_sync_client():
    global _sync_client
    if _sync_client is None:
        from anthropic import Anthropic
        _sync_client = Anthropic()
    return _sync_client


def _extract(resp) -> Dict[str, Any]:
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    usage = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    return {"text": text, "usage": usage}


def _apply_usage(obs, model: str, out: Dict[str, Any]):
    """Record model + token usage + output on the generation observation so
    Langfuse can price it. No-op-safe against ``_NullObs`` handles."""
    if obs is None:
        return
    try:
        obs.update(model=model, usage_details=out["usage"], output=out["text"])
    except Exception:  # pragma: no cover - defensive (never let tracing break a call)
        pass


async def anthropic_call(
    *,
    model: str,
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1024,
    obs=None,
) -> str:
    """Async single-turn completion. Updates ``obs`` (a Langfuse generation) with
    model/usage/output when provided. Returns the assistant text."""
    client = _get_async_client()
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "You are a precise assistant. Follow the instructions exactly.",
        messages=[{"role": "user", "content": prompt}],
    )
    out = _extract(resp)
    _apply_usage(obs, model, out)
    return out["text"]


def anthropic_call_sync(
    *,
    model: str,
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    obs=None,
) -> str:
    """Synchronous completion — used by the tie-break judge, which runs after the
    fan-out has already been awaited (no need to stay async)."""
    client = _get_sync_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "You are a precise assistant. Follow the instructions exactly.",
        messages=[{"role": "user", "content": prompt}],
    )
    out = _extract(resp)
    _apply_usage(obs, model, out)
    return out["text"]


def strip_sql(text: str) -> str:
    """Strip markdown fences and prose so we keep only the SQL statement."""
    s = text.strip()
    if "```" in s:
        # take the content of the first fenced block if present
        parts = s.split("```")
        if len(parts) >= 2:
            block = parts[1]
            block = block[3:] if block.lower().startswith("sql") else block
            s = block.strip()
    return s.strip().rstrip(";").strip()

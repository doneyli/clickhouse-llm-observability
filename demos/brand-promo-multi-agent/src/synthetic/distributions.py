"""Latency, token, and cost distributions for synthetic trace generation."""

from __future__ import annotations

import math
import random

# Lognormal params (mean_ms, sigma) per span type
LATENCY_PARAMS: dict[str, tuple[float, float]] = {
    "tool_call": (250.0, 0.5),
    "sonnet_generation": (1200.0, 0.4),
    "opus_generation": (3500.0, 0.4),
    "haiku_generation": (500.0, 0.4),
    "classify_intent": (400.0, 0.3),
    "compose_brief": (2000.0, 0.4),
    "research_crew": (5000.0, 0.3),
    "strategy_crew": (8000.0, 0.3),
    "compliance_agent": (1200.0, 0.3),
    "timeout_tool": (5000.0, 0.1),
}

# Token cost per million tokens (input, output) — Anthropic list prices.
MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

# Typical token counts per generation type (input_tokens, output_tokens)
TOKEN_COUNTS: dict[str, tuple[int, int]] = {
    "classify_intent": (120, 50),
    "research_summarize": (1200, 400),
    "market_summarize": (600, 250),
    "history_summarize": (400, 200),
    "generate_options": (2000, 1200),
    "estimate_lift": (800, 400),
    "compliance_check": (400, 200),
    "compose_brief": (1800, 800),
}


def sample_latency(span_type: str, rng: random.Random) -> int:
    """Sample a latency value in ms using lognormal distribution."""
    mean_ms, sigma = LATENCY_PARAMS.get(span_type, (500.0, 0.4))
    mu = math.log(mean_ms) - (sigma**2) / 2
    sample = rng.lognormvariate(mu, sigma)
    return max(10, int(sample))


def compute_cost_details(model: str, input_tokens: int, output_tokens: int) -> dict[str, float]:
    """Per-usage-type cost in USD, for Langfuse's generation ``cost_details``.

    Sending explicit cost makes Langfuse store it directly — no model-price
    lookup — which is what these synthetic, backdated traces need (a price
    definition's effective date would otherwise skip backfilled timestamps).
    """
    input_cost_pm, output_cost_pm = MODEL_COSTS.get(model, (3.0, 15.0))
    return {
        "input": input_tokens * input_cost_pm / 1_000_000,
        "output": output_tokens * output_cost_pm / 1_000_000,
    }


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute total cost in USD for a generation."""
    return sum(compute_cost_details(model, input_tokens, output_tokens).values())


def sample_tokens(token_type: str, rng: random.Random) -> tuple[int, int]:
    """Sample token counts with light jitter."""
    base_in, base_out = TOKEN_COUNTS.get(token_type, (500, 200))
    def jitter(x):
        return max(10, int(x * rng.uniform(0.75, 1.30)))
    return jitter(base_in), jitter(base_out)

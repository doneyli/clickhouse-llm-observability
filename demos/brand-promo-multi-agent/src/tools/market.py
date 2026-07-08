"""Market trends tool - uses Tavily if configured, else returns canned response."""

from __future__ import annotations

import random
from typing import Any

from langchain_core.tools import tool

from src.config import load_config, load_env
from src.tools.error_injection import InjectedFault, maybe_inject

_CANNED_TRENDS = [
    "Consumers increasingly favor value packs in current macroeconomic environment.",
    "Category growth slowing in mid-tier; premium and value tiers outperforming.",
    "Digital coupon redemption up 18% YoY; retailer app integration accelerating.",
    "On-the-go format sales up 12% in convenience and club channels.",
    "Sustainability claims driving +8% price premium in specialty retail.",
    "Back-to-school season showing early strong signals in Southeast and Midwest.",
    "Club channel share up 3pp; warehouse format gaining in household penetration.",
    "Flavor innovation driving trial in younger demographics (18-34).",
    "In-store promotional compliance down; digital shelf edge improving conversion.",
    "Regional taste preferences diverging; Southwest indexing higher on bold flavors.",
]


@tool
def get_market_trends(brand: str | None = None, region: str | None = None) -> dict[str, Any]:
    """Retrieve market trend data for a brand and/or region.
    Uses Tavily if TAVILY_API_KEY is set, otherwise returns curated canned insights.
    """
    fault = maybe_inject("get_market_trends")
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Market trends API unavailable"}

    env = load_env()
    load_config()

    if env.tavily_api_key:
        try:
            from tavily import TavilyClient  # type: ignore[import-not-found]

            client = TavilyClient(api_key=env.tavily_api_key)
            query_parts = ["consumer packaged goods market trends"]
            if brand:
                query_parts.append(brand)
            if region:
                query_parts.append(region)
            query = " ".join(query_parts)
            result = client.search(query, max_results=3)
            snippets = [r.get("content", "")[:200] for r in result.get("results", [])]
            return {
                "status": "ok",
                "tool.outcome": "ok",
                "source": "tavily",
                "brand": brand,
                "region": region,
                "trends": snippets,
            }
        except Exception:
            # Fall through to canned on any Tavily failure
            pass

    rng = random.Random(hash((brand or "") + (region or "")))
    selected = rng.sample(_CANNED_TRENDS, min(4, len(_CANNED_TRENDS)))

    return {
        "status": "ok",
        "tool.outcome": "ok",
        "source": "canned",
        "brand": brand,
        "region": region,
        "trends": selected,
    }

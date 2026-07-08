"""Mock sales and inventory tools backed by static JSON data."""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from src.tools.error_injection import InjectedFault, maybe_inject


@lru_cache(maxsize=1)
def _load_sales() -> list[dict[str, Any]]:
    path = Path(__file__).parent.parent / "data" / "mock_sales.json"
    return json.loads(path.read_text())["rows"]


@lru_cache(maxsize=1)
def _load_inventory() -> list[dict[str, Any]]:
    path = Path(__file__).parent.parent / "data" / "mock_inventory.json"
    return json.loads(path.read_text())["rows"]


@tool
def query_sales(
    brand: str | None = None,
    sku: str | None = None,
    region: str | None = None,
    retail_partner: str | None = None,
    quarter_start: str | None = None,
    quarter_end: str | None = None,
) -> dict[str, Any]:
    """Query mock sales data. Filters by brand, SKU, region, retail partner, and quarter range."""
    fault = maybe_inject("query_sales")
    if fault == InjectedFault.SALES_API_TIMEOUT:
        time.sleep(5)
        return {"status": "error", "tool.outcome": "timeout", "error": "Sales API timeout"}
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Sales API unavailable"}

    rows = _load_sales()

    def in_range(q: str) -> bool:
        if quarter_start and q < quarter_start:
            return False
        if quarter_end and q > quarter_end:
            return False
        return True

    filtered = [
        r for r in rows
        if (brand is None or r["brand"] == brand)
        and (sku is None or r["sku"] == sku)
        and (region is None or r["region"] == region)
        and (retail_partner is None or r["retail_partner"] == retail_partner)
        and in_range(r["quarter"])
    ]

    total_units = sum(r["units"] for r in filtered)
    total_revenue = round(sum(r["revenue_usd"] for r in filtered), 2)
    promo_rows = [r for r in filtered if r["promo_active"]]
    promo_lift = None
    if promo_rows and len(filtered) > len(promo_rows):
        base_units = sum(r["units"] for r in filtered if not r["promo_active"])
        base_count = len(filtered) - len(promo_rows)
        promo_count = len(promo_rows)
        if base_count > 0 and promo_count > 0:
            avg_base = base_units / base_count
            avg_promo = sum(r["units"] for r in promo_rows) / promo_count
            promo_lift = round((avg_promo - avg_base) / avg_base * 100, 1) if avg_base > 0 else None

    return {
        "status": "ok",
        "tool.outcome": "ok",
        "filters": {
            "brand": brand,
            "sku": sku,
            "region": region,
            "retail_partner": retail_partner,
            "quarter_start": quarter_start,
            "quarter_end": quarter_end,
        },
        "row_count": len(filtered),
        "total_units": total_units,
        "total_revenue_usd": total_revenue,
        "promo_lift_pct": promo_lift,
        "sample_rows": filtered[:5],
    }


@tool
def query_inventory(
    sku: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Query mock inventory data by SKU and/or region. Returns units on hand and days of supply."""
    fault = maybe_inject("query_inventory")
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Inventory API unavailable"}

    rows = _load_inventory()
    filtered = [
        r for r in rows
        if (sku is None or r["sku"] == sku)
        and (region is None or r["region"] == region)
    ]

    if not filtered:
        return {
            "status": "ok",
            "tool.outcome": "ok",
            "filters": {"sku": sku, "region": region},
            "row_count": 0,
            "rows": [],
        }

    avg_days_supply = round(sum(r["days_of_supply"] for r in filtered) / len(filtered), 1)
    total_units = sum(r["units_on_hand"] for r in filtered)

    return {
        "status": "ok",
        "tool.outcome": "ok",
        "filters": {"sku": sku, "region": region},
        "row_count": len(filtered),
        "total_units_on_hand": total_units,
        "avg_days_of_supply": avg_days_supply,
        "rows": filtered[:10],
    }

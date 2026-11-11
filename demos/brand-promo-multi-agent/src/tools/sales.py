"""Mock sales and inventory tools backed by static JSON data."""

from __future__ import annotations

import json
import re
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


def sales_catalog() -> dict[str, Any]:
    """Describe the actual fixture values so agents do not guess tool filters."""
    rows = _load_sales()
    return {
        "brand_skus": {
            brand: sorted({r["sku"] for r in rows if r["brand"] == brand})
            for brand in sorted({r["brand"] for r in rows})
        },
        "quarters": sorted({r["quarter"] for r in rows}),
    }


def _normalize_quarter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().upper()
    if match := re.fullmatch(r"(\d{4})[- ]?Q([1-4])", value):
        return f"{match[1]}-Q{match[2]}"
    if match := re.fullmatch(r"Q([1-4])[- ]?(\d{4})", value):
        return f"{match[2]}-Q{match[1]}"
    raise ValueError(f"Invalid quarter {value!r}. Use YYYY-Qn, for example 2024-Q1.")


@tool
def query_sales(
    brand: str | None = None,
    sku: str | None = None,
    region: str | None = None,
    retail_partner: str | None = None,
    quarter_start: str | None = None,
    quarter_end: str | None = None,
) -> dict[str, Any]:
    """Query sales by brand, SKU code, region, retailer, and inclusive quarter range.

    Use SKU codes such as BRA-CLS-LRG, never product names such as Brand A Classic.
    Omit sku to include all products for a brand. Quarters use YYYY-Qn (2024-Q1);
    Qn-YYYY is also accepted and normalized. Omit quarters to use all available history.
    """
    fault = maybe_inject("query_sales")
    if fault == InjectedFault.SALES_API_TIMEOUT:
        time.sleep(5)
        return {"status": "error", "tool.outcome": "timeout", "error": "Sales API timeout"}
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Sales API unavailable"}

    rows = _load_sales()
    try:
        quarter_start = _normalize_quarter(quarter_start)
        quarter_end = _normalize_quarter(quarter_end)
        if quarter_start and quarter_end and quarter_start > quarter_end:
            raise ValueError("quarter_start must be on or before quarter_end.")
    except ValueError as exc:
        return {"status": "error", "tool.outcome": "error", "error": str(exc)}

    valid_skus = sorted({r["sku"] for r in rows if brand is None or r["brand"] == brand})
    if sku is not None and sku not in valid_skus:
        return {
            "status": "error", "tool.outcome": "error",
            "error": f"Unknown SKU {sku!r}. Select a valid SKU code or omit sku for brand totals.",
            "valid_skus": valid_skus,
        }

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
    """Query inventory by SKU code and/or region, returning stock and days of supply.

    Use actual SKU codes such as BRA-CLS-LRG, not product names. Omit sku to discover
    available codes; an unknown SKU returns valid_skus to correct the request.
    """
    fault = maybe_inject("query_inventory")
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Inventory API unavailable"}

    rows = _load_inventory()
    valid_skus = sorted({r["sku"] for r in rows})
    if sku is not None and sku not in valid_skus:
        return {
            "status": "error", "tool.outcome": "error",
            "error": f"Unknown SKU {sku!r}. Select one of valid_skus.",
            "valid_skus": valid_skus,
        }
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

"""
The outage-credit policy as code: the ground truth `credit-amount-correct` checks against.

    credit = whole days of outage (rounded up) × line daily rate (monthly price / days in month)
    only the affected service's line, only for a confirmed outage of at least 4 hours,
    capped at one month's price.
"""

from __future__ import annotations

import calendar
import math
from datetime import datetime, timezone
from typing import Optional

DEMO_NOW = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)  # fixed clock: reproducible credits
MIN_HOURS = 4.0


def _ts(value: Optional[str]) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else DEMO_NOW


def outage_hours(outage: dict) -> float:
    return round((_ts(outage.get("end")) - _ts(outage["start"])).total_seconds() / 3600, 2)


def credit_for(line: dict, outage: Optional[dict]) -> dict:
    """{"eligible", "amount", "days", "hours", "reason"} for one line and one outage (or None)."""
    if outage is None:
        return {"eligible": False, "amount": 0.0, "days": 0, "hours": 0.0, "reason": "no confirmed outage"}
    if outage["service"] != line["kind"] or outage["zip"] != line["zip"]:
        return {"eligible": False, "amount": 0.0, "days": 0, "hours": 0.0,
                "reason": "outage does not affect this line"}
    hours = outage_hours(outage)
    if hours < MIN_HOURS:
        return {"eligible": False, "amount": 0.0, "days": 0, "hours": hours,
                "reason": f"outage under {MIN_HOURS:g} hours"}
    start = _ts(outage["start"])
    days_in_month = calendar.monthrange(start.year, start.month)[1]
    days = math.ceil(hours / 24)
    amount = min(round(days * line["monthly_price"] / days_in_month, 2), line["monthly_price"])
    return {"eligible": True, "amount": amount, "days": days, "hours": hours, "reason": "confirmed outage"}

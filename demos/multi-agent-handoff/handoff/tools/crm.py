"""
The fake Northwind Mobile back office: customers, lines, invoices, the outage feed, line
diagnostics and the KB. Reads are from data/*.json; writes (credits, technician bookings)
go to a per-run `RunState`, so evaluators can check what actually happened, not just what
the reply says.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from handoff import config
from handoff.tools import policy


@lru_cache(maxsize=1)
def _customers() -> dict:
    return {c["customer_id"]: c for c in json.loads((config.DATA_DIR / "customers.json").read_text())}


@lru_cache(maxsize=1)
def _outages() -> list:
    return json.loads((config.DATA_DIR / "outages.json").read_text())


@lru_cache(maxsize=1)
def _kb() -> list:
    return json.loads((config.DATA_DIR / "kb.json").read_text())


@dataclass
class RunState:
    """Side effects of one conversation, returned in the run record."""

    credits: list = field(default_factory=list)
    technicians: list = field(default_factory=list)


def get_customer(customer_id: str) -> dict:
    c = _customers().get(customer_id)
    if not c:
        return {"error": f"unknown customer {customer_id}"}
    return {"customer_id": c["customer_id"], "name": c["name"], "zip": c["zip"],
            "lines": [{k: ln[k] for k in ("line_id", "kind", "plan", "monthly_price")} for ln in c["lines"]]}


def _line(line_id: str) -> Optional[dict]:
    for c in _customers().values():
        for ln in c["lines"]:
            if ln["line_id"] == line_id:
                return ln
    return None


def check_outage(zip_code: str, service: Optional[str] = None) -> dict:
    hits = [o for o in _outages() if o["zip"] == str(zip_code) and (service is None or o["service"] == service)]
    return {"zip": zip_code, "outages": [{**o, "hours": policy.outage_hours(o)} for o in hits]}


def run_line_diagnostics(line_id: str) -> dict:
    ln = _line(line_id)
    if not ln:
        return {"error": f"unknown line {line_id}"}
    area = check_outage(ln["zip"], ln["kind"])["outages"]
    return {"line_id": line_id, "kind": ln["kind"], "line_status": ln["status"],
            "area_outage": area[0]["outage_id"] if area else None}


def kb_search(query: str, category: Optional[str] = None, k: int = 3) -> dict:
    words = set(re.findall(r"[a-z]+", query.lower())) - {"the", "a", "my", "is", "to", "and", "of", "i"}
    scored = []
    for a in _kb():
        if category and a["category"] != category:
            continue
        text = f"{a['title']} {a['body']}".lower()
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((score, a))
    scored.sort(key=lambda x: -x[0])
    return {"articles": [a for _, a in scored[:k]]}


def get_invoice(customer_id: str, month: Optional[str] = None) -> dict:
    c = _customers().get(customer_id)
    if not c:
        return {"error": f"unknown customer {customer_id}"}
    invs = c["invoices"] if not month else [i for i in c["invoices"] if i["month"] == month]
    return {"customer_id": customer_id, "invoices": invs}


def get_plan(line_id: str) -> dict:
    ln = _line(line_id)
    return {"error": f"unknown line {line_id}"} if not ln else {k: ln[k] for k in ("line_id", "kind", "plan", "monthly_price")}


def compute_credit(line_id: str, outage_id: Optional[str] = None) -> dict:
    ln = _line(line_id)
    if not ln:
        return {"error": f"unknown line {line_id}"}
    outage = next((o for o in _outages() if o["outage_id"] == outage_id), None) if outage_id else None
    return {"line_id": line_id, "outage_id": outage_id, **policy.credit_for(ln, outage)}


def issue_credit(state: RunState, line_id: str, amount: float, reason: str, outage_id: Optional[str] = None) -> dict:
    """Write path. Refuses an amount the policy would not give (the oracle is the guard)."""
    expected = compute_credit(line_id, outage_id)
    if "error" in expected:
        return expected
    if not expected["eligible"]:
        return {"issued": False, "reason": expected["reason"]}
    if abs(float(amount) - expected["amount"]) > 0.005:
        return {"issued": False, "reason": f"amount {amount} does not match policy {expected['amount']}"}
    record = {"line_id": line_id, "amount": expected["amount"], "reason": reason, "outage_id": outage_id}
    state.credits.append(record)
    return {"issued": True, **record}


def book_technician(state: RunState, line_id: str, window: str = "next available") -> dict:
    diag = run_line_diagnostics(line_id)
    if diag.get("area_outage"):
        return {"booked": False, "reason": f"area outage {diag['area_outage']} — no visit during an outage"}
    record = {"line_id": line_id, "window": window, "booking_id": f"TECH-{line_id}"}
    state.technicians.append(record)
    return {"booked": True, **record}

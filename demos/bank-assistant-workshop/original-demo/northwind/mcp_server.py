"""
Core-banking MCP server (OBS-04) — run: `python -m northwind.mcp_server`

A separate process exposing banking operations as MCP tools over streamable
HTTP, the way a bank's integration team would front its core systems. Synthetic,
in-memory data.

Cross-process tracing: the agent puts W3C trace context (`traceparent`) and W3C
`baggage` in each request's MCP `_meta`. This server extracts both and opens its
spans as children, so ONE trace in Langfuse (and in the APM) shows agent → MCP
client → MCP server → core-banking call, across two services — and, because the
baggage carries the turn's session/user/trace name/version/environment, the
server's observations filter and aggregate with the rest of the trace (Langfuse v4).

Authorization lives here, not in the model: every tool takes the customer id the
*session* authenticated, and the server refuses anything else.
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta

from opentelemetry import context as otel_context

from northwind import config

from mcp.server.fastmcp import Context, FastMCP

langfuse = config.get_langfuse(service_name="core-banking-mcp")
import os  # noqa: E402

# One MCP server per Langfuse target: its spans export with the same profile's keys,
# so the cross-process trace lands complete in that Langfuse (8765 cloud, 8766 self-hosted).
PORT = int(os.environ.get("NORTHWIND_MCP_PORT", "8765"))
mcp = FastMCP("northwind-core-banking", host="127.0.0.1", port=PORT)

_today = date.today()


def _d(days_ago: int) -> str:
    return (_today - timedelta(days=days_ago)).isoformat()


CUSTOMERS = {
    "C-1001": {"name": "Ana Torres", "segment": "Everyday", "since": "2017-05-02",
               "accounts": [{"id": "ACC-1001-01", "type": "Everyday checking", "currency": "USD", "balance": 2840.55},
                            {"id": "ACC-1001-02", "type": "High-Yield Savings", "currency": "USD", "balance": 12500.00}],
               "cards": [{"last4": "4417", "product": "Northwind Classic credit", "status": "active", "limit": 6000}]},
    "C-1002": {"name": "Ben Okafor", "segment": "Premier", "since": "2012-11-19",
               "accounts": [{"id": "ACC-1002-01", "type": "Premier checking", "currency": "USD", "balance": 48210.10},
                            {"id": "ACC-1002-02", "type": "Term deposit 12m", "currency": "USD", "balance": 60000.00}],
               "cards": [{"last4": "9921", "product": "Northwind Platinum credit", "status": "active", "limit": 25000}]},
    "C-1003": {"name": "Carla Mendes", "segment": "Everyday", "since": "2022-02-08",
               "accounts": [{"id": "ACC-1003-01", "type": "Everyday checking", "currency": "USD", "balance": -120.40,
                             "overdraft_protection": True}],
               "cards": [{"last4": "3088", "product": "Northwind debit", "status": "active", "limit": None}]},
    "C-1004": {"name": "David Kim", "segment": "Premier", "since": "2015-07-30",
               "accounts": [{"id": "ACC-1004-01", "type": "Premier checking", "currency": "USD", "balance": 15890.00}],
               "cards": [{"last4": "5530", "product": "Northwind Platinum credit", "status": "active", "limit": 30000}]},
}

TRANSACTIONS = {
    "ACC-1001-01": [
        {"id": "TX-88120", "date": _d(1), "merchant": "Fresh Market", "amount": -64.20},
        {"id": "TX-88117", "date": _d(2), "merchant": "STREAMFLIX SUBSCRIPTION", "amount": -15.99},
        {"id": "TX-88116", "date": _d(2), "merchant": "STREAMFLIX SUBSCRIPTION", "amount": -15.99},
        {"id": "TX-88101", "date": _d(4), "merchant": "UNKNOWN MERCHANT 0042 LAGOS", "amount": -412.00},
        {"id": "TX-88090", "date": _d(6), "merchant": "Payroll ACME Corp", "amount": 3150.00},
        {"id": "TX-88071", "date": _d(9), "merchant": "City Electric", "amount": -88.10},
        {"id": "TX-87950", "date": _d(41), "merchant": "GADGETSTORE ONLINE", "amount": -389.99},
    ],
    "ACC-1002-01": [
        {"id": "TX-77310", "date": _d(1), "merchant": "Airline FlyNorth", "amount": -1240.00},
        {"id": "TX-77302", "date": _d(3), "merchant": "Hotel Lisboa Centro", "amount": -860.35},
        {"id": "TX-77288", "date": _d(8), "merchant": "Wire to J. Okafor (GB)", "amount": -5000.00},
        {"id": "TX-77150", "date": _d(47), "merchant": "TRAVELHUB BOOKING", "amount": -1120.00},
    ],
    "ACC-1003-01": [
        {"id": "TX-66012", "date": _d(1), "merchant": "Rent - Parkview Apts", "amount": -1100.00},
        {"id": "TX-66009", "date": _d(1), "merchant": "Overdraft fee", "amount": -15.00},
        {"id": "TX-66001", "date": _d(5), "merchant": "Coffee Corner", "amount": -4.50},
        {"id": "TX-65890", "date": _d(38), "merchant": "FITCLUB MEMBERSHIP", "amount": -59.90},
    ],
    "ACC-1004-01": [
        {"id": "TX-55440", "date": _d(2), "merchant": "Brokerage transfer", "amount": -5000.00},
        {"id": "TX-55431", "date": _d(7), "merchant": "Salary KimTech", "amount": 9800.00},
        {"id": "TX-55300", "date": _d(52), "merchant": "ELECTROMART", "amount": -749.00},
    ],
}


def _traced(ctx: Context | None, name: str, payload: dict):
    """Attach the caller's trace context from MCP `_meta`, open a server span.

    The baggage half of the carrier gives these spans the caller's session, user,
    trace name, version and environment, and tells the SDK the trace already has a
    root — so this span stays a child instead of becoming a second root.
    """
    carrier = {}
    try:
        meta = ctx.request_context.meta if ctx else None
        if meta is not None:
            extra = getattr(meta, "model_extra", None) or {}
            carrier = {k: v for k, v in {**extra, **(meta if isinstance(meta, dict) else {})}.items()
                       if k in ("traceparent", "tracestate", "baggage")}
    except Exception:  # noqa: BLE001
        pass
    token = otel_context.attach(config.extract_trace_context(carrier)) if carrier else None
    span = langfuse.start_as_current_observation(
        as_type="span", name=f"mcp-server: {name}", input=payload,
        metadata={"mcp.server": "northwind-core-banking", "mcp.tool": name,
                  "linked_by": ("traceparent + baggage in MCP _meta" if "baggage" in carrier else
                                "traceparent in MCP _meta" if carrier else "none")})
    return token, span


def _run(ctx, name, payload, fn):
    token, cm = _traced(ctx, name, payload)
    try:
        with cm as span:
            with langfuse.start_as_current_observation(
                    as_type="span", name=f"core-banking.{name}",
                    metadata={"system": "core-banking", "latency_profile": "mainframe"}):
                result = fn()
            span.update(output=result)
            return result
    finally:
        if token is not None:
            otel_context.detach(token)


def _customer(customer_id: str) -> dict:
    if customer_id not in CUSTOMERS:
        raise ValueError(f"customer {customer_id} not found")
    return CUSTOMERS[customer_id]


@mcp.tool()
def get_customer_profile(customer_id: str, ctx: Context = None) -> dict:
    """Profile of the authenticated customer: name, segment, customer since."""
    def fn():
        c = _customer(customer_id)
        return {"customer_id": customer_id, "name": c["name"], "segment": c["segment"], "since": c["since"]}
    return _run(ctx, "get_customer_profile", {"customer_id": customer_id}, fn)


@mcp.tool()
def list_accounts(customer_id: str, ctx: Context = None) -> dict:
    """Accounts and cards of the authenticated customer with current balances."""
    def fn():
        c = _customer(customer_id)
        return {"accounts": c["accounts"], "cards": c["cards"]}
    return _run(ctx, "list_accounts", {"customer_id": customer_id}, fn)


@mcp.tool()
def get_recent_transactions(customer_id: str, account_id: str, days: int = 30, ctx: Context = None) -> dict:
    """Recent transactions for one of the customer's accounts."""
    def fn():
        owned = {a["id"] for a in _customer(customer_id)["accounts"]}
        if account_id not in owned:
            return {"error": "ACCESS_DENIED", "detail": f"{account_id} does not belong to the signed-in customer"}
        cutoff = (_today - timedelta(days=days)).isoformat()
        return {"account_id": account_id, "window_days": days, "from_date": cutoff,
                "transactions": [t for t in TRANSACTIONS.get(account_id, []) if t["date"] >= cutoff]}
    return _run(ctx, "get_recent_transactions",
                {"customer_id": customer_id, "account_id": account_id, "days": days}, fn)


@mcp.tool()
def block_card(customer_id: str, card_last4: str, reason: str, ctx: Context = None) -> dict:
    """Block one of the customer's cards immediately and order a replacement."""
    def fn():
        cards = {c["last4"]: c for c in _customer(customer_id)["cards"]}
        if card_last4 not in cards:
            return {"error": "CARD_NOT_FOUND", "detail": f"no card ending {card_last4} for this customer"}
        cards[card_last4]["status"] = "blocked"
        return {"status": "blocked", "card_last4": card_last4, "reason": reason,
                "confirmation": f"BLK-{uuid.uuid4().hex[:8].upper()}",
                "replacement_eta_business_days": "5-7"}
    return _run(ctx, "block_card", {"customer_id": customer_id, "card_last4": card_last4, "reason": reason}, fn)


@mcp.tool()
def open_dispute(customer_id: str, transaction_id: str, reason: str, ctx: Context = None) -> dict:
    """Open a dispute on one of the customer's card or account transactions."""
    def fn():
        owned = {t["id"]: t for a in _customer(customer_id)["accounts"] for t in TRANSACTIONS.get(a["id"], [])}
        if transaction_id not in owned:
            return {"error": "TRANSACTION_NOT_FOUND", "detail": f"{transaction_id} not found for this customer"}
        return {"case_id": f"DSP-{random.randint(100000, 999999)}", "transaction": owned[transaction_id],
                "status": "opened", "provisional_credit_within_business_days": 10,
                "investigation_max_days": 45}
    return _run(ctx, "open_dispute", {"customer_id": customer_id, "transaction_id": transaction_id,
                                      "reason": reason}, fn)


@mcp.tool()
def schedule_callback(customer_id: str, topic: str, preferred_time: str = "next available", ctx: Context = None) -> dict:
    """Schedule a call-back from a human agent."""
    def fn():
        _customer(customer_id)
        return {"callback_id": f"CB-{random.randint(10000, 99999)}", "topic": topic,
                "slot": preferred_time, "status": "scheduled"}
    return _run(ctx, "schedule_callback", {"customer_id": customer_id, "topic": topic,
                                           "preferred_time": preferred_time}, fn)


if __name__ == "__main__":
    print(f"core-banking MCP on http://127.0.0.1:{PORT}/mcp  (Langfuse: {config.LANGFUSE_BASE_URL})")
    mcp.run(transport="streamable-http")

"""Domain tools exposed to the agents: Anthropic tool schemas + the Python behind them."""

from __future__ import annotations

from typing import Any, Callable

from handoff.tools import crm


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


S = {"type": "string"}
N = {"type": "number"}

# name → (description, input_schema, fn(state, **args))
TOOLS: dict[str, tuple[str, dict, Callable[..., Any]]] = {
    "get_customer": ("Look up the customer's profile and lines (line ids, kinds, plans).",
                     _obj({"customer_id": S}, ["customer_id"]),
                     lambda st, **a: crm.get_customer(**a)),
    "faq_lookup": ("Search general FAQs (hours, payments, roaming, returns, how credits work).",
                   _obj({"query": S}, ["query"]),
                   lambda st, **a: crm.kb_search(a["query"], category="faq")),
    "kb_search": ("Search troubleshooting articles.",
                  _obj({"query": S}, ["query"]),
                  lambda st, **a: crm.kb_search(a["query"])),
    "check_outage": ("Check the outage feed for a zip code (optionally one service: mobile | home-internet).",
                     _obj({"zip_code": S, "service": S}, ["zip_code"]),
                     lambda st, **a: crm.check_outage(**a)),
    "run_line_diagnostics": ("Run diagnostics on one line; returns line status and any area outage.",
                             _obj({"line_id": S}, ["line_id"]),
                             lambda st, **a: crm.run_line_diagnostics(**a)),
    "book_technician": ("Book a technician visit for a line (refused during an area outage).",
                        _obj({"line_id": S, "window": S}, ["line_id"]),
                        lambda st, **a: crm.book_technician(st, **a)),
    "get_invoice": ("Get the customer's invoices (optionally one month, YYYY-MM) and any credits already "
                    "applied to the account that will show on the next invoice.",
                    _obj({"customer_id": S, "month": S}, ["customer_id"]),
                    lambda st, **a: crm.get_invoice(**a, state=st)),
    "get_plan": ("Get the plan and monthly price of a line.",
                 _obj({"line_id": S}, ["line_id"]),
                 lambda st, **a: crm.get_plan(**a)),
    "compute_credit": ("Compute the policy credit for a line and a confirmed outage id. Always use this; never "
                       "calculate credits yourself.",
                       _obj({"line_id": S, "outage_id": S}, ["line_id"]),
                       lambda st, **a: crm.compute_credit(**a)),
    "issue_credit": ("Apply a credit to the customer's next bill. The amount must be the compute_credit amount.",
                     _obj({"line_id": S, "amount": N, "reason": S, "outage_id": S}, ["line_id", "amount", "reason"]),
                     lambda st, **a: crm.issue_credit(st, **a)),
}


def schema(name: str) -> dict:
    desc, params, _ = TOOLS[name]
    return {"name": name, "description": desc, "input_schema": params}


def call(name: str, state: crm.RunState, args: dict) -> Any:
    try:
        return TOOLS[name][2](state, **args)
    except TypeError as exc:  # bad arguments from the model: tell it, don't crash
        return {"error": f"bad arguments for {name}: {exc}"}

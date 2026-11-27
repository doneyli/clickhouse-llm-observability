"""
The fake Northwind Mobile back office: customers, lines, invoices, the outage feed, line
diagnostics and the KB. Reference data is read from data/*.json.

Writes (credits, technician bookings) go to a **ledger**: one SQLite file that every agent
in every process reads and writes, namespaced by conversation id so runs never interfere.
It is the system of record a real support desk has: what billing issued on a peer hop
from tech-support is what billing (or anyone) reads back on the next turn, whatever the
trace linkage. Evaluators check the ledger, so they score what actually happened, not
what a reply says happened.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
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


# --- the ledger ------------------------------------------------------------------------------

LEDGER_PATH = Path(config.setting("HANDOFF_LEDGER", str(config.DEMO_DIR / ".run" / "ledger.sqlite")))
KINDS = ("credits", "technicians")


def _db() -> sqlite3.Connection:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LEDGER_PATH, timeout=10, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS ledger (conversation_id TEXT NOT NULL, kind TEXT NOT NULL, "
                "key TEXT NOT NULL, record TEXT NOT NULL, ts REAL NOT NULL, "
                "PRIMARY KEY (conversation_id, kind, key))")
    return con


def ledger_snapshot(conversation_id: str) -> dict:
    """Everything written in one conversation, by any agent in any process."""
    with _db() as con:
        rows = con.execute("SELECT kind, record FROM ledger WHERE conversation_id = ? ORDER BY ts",
                           (conversation_id,)).fetchall()
    out: dict = {k: [] for k in KINDS}
    for kind, record in rows:
        out[kind].append(json.loads(record))
    return out


@dataclass
class RunState:
    """What one agent call wrote (its effects) and its view of the back office.

    With a `conversation_id`, reads and writes go to the shared ledger. Without one (unit
    tests), they stay in this object.
    """

    conversation_id: Optional[str] = None
    credits: list = field(default_factory=list)
    technicians: list = field(default_factory=list)
    _index: dict = field(default_factory=dict, repr=False, compare=False)  # (kind, key) → record, no-ledger mode

    def existing(self, kind: str, key: str) -> Optional[dict]:
        if self.conversation_id:
            with _db() as con:
                row = con.execute("SELECT record FROM ledger WHERE conversation_id = ? AND kind = ? AND key = ?",
                                  (self.conversation_id, kind, key)).fetchone()
            return json.loads(row[0]) if row else None
        return self._index.get((kind, key))

    def write(self, kind: str, key: str, record: dict) -> bool:
        """Record a write; False when the same write is already on the ledger."""
        if self.conversation_id:
            with _db() as con:
                cur = con.execute("INSERT OR IGNORE INTO ledger VALUES (?, ?, ?, ?, ?)",
                                  (self.conversation_id, kind, key, json.dumps(record), time.time()))
            if cur.rowcount == 0:
                return False
        elif (kind, key) in self._index:
            return False
        else:
            self._index[(kind, key)] = record
        getattr(self, kind).append(record)
        return True

    def ledger(self, kind: str) -> list:
        if self.conversation_id:
            return ledger_snapshot(self.conversation_id)[kind]
        return list(getattr(self, kind))


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


def get_invoice(customer_id: str, month: Optional[str] = None, state: Optional[RunState] = None) -> dict:
    c = _customers().get(customer_id)
    if not c:
        return {"error": f"unknown customer {customer_id}"}
    invs = c["invoices"] if not month else [i for i in c["invoices"] if i["month"] == month]
    lines = {ln["line_id"] for ln in c["lines"]}
    pending = [r for r in (state.ledger("credits") if state else []) if r["line_id"] in lines]
    return {"customer_id": customer_id, "invoices": invs,
            "credits_applied": pending,
            "note": "credits_applied are on the account and will show on the next invoice"}


PRIORITY_DATA_GB = {"mobile": 50, "home-internet": 1200}  # per month; after it, speeds drop to 2 Mbps


def get_plan(line_id: str) -> dict:
    ln = _line(line_id)
    if not ln:
        return {"error": f"unknown line {line_id}"}
    out = {k: ln[k] for k in ("line_id", "kind", "plan", "monthly_price")}
    cap = PRIORITY_DATA_GB[ln["kind"]]
    out["priority_data"] = {"cap_gb": cap, "resets": "2026-12-01", "speed_after_cap": "2 Mbps",
                            "used_gb": round(cap * 1.04, 1) if ln["status"] == "speed-throttled" else round(cap * 0.41, 1)}
    return out


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
    key = f"{line_id}:{outage_id}"
    prior = state.existing("credits", key)
    if prior:
        return {"issued": False, "reason": "already credited for this outage", "existing": prior}
    if abs(float(amount) - expected["amount"]) > 0.005:
        return {"issued": False, "reason": f"amount {amount} does not match policy {expected['amount']}"}
    record = {"line_id": line_id, "amount": expected["amount"], "reason": reason, "outage_id": outage_id}
    if not state.write("credits", key, record):
        return {"issued": False, "reason": "already credited for this outage"}
    return {"issued": True, **record}


def book_technician(state: RunState, line_id: str, window: str = "next available") -> dict:
    diag = run_line_diagnostics(line_id)
    if diag.get("area_outage"):
        return {"booked": False, "reason": f"area outage {diag['area_outage']} — no visit during an outage"}
    prior = state.existing("technicians", line_id)
    if prior:
        return {"booked": False, "reason": "a visit is already booked for this line", "existing": prior}
    record = {"line_id": line_id, "window": window, "booking_id": f"TECH-{line_id}"}
    if not state.write("technicians", line_id, record):
        return {"booked": False, "reason": "a visit is already booked for this line"}
    return {"booked": True, **record}

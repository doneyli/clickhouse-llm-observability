"""
Regenerate the synthetic Northwind Mobile data (deterministic; committed output).

    .venv/bin/python data/generate.py

Writes customers.json and outages.json next to this file. All names, ids and zips are
fictional. The demo clock is fixed (DEMO_NOW in handoff/tools/policy.py) so every credit
the oracle computes is reproducible.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIRST = ["Ana", "Ben", "Chloe", "Dev", "Elena", "Farid", "Grace", "Hiro", "Ines", "Jonas", "Kira", "Luis",
         "Maya", "Nate", "Olu", "Priya", "Quinn", "Rosa", "Sam", "Tara"]
LAST = ["Abara", "Brandt", "Castillo", "Dunn", "Ekwueme", "Fischer", "Gomez", "Haddad", "Ito", "Jensen"]
ZIPS = ["94107", "94110", "94117", "94612", "95014", "94040", "94301", "95112"]
PLANS = {
    "mobile": [("Mobile Essentials", 35.00), ("Mobile Unlimited", 65.00), ("Mobile Unlimited Plus", 85.00)],
    "home-internet": [("Fiber 300", 55.00), ("Fiber 1 Gig", 80.00), ("Fixed Wireless 100", 45.00)],
}
LINE_STATUS = ["ok"] * 7 + ["router-offline", "sim-not-provisioned", "apn-misconfigured", "speed-throttled"]
MONTHS = ["2026-09", "2026-10", "2026-11"]


def main() -> None:
    rng = random.Random(245)
    customers = []
    for i in range(42):
        cid = f"cust-{1001 + i}"
        zip_code = ZIPS[i % len(ZIPS)]
        kinds = ["mobile", "home-internet"] if i % 3 != 2 else ["mobile"]
        lines = []
        for n, kind in enumerate(kinds, start=1):
            plan, price = rng.choice(PLANS[kind])
            lines.append({"line_id": f"L-{1001 + i}-{n}", "kind": kind, "plan": plan, "monthly_price": price,
                          "status": rng.choice(LINE_STATUS), "zip": zip_code})
        invoices = []
        for m in MONTHS:
            total = round(sum(ln["monthly_price"] for ln in lines) * 1.0875, 2)
            invoices.append({"invoice_id": f"INV-{1001 + i}-{m.replace('-', '')}", "month": m,
                             "total": total, "status": "paid" if m != "2026-11" else "open"})
        customers.append({"customer_id": cid, "name": f"{FIRST[i % len(FIRST)]} {LAST[i % len(LAST)]}",
                          "zip": zip_code, "lines": lines, "invoices": invoices, "credits": []})
    # Hand-placed facts the scenarios rely on (data/scenarios.json).
    by_id = {c["customer_id"]: c for c in customers}
    pins = {
        "cust-1042": ("94107", {"home-internet": ("Fiber 1 Gig", 80.00, "ok"), "mobile": ("Mobile Unlimited", 65.00, "ok")}),
        "cust-1007": ("94110", {"home-internet": ("Fiber 300", 55.00, "ok")}),
        "cust-1013": ("95014", {"home-internet": ("Fiber 300", 55.00, "ok")}),
        "cust-1004": ("94612", {"mobile": ("Mobile Unlimited", 65.00, "ok")}),
        "cust-1016": ("94040", {"home-internet": ("Fiber 1 Gig", 80.00, "router-offline")}),
        "cust-1020": ("94301", {"mobile": ("Mobile Essentials", 35.00, "apn-misconfigured")}),
    }
    for cid, (zip_code, kinds) in pins.items():
        c = by_id[cid]
        c["zip"] = zip_code
        for kind in kinds:
            if not any(ln["kind"] == kind for ln in c["lines"]):
                n = len(c["lines"]) + 1
                c["lines"].append({"line_id": f"L-{cid[5:]}-{n}", "kind": kind, "zip": zip_code,
                                   "plan": "", "monthly_price": 0.0, "status": "ok"})
        for ln in c["lines"]:
            ln["zip"] = zip_code
            if ln["kind"] in kinds:
                ln["plan"], ln["monthly_price"], ln["status"] = kinds[ln["kind"]]
        for inv in c["invoices"]:
            inv["total"] = round(sum(ln["monthly_price"] for ln in c["lines"]) * 1.0875, 2)
    outages = [
        # The hero outage: 94107 fiber, last night → this morning (14 h).
        {"outage_id": "OUT-2026-1117-01", "zip": "94107", "service": "home-internet",
         "start": "2026-11-17T19:00:00Z", "end": "2026-11-18T09:00:00Z", "status": "resolved"},
        # Short blip: below the 4-hour credit threshold.
        {"outage_id": "OUT-2026-1116-02", "zip": "94110", "service": "home-internet",
         "start": "2026-11-16T13:00:00Z", "end": "2026-11-16T15:30:00Z", "status": "resolved"},
        # Ongoing mobile outage.
        {"outage_id": "OUT-2026-1118-03", "zip": "94612", "service": "mobile",
         "start": "2026-11-18T06:00:00Z", "end": None, "status": "ongoing"},
        # Long multi-day outage (credit capped at whole days).
        {"outage_id": "OUT-2026-1112-04", "zip": "95014", "service": "home-internet",
         "start": "2026-11-12T08:00:00Z", "end": "2026-11-14T20:00:00Z", "status": "resolved"},
    ]
    (HERE / "customers.json").write_text(json.dumps(customers, indent=1) + "\n")
    (HERE / "outages.json").write_text(json.dumps(outages, indent=1) + "\n")
    print(f"wrote {len(customers)} customers, {len(outages)} outages")


if __name__ == "__main__":
    main()

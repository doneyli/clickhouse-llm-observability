"""
Generate the synthetic annuity service requests (retirement / annuities use case): data/claims/service.json.

In-force requests from living owners — partial withdrawals, full surrenders, replacements (1035 exchanges) —
checked against the servicing SOPs (SOP-SVC-*: surrender charges, early-distribution tax, RMD, rider forfeiture,
replacement disclosure and review, suitability profile, senior consumers, compensation disclosure, NY best
interest). Every scenario is explicit; the script exits 1 unless every servicing rule fires at least twice.

    .venv/bin/python scripts/make_service_requests.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import catalog, oracle  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "claims" / "service.json"
FIRST = ["Eleanor", "Theodore", "Rosalind", "Bernard", "Judith", "Clarence", "Miriam", "Desmond", "Beatrice", "Wallace",
         "Lorraine", "Stanley", "Agnes", "Roland", "Phyllis", "Vernon", "Edith", "Lyle", "Constance", "Orville"]
LAST = ["Ashcroft", "Bellamy", "Carrington", "Draper", "Ellsworth", "Fairbanks", "Gilchrist", "Hargrove", "Ingram",
        "Jessop", "Kendrick", "Langley", "Merriweather", "Northcott", "Ormsby", "Prescott", "Quimby", "Radcliffe"]
AGENTS = ["A. Ferreira (agent 40112)", "M. Goldberg (agent 40388)", "T. Nwosu (agent 41027)", "J. Park (agent 40951)"]
PRODUCTS = ["Northwind Secure Growth 7 (fixed indexed annuity)", "Northwind Income Builder (variable annuity)"]


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def build(i: int, rng: random.Random, *, state: str, rtype: str, months: int, age: float, cv: float = 180000,
          amount: float | None = None, qualified: bool = False, rmd_remaining: float = 0, rider: str | None = None,
          agent: bool = False, profile: str = "complete", horizon: float = 10, new_period: int = 7,
          w4r: bool = True, rider_ack: bool = False, notice: bool = True, scenario: str, request_id: str | None = None,
          tags: tuple = ()) -> dict:
    req_date = date(2026, 9, 1) + timedelta(days=rng.randint(0, 40))
    issue = add_months(req_date, -months) - timedelta(days=1)
    assert oracle.months_between(issue, req_date) == months, (i, issue, req_date)
    dob = req_date - timedelta(days=int(age * 365.25) + rng.randint(1, 20))
    prof = {"annual_income": rng.choice([42000, 65000, 88000, 120000]), "liquid_net_worth": rng.choice(
        [60000, 150000, 400000]), "risk_tolerance": rng.choice(["conservative", "moderate"]),
            "time_horizon_years": horizon, "liquidity_needs": rng.choice(["low", "moderate"])}
    if profile == "incomplete":
        prof["liquidity_needs"] = None
        prof["time_horizon_years"] = None if rtype != "replacement" else horizon
    rec = {
        "request_id": request_id or f"SR-2026-{100 + i:05d}", "scenario": scenario, "tags": list(tags),
        "line": catalog.SERVICE, "state": state, "request_type": rtype, "request_date": str(req_date),
        "received_via": "agent" if agent else "owner portal",
        "contract_number": f"NLA-A-{620000 + i * 41}", "product": rng.choice(PRODUCTS), "issue_date": str(issue),
        "contract_value": float(cv), "qualified": qualified, "rmd_remaining": float(rmd_remaining),
        "guaranteed_rider": rider, "amount": float(amount) if amount is not None else (
            float(cv) if rtype in ("surrender", "replacement") else round(cv * 0.05, 2)),
        "owner": {"name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "date_of_birth": str(dob)},
        "agent_recommended": agent, "agent": rng.choice(AGENTS) if agent else None, "profile": prof,
        "documents": {"withholding_form": w4r, "rider_ack": rider_ack, "replacement_notice": notice},
    }
    if rtype == "replacement":
        rec.update({"new_product": "Competitor Premier Index 10 (fixed indexed annuity)" if new_period >= 10
                    else "Northwind Secure Growth 7 (fixed indexed annuity)", "new_surrender_period_years": new_period})
    rec["expected"] = oracle.expected(rec)
    rec["candidate_sops"] = oracle.candidate_sops(catalog.SERVICE, state)
    return rec


def requests() -> list[dict]:
    rng = random.Random(20261019)
    S = []
    b = lambda **kw: S.append(build(len(S), rng, **kw))  # noqa: E731
    b(request_id="SR-2026-00077", state="NY", rtype="replacement", months=30, age=76.4, cv=240000, rider="GLWB income rider",
      agent=True, horizon=4, new_period=7, scenario="hero: NY senior replacement — surrender period, 30-month-old "
      "contract, rider loss, horizon shorter than the new surrender period", tags=("hero",))
    b(state="NJ", rtype="withdrawal", months=40, age=66.2, amount=12000, scenario="withdrawal within the free amount")
    b(state="TX", rtype="withdrawal", months=15, age=63.0, amount=40000, scenario="withdrawal above the free amount in year 2 (6%)")
    b(state="FL", rtype="withdrawal", months=50, age=55.1, amount=30000, scenario="under 59½, above the free amount in year 5")
    b(state="CA", rtype="withdrawal", months=70, age=57.8, amount=9000, scenario="under 59½, within the free amount")
    b(state="NY", rtype="withdrawal", months=120, age=74.3, qualified=True, rmd_remaining=8200, amount=10000,
      scenario="qualified, age 74, RMD still due")
    b(state="NJ", rtype="withdrawal", months=110, age=79.0, qualified=True, rmd_remaining=6400, agent=True, amount=15000,
      scenario="qualified, age 79, RMD due, agent-recommended (senior review)")
    b(state="TX", rtype="withdrawal", months=96, age=68.0, w4r=False, amount=8000, scenario="withholding election missing")
    b(state="FL", rtype="surrender", months=100, age=71.0, rider="GLWB income rider", scenario="surrender forfeits a rider, no acknowledgment")
    b(state="CA", rtype="surrender", months=27, age=68.5, scenario="surrender in year 3 (5%)")
    b(state="NY", rtype="surrender", months=125, age=70.0, rider="return-of-premium death benefit", rider_ack=True,
      scenario="surrender after the charge period, rider acknowledged")
    b(state="NJ", rtype="replacement", months=100, age=64.0, agent=True, horizon=10, new_period=7,
      scenario="clean replacement after the surrender period")
    b(state="TX", rtype="replacement", months=20, age=61.0, agent=True, horizon=8, new_period=7,
      scenario="replacement of a 20-month-old contract in its surrender period")
    b(state="FL", rtype="replacement", months=96, age=69.0, agent=True, notice=False, profile="incomplete", horizon=9,
      scenario="replacement: notice missing and profile incomplete")
    b(state="CA", rtype="replacement", months=104, age=66.0, agent=False, horizon=3, new_period=10,
      scenario="owner-initiated replacement into a 10-year surrender period with a 3-year horizon")
    b(state="NY", rtype="replacement", months=115, age=62.0, agent=True, horizon=12, new_period=7,
      scenario="clean NY replacement (60-day free look, written best-interest basis)")
    b(state="NJ", rtype="withdrawal", months=60, age=70.0, agent=True, profile="incomplete", amount=11000,
      scenario="agent-recommended withdrawal, profile incomplete")
    b(state="TX", rtype="replacement", months=90, age=77.0, rider="GLWB income rider", agent=True, horizon=9, new_period=7,
      scenario="senior replacement that ends a guaranteed rider")
    b(state="CA", rtype="withdrawal", months=80, age=80.2, agent=True, amount=10000,
      scenario="agent-recommended withdrawal for an 80-year-old (senior review)")
    b(state="NY", rtype="withdrawal", months=26, age=52.0, w4r=False, amount=45000,
      scenario="age 52, above the free amount in year 3, withholding election missing")
    b(state="TX", rtype="surrender", months=62, age=72.0, rider="GLWB income rider",
      scenario="surrender in year 6 that forfeits a rider, no acknowledgment")
    b(state="NJ", rtype="replacement", months=108, age=67.0, agent=True, notice=False, horizon=10, new_period=7,
      scenario="replacement notice missing")
    return S


def main() -> int:
    reqs = requests()
    OUT.write_text(json.dumps(reqs, indent=1) + "\n")
    cov = Counter(r for q in reqs for r in q["expected"]["rule_ids"])
    ids = sorted({r["rule_id"] for r in catalog.all_rules() if catalog.SERVICE in r["lines"]})
    print(f"{len(reqs)} service requests → {OUT}")
    print("decisions:", dict(Counter(q["expected"]["decision"] for q in reqs)))
    low = [i for i in ids if cov.get(i, 0) < 2]
    for i in ids:
        print(f"  {i:<12} {cov.get(i, 0)}")
    hero = reqs[0]["expected"]
    print("hero:", hero["decision"], hero["rule_ids"], "critical", hero["critical_rule_ids"])
    if low:
        print("UNDER-COVERED:", low)
        return 1
    print("coverage OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

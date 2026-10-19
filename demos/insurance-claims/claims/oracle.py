"""
The rule oracle — computes, deterministically, what SHOULD happen on a claim.

It evaluates the structured rules (data/sops/rules.json, rendered from catalog.py)
against claim facts. It is the ground truth for the golden dataset and for the
evaluators; the agent never sees it, and never sees the structured rules — it reads
the prose SOPs.

    facts = derive_facts(claim)
    out   = expected(claim)   # rule ids, critical ids, decision, amounts, letter requirements

Rules that apply = in scope (line, state or ALL), in effect on the date of death,
condition true — minus every rule a firing rule supersedes (a state rule replacing the
company standard, ERISA replacing state timelines, WA's 12-month suicide exclusion …).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from claims import catalog

RULES_JSON = Path(__file__).resolve().parent.parent / "data" / "sops" / "rules.json"
DECISIONS = ["PAY", "PEND", "DENY", "REFER_SIU"]
_PRECEDENCE = {"refer_siu": 3, "deny": 2, "refer_review": 2, "hold": 1, "none": 0}
_DECISION_OF = {3: "REFER_SIU", 2: "DENY", 1: "PEND", 0: "PAY"}
# Annuity servicing decisions: a supervisory/compliance review outranks a hold, which outranks processing.
SERVICE_DECISIONS = ["APPROVE", "PEND", "REFER_REVIEW"]
_SERVICE_DECISION_OF = {2: "REFER_REVIEW", 1: "PEND", 0: "APPROVE"}


def is_service(case: dict) -> bool:
    return case.get("line") == catalog.SERVICE


def as_of(case: dict) -> str:
    """The date rules are evaluated at: the date of death for a claim, the request date for a service request."""
    return case.get("date_of_death") or case["request_date"]


@lru_cache(maxsize=1)
def rules() -> list[dict]:
    """Structured rules — from rules.json when rendered, else straight from the catalog."""
    if RULES_JSON.exists():
        return json.loads(RULES_JSON.read_text())["rules"]
    return catalog.all_rules()


def rule_versions(rule_id: str) -> list[dict]:
    return [r for r in rules() if r["rule_id"] == rule_id]


def _d(s: Optional[str]) -> Optional[date]:
    return date.fromisoformat(s) if s else None


def months_between(start: date, end: date) -> int:
    """Whole months from start to end (a death on the 24-month anniversary is at 24)."""
    m = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        m -= 1
    return m


def age_on(dob: Optional[str], on: date) -> Optional[int]:
    if not dob:
        return None
    b = date.fromisoformat(dob)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


def gross_benefit(claim: dict) -> float:
    line = claim["line"]
    if line == catalog.LIFE:
        return float(claim["face_amount"])
    if line == catalog.GROUP:
        return float(claim["basic_amount"]) + float(claim.get("supplemental_amount") or 0)
    return float(max(claim["contract_value"], claim["premiums_paid"] - claim.get("withdrawals", 0)))


def derive_service_facts(req: dict) -> dict:
    """Facts for an annuity service request (withdrawal, surrender, replacement)."""
    on = _d(req["request_date"])
    months = months_between(_d(req["issue_date"]), on)
    year = months // 12 + 1
    sched = [7, 6, 5, 4, 3, 2, 1]
    pct = sched[year - 1] if year <= len(sched) else 0
    cv = float(req["contract_value"])
    amount = float(req.get("amount") or (cv if req["request_type"] == "surrender" else 0))
    free = round(cv * 0.10, 2)
    dob = _d(req["owner"]["date_of_birth"])
    age = (on - dob).days / 365.25
    prof = req.get("profile") or {}
    return {
        "line": req["line"], "state": req["state"], "request_type": req["request_type"],
        "months_since_issue": months, "contract_year": year, "surrender_charge_pct": pct,
        "contract_value": cv, "amount": amount, "free_amount": free, "amount_above_free": round(max(0.0, amount - free), 2),
        "owner_age": round(age, 2), "qualified": bool(req.get("qualified")),
        "rmd_remaining": float(req.get("rmd_remaining") or 0),
        "withholding_form_present": bool((req.get("documents") or {}).get("withholding_form", True)),
        "has_guaranteed_rider": bool(req.get("guaranteed_rider")),
        "rider_ack_signed": bool((req.get("documents") or {}).get("rider_ack", False)),
        "replacement_notice_signed": bool((req.get("documents") or {}).get("replacement_notice", False)),
        "agent_recommended": bool(req.get("agent_recommended")),
        "profile_complete": all(prof.get(k) not in (None, "") for k in
                                ("annual_income", "liquid_net_worth", "risk_tolerance", "time_horizon_years",
                                 "liquidity_needs")),
        "horizon_shorter_than_new_period": (req["request_type"] == "replacement" and prof.get("time_horizon_years")
                                            is not None and float(prof["time_horizon_years"])
                                            < float(req.get("new_surrender_period_years") or 0)),
    }


def derive_facts(claim: dict) -> dict:
    """The fact set the rule conditions are written against."""
    if is_service(claim):
        return derive_service_facts(claim)
    dod = _d(claim["date_of_death"])
    docs = claim.get("documents") or {}
    cert = docs.get("death_certificate") or {}
    stmt = docs.get("claimant_statement") or {}
    bens = claim.get("beneficiaries") or []
    surviving = [b for b in bens if b.get("survived", True)]
    spouse_share = sum(b["share_pct"] for b in surviving if b["relationship"] == "spouse")
    cert_dod = cert.get("date_of_death", claim["date_of_death"])
    stmt_dod = stmt.get("date_of_death", claim["date_of_death"])
    injected = any((d or {}).get("injection") for d in docs.values() if isinstance(d, dict))
    return {
        "line": claim["line"], "state": claim["state"],
        "months_since_issue": months_between(_d(claim["issue_date"]), dod),
        "days_proof_to_decision": (_d(claim["decision_date"]) - _d(claim["proof_date"])).days,
        "benefit_amount": gross_benefit(claim),
        "manner_of_death": claim.get("manner_of_death", "natural"),
        "policy_status": claim.get("policy_status", "in_force"),
        "foreign_death": bool(claim.get("foreign_death")),
        "ofac_match": (claim.get("screening") or {}).get("ofac_match", "none"),
        "married": (claim.get("insured") or {}).get("marital_status") == "married",
        "spouse_share_pct": spouse_share,
        "spouse_sole_beneficiary": len(surviving) == 1 and surviving[0]["relationship"] == "spouse",
        "has_minor_beneficiary": any(a is not None and a < 18 for a in (age_on(b.get("date_of_birth"), dod) for b in surviving)),
        "any_beneficiary_predeceased": len(surviving) < len(bens),
        "no_surviving_beneficiary": not surviving,
        "claimant_is_beneficiary": bool((claim.get("claimant") or {}).get("is_beneficiary", True)),
        "fy_commission_paid": bool(claim.get("fy_commission_paid")),
        "employment_ended_days": int(claim.get("employment_ended_days") or 0),
        "converted": bool(claim.get("converted")),
        "supplemental_amount": float(claim.get("supplemental_amount") or 0),
        "eoi_approved": bool(claim.get("eoi_approved")),
        "qualified": bool(claim.get("qualified")),
        "doc_inconsistency": cert_dod != stmt_dod and bool(stmt.get("present", True)),
        "embedded_instruction": injected,
        "death_certificate_present": bool(cert.get("present", True)),
        "death_certificate_certified": bool(cert.get("certified", True)),
        "claimant_statement_present": bool(stmt.get("present", True)),
        "w9_present": bool(docs.get("w9", True)),
        "hipaa_authorization_present": bool(docs.get("hipaa_authorization", False)),
        "employer_statement_present": bool(docs.get("employer_statement", True)),
        "withholding_form_present": bool(docs.get("withholding_form", True)),
    }


def _leaf(cond: dict, facts: dict) -> bool:
    v = facts.get(cond["fact"])
    op, target = cond["op"], cond.get("value")
    if op == "true":
        return v is True
    if op == "false":
        return v is False
    if v is None:
        return False
    return {"<": lambda: v < target, "<=": lambda: v <= target, ">": lambda: v > target,
            ">=": lambda: v >= target, "==": lambda: v == target, "!=": lambda: v != target,
            "in": lambda: v in target, "not_in": lambda: v not in target}[op]()


def holds(cond: Any, facts: dict) -> bool:
    if cond is True or cond is None:
        return True
    if "all" in cond:
        return all(holds(c, facts) for c in cond["all"])
    if "any" in cond:
        return any(holds(c, facts) for c in cond["any"])
    if "not" in cond:
        return not holds(cond["not"], facts)
    return _leaf(cond, facts)


def in_scope(r: dict, line: str, state: str) -> bool:
    return line in r["lines"] and ("ALL" in r["states"] or state in r["states"])


def in_effect(r: dict, on: date) -> bool:
    return _d(r["effective_from"]) <= on and (r["effective_to"] is None or on <= _d(r["effective_to"]))


def applicable(claim: dict, facts: Optional[dict] = None) -> list[dict]:
    """Rule versions that apply to the claim, after supersession.

    A replacing rule supersedes whenever it is in scope and in effect — whether or not
    its own condition holds. Washington's 12-month suicide exclusion replaces the
    24-month company rule for a death at month 18 too: neither applies, only the
    contestability review does.
    """
    facts = facts or derive_facts(claim)
    dod = _d(as_of(claim))
    live = [r for r in rules() if in_scope(r, claim["line"], claim["state"]) and in_effect(r, dod)]
    superseded = {s for r in live for s in r["supersedes"]}
    return [r for r in live if r["rule_id"] not in superseded and holds(r["condition"], facts)]


def interest(claim: dict, rule_version: dict, gross: float) -> float:
    p = rule_version["params"]
    start = _d(claim["date_of_death"]) if p["start"] == "date_of_death" else _d(claim["proof_date"])
    start = start + timedelta(days=int(p.get("offset_days", 0)))
    days = max(0, (_d(claim["decision_date"]) - start).days)
    return round(gross * p["rate"] * days / 365, 2)


def payable(claim: dict, fired: list[dict]) -> float:
    ids = {r["rule_id"] for r in fired}
    if claim["line"] == catalog.LIFE:
        amount = float(claim["face_amount"])
        if "LIFE-LAPSE-002" in ids:
            amount -= float(claim.get("overdue_premium") or 0)
        return amount
    if claim["line"] == catalog.GROUP:
        supp = float(claim.get("supplemental_amount") or 0)
        if "GRP-EOI-001" in ids:
            supp = min(supp, 150000.0)
        return float(claim["basic_amount"]) + supp
    return gross_benefit(claim)


def surrender_charge(facts: dict, fired_ids: set) -> float:
    if "SVC-WD-001" not in fired_ids:
        return 0.0
    return round(facts["amount_above_free"] * facts["surrender_charge_pct"] / 100.0, 2)


def expected_service(req: dict) -> dict:
    facts = derive_service_facts(req)
    fired = applicable(req, facts)
    level = max([_PRECEDENCE[r["effect"]] for r in fired] or [0])
    ids = {r["rule_id"] for r in fired}
    return {"rule_ids": sorted(ids),
            "critical_rule_ids": sorted({r["rule_id"] for r in fired if r["severity"] == "critical"}),
            "decision": _SERVICE_DECISION_OF[min(level, 2)],
            "letter_requirements": sorted({r["rule_id"] for r in fired if r["letter"]}),
            "surrender_charge": surrender_charge(facts, ids), "free_amount": facts["free_amount"],
            "interest_rule": None, "commission_chargeback_pct": 0, "gross_benefit": None, "interest_due": None}


def expected(claim: dict) -> dict:
    """Ground truth for one claim (or annuity service request)."""
    if is_service(claim):
        return expected_service(claim)
    facts = derive_facts(claim)
    fired = applicable(claim, facts)
    level = max([_PRECEDENCE[r["effect"]] for r in fired] or [0])
    decision = _DECISION_OF[level]
    interest_rules = [r for r in fired if r["type"] == "PAY_CALC" and "rate" in r["params"]]
    out = {
        "rule_ids": sorted({r["rule_id"] for r in fired}),
        "critical_rule_ids": sorted({r["rule_id"] for r in fired if r["severity"] == "critical"}),
        "decision": decision,
        "letter_requirements": sorted({r["rule_id"] for r in fired if r["letter"]}),
        "interest_rule": (f"{interest_rules[0]['rule_id']}@v{interest_rules[0]['version']}"
                          if interest_rules else None),
        "commission_chargeback_pct": max([r["params"].get("chargeback_pct", 0) for r in fired
                                          if r["type"] == "COMMISSION"] or [0]),
        "gross_benefit": None, "interest_due": None,
    }
    if decision == "PAY":
        gross = payable(claim, fired)
        out["gross_benefit"] = round(gross, 2)
        out["interest_due"] = interest(claim, interest_rules[0], gross) if interest_rules else 0.0
    return out


def candidate_sops(line: str, state: str) -> list[str]:
    """SOPs tagged for this line × state — what a correct retriever must return (retrieval-coverage)."""
    return sorted({s["sop_id"] for s in catalog.SOPS
                   if line in s["lines"] and ("ALL" in s["states"] or state in s["states"])})


def rule_by_id(rule_id: str, on: Optional[str] = None) -> Optional[dict]:
    """The version of a rule in effect on a date (or the latest)."""
    vs = rule_versions(rule_id)
    if not vs:
        return None
    if on:
        d = _d(on)
        vs = [r for r in vs if in_effect(r, d)]
    return max(vs, key=lambda r: r["version"]) if vs else None

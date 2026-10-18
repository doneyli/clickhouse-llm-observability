"""
The payment calculator — deterministic code, never the LLM.

The rules agent decides WHICH calculation rules apply (and which version); this
module applies their parameters from the structured rules. A missed interest rule
therefore shows up as a wrong amount, which `interest-within-tolerance` catches.
"""

from __future__ import annotations

from claims import catalog, oracle


def _version(rule_id: str, version, dod: str):
    if version not in (None, "", 0):
        try:
            v = next((r for r in oracle.rule_versions(rule_id) if r["version"] == int(version)), None)
            if v is not None:
                return v
        except (TypeError, ValueError):
            pass
    return oracle.rule_by_id(rule_id, dod)


def calculate(observed: dict, rules: list[dict], decision: str) -> dict:
    """{gross_benefit, interest_due, interest_rule} from the rules the agent extracted."""
    if decision != "PAY":
        return {"gross_benefit": None, "interest_due": None, "interest_rule": None}
    ids = {r.get("rule_id") for r in rules}
    line = observed["line"]
    if line == catalog.LIFE:
        gross = float(observed["face_amount"])
        if "LIFE-LAPSE-002" in ids:
            gross -= float(observed.get("overdue_premium") or 0)
    elif line == catalog.GROUP:
        supp = float(observed.get("supplemental_amount") or 0)
        if "GRP-EOI-001" in ids:
            supp = min(supp, 150000.0)
        gross = float(observed["basic_amount"]) + supp
    else:
        gross = oracle.gross_benefit(observed)
    interest_rule, interest = None, 0.0
    for r in rules:
        v = _version(r.get("rule_id", ""), r.get("version"), observed["date_of_death"])
        if v and v["type"] == "PAY_CALC" and "rate" in v["params"]:
            interest_rule = f"{v['rule_id']}@v{v['version']}"
            interest = oracle.interest(observed, v, gross)
            break
    return {"gross_benefit": round(gross, 2), "interest_due": interest, "interest_rule": interest_rule}

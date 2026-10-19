"""
A simulated claims examiner, for generated traffic and the CLI.

The examiner knows the right answer (the oracle) and reviews the agent's recommendation
the way a careful examiner would: approve when the decision is right, override when it
is not, with a reason from the error-analysis taxonomy:

    missed-rule · wrong-rule · calc-error · doc-misread · policy-data-error · other
"""

from __future__ import annotations

import random

from claims import evals

REASONS = ["missed-rule", "wrong-rule", "calc-error", "doc-misread", "policy-data-error", "other"]
EXAMINERS = ["examiner.jlopez", "examiner.mchen", "examiner.rpatel", "examiner.okim"]


def simulate(claim: dict, res: dict, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random(claim.get("claim_id") or claim.get("request_id"))
    exp = claim["expected"]
    got_rules = set(res.get("rules") or [])
    missed = sorted(set(exp["rule_ids"]) - got_rules)
    extra = sorted(got_rules - set(exp["rule_ids"]))
    who = rng.choice(EXAMINERS)
    rec = res.get("recommendation")
    calc = res.get("calculation") or {}
    if rec != exp["decision"]:
        reason = "missed-rule" if missed else ("wrong-rule" if extra else "other")
        crit = sorted(set(exp["critical_rule_ids"]) & set(missed))
        return {"action": "override", "decision": exp["decision"], "reason": reason, "missed_rule_ids": missed,
                "examiner_id": who,
                "note": (f"Recommended {rec}; should be {exp['decision']}. "
                         + (f"Missed critical rule {', '.join(crit)}. " if crit else "")
                         + (f"Missed {', '.join(missed)}. " if missed else "")
                         + (f"Not applicable: {', '.join(extra)}." if extra else "")).strip()}
    from claims import oracle
    money_or_critical = [r for r in missed if r in exp["critical_rule_ids"] or any(
        v["type"] == "COMMISSION" for v in oracle.rule_versions(r))]
    if money_or_critical:  # right decision, but a chargeback or a critical step would be lost
        return {"action": "override", "decision": rec, "reason": "missed-rule", "missed_rule_ids": missed,
                "examiner_id": who, "note": f"Decision stands, but the recommendation missed {', '.join(money_or_critical)}"
                                            " — added to the action list."}
    if exp["decision"] == "PAY" and exp.get("interest_due") is not None and calc.get("interest_due") is not None \
            and abs(calc["interest_due"] - exp["interest_due"]) > evals.TOLERANCE_USD:
        return {"action": "override", "decision": "PAY", "reason": "calc-error", "missed_rule_ids": missed,
                "examiner_id": who,
                "note": f"Interest {calc['interest_due']} should be {exp['interest_due']} ({exp['interest_rule']})."}
    return {"action": "approve", "examiner_id": who,
            "note": "Approved as recommended." + (f" (Missed minor rule(s) noted: {', '.join(missed)}.)" if missed else "")}

"""
Offline tests for the photo-audit code evaluators. No network, no keys, no cost.

These pin the invariants the demo's argument rests on. Two matter more than the
rest, and both are the kind of bug that makes a demo lie rather than crash:

  * An UNREADABLE scene where the agent correctly declines must score as a PASS
    on every emitted check. Grading it against claim verdicts derived from
    attributes it could not see marks correct behaviour wrong.
  * An agent that believes the marketing copy over the photo must FAIL the
    verdict check while still PASSING every attribute-shape check — that gap is
    the whole reason a vision judge earns its cost.

Run:
    ./.venv/bin/python scripts/test_photo_scoring.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import photo_contract as pc  # noqa: E402
from agent.photo_scoring import (  # noqa: E402
    NotApplicable, PHOTO_CODE_EVALUATORS, run_photo_code_evaluators,
)

_fails = []


def check(cond, label, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fails.append(label)


def by_name(results):
    return {r.name: r for r in results}


# --- fixtures -------------------------------------------------------------
DATED = {"room_type": "kitchen", "cabinetry": "dark_dated", "countertop": "laminate",
         "flooring": "tile", "clutter": "moderate", "window_count": 1,
         "appliances": ["oven", "fridge"]}
CLAIMS = ["recently renovated", "floods with natural light", "on a quiet street"]


def audit(*, attrs, claim_verdicts, verdict, listing="BCN-202", conf=0.9):
    """Build an audit output in the contract's shape."""
    return {
        "listing_id": listing, "cited_listing_id": listing,
        "extracted_attributes": dict(attrs),
        "extraction_confidence": conf,
        "claims": [{"claim": c, "verdict": v, "evidence": "…"}
                   for c, v in claim_verdicts.items()],
        "verdict": verdict, "corrected_copy": "…",
    }


print("\n=== contract/evaluator wiring ===")
check(len(PHOTO_CODE_EVALUATORS) == len(pc.CODE_SCORE_NAMES),
      "one evaluator per contract score name",
      f"{len(PHOTO_CODE_EVALUATORS)} vs {len(pc.CODE_SCORE_NAMES)}")
check({f.score_name for f in PHOTO_CODE_EVALUATORS} == set(pc.CODE_SCORE_NAMES),
      "evaluator score names match the contract exactly")

# --- 1. the honest agent on a readable, contradicting photo ---------------
print("\n=== 1. correct agent, contradicted scene ===")
exp = pc.build_expected_output(CLAIMS, DATED)
r = by_name(run_photo_code_evaluators(
    audit(attrs=DATED,
          claim_verdicts={"recently renovated": "contradicted",
                          "floods with natural light": "contradicted",
                          "on a quiet street": "unverifiable"},
          verdict="contradicted"),
    {**exp, "claims": CLAIMS}))
check(r[pc.SCORE_VERDICT_EXACT].value is True, "verdict-exact-match passes")
check(r[pc.SCORE_ABSTAINS_UNVERIFIABLE].value == 1.0, "abstained on the quiet street")
check(r[pc.SCORE_ATTRS_EXACT_MATCH].value == 1.0, "attributes-exact-match 1.0")
check(r[pc.SCORE_CLAIM_COVERAGE].value == 1.0, "claim-coverage 1.0")

# --- 2. THE DEMO'S CORE CASE: believes the copy, not the photo ------------
print("\n=== 2. agent believes the marketing copy over the photo ===")
r = by_name(run_photo_code_evaluators(
    audit(attrs=DATED,
          claim_verdicts={"recently renovated": "supported",
                          "floods with natural light": "supported",
                          "on a quiet street": "unverifiable"},
          verdict="supported"),
    {**exp, "claims": CLAIMS}))
check(r[pc.SCORE_VERDICT_EXACT].value is False,
      "verdict-exact-match FAILS (the defect is caught)")
check(r[pc.SCORE_ATTRS_SCHEMA_VALID].value is True
      and r[pc.SCORE_CLOSED_VOCABULARY].value is True
      and r[pc.SCORE_ATTRS_EXACT_MATCH].value == 1.0,
      "every attribute-shape check still PASSES — shape checks cannot see this")

# --- 3. UNREADABLE scene, agent correctly declines ------------------------
print("\n=== 3. unreadable scene, agent declines (must be all-pass) ===")
exp_unread = pc.build_expected_output(CLAIMS, DATED, unreadable=True)
check(exp_unread["verdict"] == "needs_better_photo",
      "ground truth expects needs_better_photo")
results = run_photo_code_evaluators(
    audit(attrs={}, claim_verdicts={c: "unverifiable" for c in CLAIMS},
          verdict="needs_better_photo", conf=0.2),
    {**exp_unread, "claims": CLAIMS}, include_not_applicable=True)
r = by_name(results)
check(r[pc.SCORE_VERDICT_EXACT].value is True,
      "verdict-exact-match PASSES for declining")
check(isinstance(r[pc.SCORE_ABSTAINS_UNVERIFIABLE], NotApplicable),
      "abstains-when-unverifiable stands down (not a 0.0)")
check(isinstance(r[pc.SCORE_ATTRS_EXACT_MATCH], NotApplicable),
      "attributes-exact-match stands down (not a 0.0)")
scored = [x for x in results if not isinstance(x, NotApplicable)]
bad = [s.name for s in scored
       if (s.value is False or (isinstance(s.value, float) and s.value < 1.0))]
check(not bad, "NO emitted score penalises the correct behaviour", str(bad))

# --- 4. unreadable scene, agent audits anyway (over-confidence) -----------
print("\n=== 4. unreadable scene, agent audits anyway ===")
r = by_name(run_photo_code_evaluators(
    audit(attrs=DATED, claim_verdicts={c: "supported" for c in CLAIMS},
          verdict="supported", conf=0.9),
    {**exp_unread, "claims": CLAIMS}, include_not_applicable=True))
check(r[pc.SCORE_VERDICT_EXACT].value is False,
      "over-confidence is caught by verdict-exact-match")
check(isinstance(r[pc.SCORE_ABSTAINS_UNVERIFIABLE], NotApplicable),
      "abstains still stands down rather than double-penalising")

# --- 5. a wrong extraction is caught deterministically -------------------
print("\n=== 5. extractor misreads the photo ===")
wrong = {**DATED, "window_count": 3, "countertop": "stone",
         "appliances": ["oven", "fridge", "dishwasher"]}
r = by_name(run_photo_code_evaluators(
    audit(attrs=wrong, claim_verdicts={c: "supported" for c in CLAIMS},
          verdict="supported"), {**exp, "claims": CLAIMS}))
v = r[pc.SCORE_ATTRS_EXACT_MATCH].value
check(0.0 < v < 1.0, "attributes-exact-match catches it numerically", f"value={v}")
check("window_count" in r[pc.SCORE_ATTRS_EXACT_MATCH].comment,
      "comment names the wrong attribute (failures must be inspectable)",
      r[pc.SCORE_ATTRS_EXACT_MATCH].comment[:90])
check(r[pc.SCORE_ATTRS_SCHEMA_VALID].value is True,
      "still schema-valid — wrong is not the same as malformed")
# appliance ORDER must not decide correctness
r2 = by_name(run_photo_code_evaluators(
    audit(attrs={**DATED, "appliances": ["fridge", "oven"]},
          claim_verdicts={c: "unverifiable" for c in CLAIMS},
          verdict="unverifiable"), {**exp, "claims": CLAIMS}))
check(r2[pc.SCORE_ATTRS_EXACT_MATCH].value == 1.0,
      "appliance list order does not affect the score")

# --- 6. no ground truth (the live-traffic shape) -------------------------
print("\n=== 6. live traffic: no ground truth available ===")
results = run_photo_code_evaluators(
    audit(attrs=DATED, claim_verdicts={c: "unverifiable" for c in CLAIMS},
          verdict="unverifiable"), {"claims": CLAIMS})
names = {s.name for s in results}
check(pc.SCORE_VERDICT_EXACT not in names,
      "verdict-exact-match is absent, not invented")
check(pc.SCORE_ATTRS_EXACT_MATCH not in names,
      "attributes-exact-match is absent, not invented")
check(pc.SCORE_CLAIM_COVERAGE in names,
      "claim-coverage still measurable when the caller supplies the claim list")

print()
if _fails:
    print(f"TEST PHOTO SCORING: FAILED — {len(_fails)} check(s)")
    for f in _fails:
        print(f"  - {f}")
    sys.exit(1)
print("TEST PHOTO SCORING: PASSED")

"""
ROI assumptions — ONE place, labelled as assumptions everywhere they surface (dashboards, score comments).
Replace them with the carrier's measured baselines in a POC; nothing here is a measured result.

  MANUAL_MINUTES       examiner time to research the SOPs that apply to one death claim, by hand
  REVIEW_MINUTES       examiner time to review an agent recommendation (approve / override)
  EXAMINER_COST_HOUR   fully loaded examiner cost per hour (USD)
"""

MANUAL_MINUTES = 45.0
REVIEW_MINUTES = {"approve": 8.0, "override": 22.0}
EXAMINER_COST_HOUR = 55.0
NOTE = (f"demo assumption: {MANUAL_MINUTES:.0f} min manual SOP research vs {REVIEW_MINUTES['approve']:.0f} min review "
        f"(approve) / {REVIEW_MINUTES['override']:.0f} min (override), at ${EXAMINER_COST_HOUR:.0f}/h — see claims/business.py")


def minutes_saved(action: str) -> float:
    return MANUAL_MINUTES - REVIEW_MINUTES["approve" if action == "approve" else "override"]


def value_usd(action: str) -> float:
    return round(minutes_saved(action) / 60.0 * EXAMINER_COST_HOUR, 2)

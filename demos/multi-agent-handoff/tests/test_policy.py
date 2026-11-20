"""The credit oracle and the write-path guard that enforces it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff.tools import crm  # noqa: E402


def test_hero_credit_is_one_day_of_fiber():
    r = crm.compute_credit("L-1042-2", "OUT-2026-1117-01")  # 14 h on an $80 line, 30-day month
    assert (r["eligible"], r["amount"], r["days"], r["hours"]) == (True, 2.67, 1, 14.0)


def test_multi_day_outage_rounds_up_to_whole_days():
    r = crm.compute_credit("L-1013-2", "OUT-2026-1112-04")  # 60 h → 3 days on a $55 line
    assert (r["amount"], r["days"]) == (5.5, 3)


def test_short_blip_is_not_eligible():
    r = crm.compute_credit("L-1007-2", "OUT-2026-1116-02")  # 2.5 h
    assert not r["eligible"] and "under 4 hours" in r["reason"]


def test_no_confirmed_outage_means_no_credit():
    r = crm.compute_credit("L-1042-2", None)
    assert not r["eligible"] and r["reason"] == "no confirmed outage"


def test_outage_on_other_service_does_not_apply():
    r = crm.compute_credit("L-1042-1", "OUT-2026-1117-01")  # mobile line, fiber outage
    assert not r["eligible"]


def test_issue_credit_refuses_a_wrong_amount_and_records_the_right_one():
    st = crm.RunState()
    assert crm.issue_credit(st, "L-1042-2", 5.00, "outage", "OUT-2026-1117-01")["issued"] is False
    assert crm.issue_credit(st, "L-1042-2", 2.67, "outage", "OUT-2026-1117-01")["issued"] is True
    assert st.credits == [{"line_id": "L-1042-2", "amount": 2.67, "reason": "outage", "outage_id": "OUT-2026-1117-01"}]


def test_no_technician_during_an_area_outage():
    st = crm.RunState()
    assert crm.book_technician(st, "L-1042-2")["booked"] is False
    assert crm.book_technician(st, "L-1016-2")["booked"] is True

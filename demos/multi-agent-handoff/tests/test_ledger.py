"""The shared back-office ledger: one system of record for every agent and process."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff.tools import crm  # noqa: E402


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "LEDGER_PATH", tmp_path / "ledger.sqlite")


def test_a_credit_one_agent_issues_is_visible_to_another():
    # billing on tech-support's peer hop, then billing again (or anyone) on a later turn
    first = crm.RunState(conversation_id="conv-a")
    assert crm.issue_credit(first, "L-1042-2", 2.67, "outage", "OUT-2026-1117-01")["issued"] is True
    later = crm.RunState(conversation_id="conv-a")
    inv = crm.get_invoice("cust-1042", state=later)
    assert [c["amount"] for c in inv["credits_applied"]] == [2.67]
    assert later.credits == []  # effects are what THIS call wrote


def test_the_same_credit_is_never_issued_twice():
    a, b = crm.RunState(conversation_id="conv-b"), crm.RunState(conversation_id="conv-b")
    assert crm.issue_credit(a, "L-1042-2", 2.67, "outage", "OUT-2026-1117-01")["issued"] is True
    again = crm.issue_credit(b, "L-1042-2", 2.67, "outage", "OUT-2026-1117-01")
    assert again["issued"] is False and again["reason"] == "already credited for this outage"
    assert len(crm.ledger_snapshot("conv-b")["credits"]) == 1


def test_conversations_do_not_see_each_other():
    crm.issue_credit(crm.RunState(conversation_id="conv-c"), "L-1042-2", 2.67, "outage", "OUT-2026-1117-01")
    assert crm.ledger_snapshot("conv-d") == {"credits": [], "technicians": []}


def test_one_technician_visit_per_line():
    st = crm.RunState(conversation_id="conv-e")
    assert crm.book_technician(st, "L-1016-2")["booked"] is True
    assert crm.book_technician(crm.RunState(conversation_id="conv-e"), "L-1016-2")["booked"] is False


@pytest.mark.parametrize("conversation_id", [None, "conv-unknown-line"])
def test_unknown_line_never_creates_a_phantom_booking(conversation_id):
    state = crm.RunState(conversation_id=conversation_id)
    result = crm.book_technician(state, "L-DOES-NOT-EXIST")
    assert result == {"booked": False, "error": "unknown line L-DOES-NOT-EXIST"}
    assert state.technicians == []
    if conversation_id:
        assert crm.ledger_snapshot(conversation_id)["technicians"] == []

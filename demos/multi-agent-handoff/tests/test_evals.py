"""The experiment evaluators and the routing dataset they score against."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from handoff import datasets, evals  # noqa: E402

HERO = next(i for i in datasets.routing_items() if i["id"] == "routing-credit-01")["expected_output"]
FAQ = next(i for i in datasets.routing_items() if i["id"] == "routing-faq-01")["expected_output"]


def _run(**kw):
    base = {"topology": "a2a", "reply": "", "agent_path": [], "first_handoff": None, "handoffs": [], "tools": [],
            "effects": {"credits": [], "technicians": []}}
    return {**base, **kw}


def _scores(output, expected):
    return {e.name: e.value for f in evals.ITEM_EVALUATORS for e in f(output=output, expected_output=expected)}


GOOD_HERO = _run(
    reply="Billing applied a $2.67 credit to your fiber line.",
    agent_path=["concierge", "tech-support", "billing"], first_handoff="tech-support",
    handoffs=[{"kind": "transfer", "from": "concierge", "to": "tech-support",
               "payload": {"customer_id": "cust-1042", "line_id": "L-1042-2", "symptom": "down"}},
              {"kind": "delegate", "from": "tech-support", "to": "billing",
               "payload": {"customer_id": "cust-1042", "line_id": "L-1042-2", "issue_summary": "14 h outage"}}],
    tools=["concierge:get_customer", "tech-support:check_outage", "billing:compute_credit", "billing:issue_credit"],
    effects={"credits": [{"line_id": "L-1042-2", "amount": 2.67}], "technicians": []})


def test_dataset_is_40_items_with_oracle_amounts():
    items = datasets.routing_items()
    assert len(items) == 40 and len({i["id"] for i in items}) == 40
    assert HERO["outcome"] == "credit:L-1042-2:2.67" and HERO["first_handoff"] == "tech-support"
    assert FAQ["first_handoff"] is None and FAQ["required_fields"] == []


def test_a_correct_hero_run_scores_full_marks():
    s = _scores(GOOD_HERO, HERO)
    assert s == {"handoff-target-correct": 1, "path-match": 1, "path-contains": 1, "handoff-context-complete": 1,
                 "credit-amount-correct": 1, "resolution-correct": 1, "reply-mentions-facts": 1,
                 "outage-verified-first": 1}


def test_f1_misroute_is_caught_by_routing_and_process_but_not_politeness():
    run = _run(reply="Done! I've credited $2.67.", agent_path=["concierge", "billing"], first_handoff="billing",
               handoffs=[{"kind": "transfer", "from": "concierge", "to": "billing",
                          "payload": {"customer_id": "cust-1042", "line_id": "L-1042-2", "issue_summary": "credit"}}],
               tools=["billing:compute_credit"], effects={"credits": [], "technicians": []})
    s = _scores(run, HERO)
    assert s["handoff-target-correct"] == 0 and s["path-match"] == 0 and s["outage-verified-first"] == 0
    assert s["resolution-correct"] == 0 and s["credit-amount-correct"] == 0  # the ledger says no credit
    assert s["reply-mentions-facts"] == 1  # the reply alone looks right: why outcome checks read the ledger


def test_f2_context_loss_names_the_missing_field():
    run = {**GOOD_HERO, "handoffs": [{**GOOD_HERO["handoffs"][0], "payload": {"customer_id": "cust-1042",
                                                                             "symptom": "down"}},
                                     GOOD_HERO["handoffs"][1]]}
    ev = next(e for e in evals.handoff_context_complete(output=run) if e.name == "handoff-context-complete")
    assert ev.value == 0.5 and "concierge→tech-support: line_id" in ev.comment


def test_single_agent_gets_outcome_and_process_scores_only():
    run = {**GOOD_HERO, "topology": "single", "agent_path": ["single"], "first_handoff": None, "handoffs": [],
           "tools": ["single:check_outage", "single:compute_credit", "single:issue_credit"]}
    assert set(_scores(run, HERO)) == {"credit-amount-correct", "resolution-correct", "reply-mentions-facts",
                                       "outage-verified-first"}


def test_answering_a_faq_without_a_handoff():
    s = _scores(_run(agent_path=["concierge"]), FAQ)
    assert s["handoff-target-correct"] == 1 and s["unnecessary-handoff"] == 0 and s["resolution-correct"] == 1
    s = _scores(_run(agent_path=["concierge", "billing"], first_handoff="billing"), FAQ)
    assert s["handoff-target-correct"] == 0 and s["unnecessary-handoff"] == 1


def test_a_duplicate_credit_fails_resolution():
    run = {**GOOD_HERO, "effects": {"credits": [{"line_id": "L-1042-2", "amount": 2.67}] * 2, "technicians": []}}
    assert _scores(run, HERO)["resolution-correct"] == 0

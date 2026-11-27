"""The Langfuse code evaluators, run locally exactly as uploaded (evaluators/*.py)."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "evaluators"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CONTEXT, LOOP = _load("handoff_context_complete"), _load("handoff_loop")


def _ctx(input=None, metadata=None, output=None):
    return SimpleNamespace(observation=SimpleNamespace(input=input, output=output, metadata=metadata, tool_calls=[]),
                           experiment=None)


def _scores(result):
    return {s.name: (s.value, s.comment) for s in result.scores}


def _v5(md):  # how the v5 SDK writes metadata values: JSON-encoded strings
    return {k: json.dumps(v) for k, v in md.items()}


HANDOFF_MD = {"role": "handoff", "handoff_kind": "transfer", "handoff_to": "tech-support",
              "required_fields": "customer_id,line_id,symptom"}


def test_complete_transfer_passes():
    inp = {"to": "tech-support", "kind": "transfer", "message": "internet down",
           "payload": {"customer_id": "cust-1042", "line_id": "L-1042-2", "symptom": "no connectivity"}}
    s = _scores(CONTEXT.evaluate(_ctx(inp, _v5(HANDOFF_MD))))
    assert s["handoff-context-complete"][0] is True and s["handoff-payload-clean"][0] is True


def test_f2_slim_context_names_line_id():
    inp = {"to": "tech-support", "kind": "transfer", "payload": {"customer_id": "cust-1042", "symptom": "down"}}
    value, comment = _scores(CONTEXT.evaluate(_ctx(inp, HANDOFF_MD)))["handoff-context-complete"]
    assert value is False and comment.endswith("missing: line_id")


def test_delegation_question_counts_as_issue_summary():
    md = {**HANDOFF_MD, "handoff_kind": "delegate", "handoff_to": "billing",
          "required_fields": "customer_id,line_id,issue_summary"}
    inp = {"kind": "delegate", "message": "Credit for 14 h outage?", "payload": {"customer_id": "c", "line_id": "L"}}
    assert _scores(CONTEXT.evaluate(_ctx(inp, md)))["handoff-context-complete"][0] is True


def test_card_number_in_payload_is_flagged_by_last_four_only():
    inp = {"kind": "transfer", "payload": {"customer_id": "c", "line_id": "L", "symptom": "x",
                                           "note": "card 4111 1111 1111 1111"}}
    value, comment = _scores(CONTEXT.evaluate(_ctx(inp, HANDOFF_MD)))["handoff-payload-clean"]
    assert value is False and "1111" in comment and "4111 1111" not in comment


def test_continuation_has_no_field_check():
    s = _scores(CONTEXT.evaluate(_ctx({"kind": "continue", "payload": {"customer_id": "c"}},
                                      {**HANDOFF_MD, "handoff_kind": "continue"})))
    assert set(s) == {"handoff-payload-clean"}


def test_normal_turn_is_not_a_loop():
    s = _scores(LOOP.evaluate(_ctx(metadata=_v5({"agent_path": "concierge>tech-support>billing",
                                                 "handoff_count": "2", "hop_cap_hit": "false"}))))
    assert s["handoff-loop"][0] is False and s["handoff-count"][0] == 2


def test_f3_ping_pong_is_a_loop():
    value, comment = _scores(LOOP.evaluate(_ctx(metadata={
        "agent_path": "concierge>billing>tech-support>billing>tech-support", "handoff_count": "4"})))["handoff-loop"]
    assert value is True and "ping-pong" in comment


def test_hop_cap_is_a_loop():
    assert _scores(LOOP.evaluate(_ctx(metadata={"agent_path": "concierge>billing", "handoff_count": "1",
                                                "hop_cap_hit": "true"})))["handoff-loop"][0] is True


def test_f5_failed_handoff_is_scored():
    inp = {"kind": "delegate", "message": "balance?", "payload": {"customer_id": "c", "line_id": "L"}}
    md = {**HANDOFF_MD, "handoff_kind": "delegate", "handoff_to": "billing",
          "required_fields": "customer_id,line_id,issue_summary"}
    failed = _scores(CONTEXT.evaluate(_ctx(inp, md, {"state": "failed", "reply": "billing unavailable: timeout"})))
    ok = _scores(CONTEXT.evaluate(_ctx(inp, md, json.dumps({"state": "completed", "reply": "141.38"}))))
    assert failed["handoff-succeeded"] == (False, "billing unavailable: timeout") and ok["handoff-succeeded"][0] is True

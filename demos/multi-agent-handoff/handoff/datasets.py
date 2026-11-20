"""
The evaluation datasets (SPEC §7), built from the synthetic back office so the expected
values cannot drift from the data: credit amounts come from the policy oracle, invoice
totals from customers.json, required handoff fields from the agent registry.

`handoff-routing`: 40 single-turn requests. Each item says which specialist should get
the first handoff, the ideal agent path, what the first handoff must carry, and what the
back office should look like afterwards (`outcome`). `reply_mentions` lists facts the
reply has to state.
"""

from __future__ import annotations

import json
from functools import lru_cache

from handoff import config
from handoff.agents.registry import AGENTS
from handoff.tools import crm

ROUTING = "handoff-routing"

C, T, B = "concierge", "tech-support", "billing"


@lru_cache(maxsize=1)
def _customers() -> dict:
    return {c["customer_id"]: c for c in json.loads((config.DATA_DIR / "customers.json").read_text())}


def _invoice(customer_id: str, month: str) -> dict:
    return next(i for i in _customers()[customer_id]["invoices"] if i["month"] == month)


def _total(customer_id: str, month: str) -> str:
    return f"{_invoice(customer_id, month)['total']:.2f}"


def _credit(line_id: str, outage_id: str) -> tuple[str, list[str], dict]:
    r = crm.compute_credit(line_id, outage_id)
    assert r["eligible"], (line_id, outage_id, r)
    return f"credit:{line_id}:{r['amount']}", [f"{r['amount']:.2f}"], {
        "line_id": line_id, "outage_id": outage_id, "amount": r["amount"]}


# (id, category, customer, message, ideal path, outcome, reply_mentions)
def _specs() -> list[tuple]:
    hero = _credit("L-1042-2", "OUT-2026-1117-01")
    return [
        # --- answered by the concierge: no handoff -------------------------------------------
        ("faq-01", "faq", "cust-1010", "What time do your stores open on Sunday?", [C], "faq_answered", []),
        ("faq-02", "faq", "cust-1031", "How can I pay my bill online?", [C], "faq_answered", []),
        ("faq-03", "faq", "cust-1039", "Is there a discount if I turn on autopay?", [C], "faq_answered", []),
        ("faq-04", "faq", "cust-1006", "I'm going to Mexico next month. Will my phone work there, and what does it cost?",
         [C], "faq_answered", []),
        ("faq-05", "faq", "cust-1027", "How do I return a phone I bought last week?", [C], "faq_answered", []),
        ("faq-06", "faq", "cust-1015", "Just curious: how do outage credits work in general?", [C], "faq_answered", []),
        ("faq-08", "faq", "cust-1009", "What are your support hours?", [C], "faq_answered", []),
        ("faq-09", "faq", "cust-1040", "Which plans do I have on my account?", [C], "faq_answered", ["Fiber 300"]),
        # --- billing questions -----------------------------------------------------------------
        ("bill-01", "billing", "cust-1011", "How much is my bill this month?", [C, B], "info_answered",
         [_total("cust-1011", "2026-11")]),
        ("bill-02", "billing", "cust-1032", "Has my October bill been paid?", [C, B], "info_answered", ["paid"]),
        ("bill-03", "billing", "cust-1023", "What was my total for September?", [C, B], "info_answered",
         [_total("cust-1023", "2026-09")]),
        ("bill-04", "billing", "cust-1034", "Can you tell me what I owe right now?", [C, B], "info_answered",
         [_total("cust-1034", "2026-11")]),
        ("bill-05", "billing", "cust-1014", "I think my November bill is wrong. What's on it?", [C, B], "info_answered",
         [_total("cust-1014", "2026-11")]),
        ("bill-06", "billing", "cust-1021", "How much is my current invoice?", [C, B], "info_answered",
         [_total("cust-1021", "2026-11")]),
        # was faq-07: billing's agent card says it owns plan changes, so routing there is right
        ("bill-07", "billing", "cust-1030", "How do I change to a different plan?", [C, B], "info_answered", []),
        # --- technical, no outage ------------------------------------------------------------
        ("tech-01", "tech", "cust-1020", "Calls work but I have no mobile data since I got my new phone.", [C, T],
         "resolved", ["APN"]),
        ("tech-02", "tech", "cust-1022", "My phone shows full bars but nothing loads on mobile data.", [C, T],
         "resolved", ["APN"]),
        ("tech-03", "tech", "cust-1024", "Mobile data stopped working after I switched phones.", [C, T],
         "resolved", ["APN"]),
        ("tech-04", "tech", "cust-1035", "I can call and text but there's no mobile internet.", [C, T],
         "resolved", ["APN"]),
        ("tech-05", "tech", "cust-1018", "My new SIM card says no service.", [C, T], "resolved", []),
        ("tech-06", "tech", "cust-1008", "My home internet has been really slow all week.", [C, T], "resolved", []),
        ("tech-07", "tech", "cust-1016",
         "My fiber router has a red light and nothing works. I already restarted it twice. Please send a technician.",
         [C, T], "technician_booked:L-1016-2", []),
        ("tech-08", "tech", "cust-1011",
         "My home internet router shows offline. I've rebooted it already. Can someone come and fix it?",
         [C, T], "technician_booked:L-1011-2", []),
        ("tech-09", "tech", "cust-1007", "My phone says SIM not provisioned.", [C, T], "resolved", []),
        # --- confirmed outage → credit: the peer hop tech-support → billing ------------------
        ("credit-01", "outage-credit", "cust-1042",
         "My home internet has been down since last night. I want money back for it.", [C, T, B], *hero[:2]),
        ("credit-02", "outage-credit", "cust-1001", "Internet was out all night. Do I get a credit for that?",
         [C, T, B], *_credit("L-1001-2", "OUT-2026-1117-01")[:2]),
        ("credit-03", "outage-credit", "cust-1025",
         "We had no internet from yesterday evening until this morning. Can you credit me?",
         [C, T, B], *_credit("L-1025-2", "OUT-2026-1117-01")[:2]),
        ("credit-04", "outage-credit", "cust-1041", "About last night's internet outage: I'd like the credit I'm owed.",
         [C, T, B], *_credit("L-1041-2", "OUT-2026-1117-01")[:2]),
        ("credit-05", "outage-credit", "cust-1005", "My home internet was out for days last week. I want a refund.",
         [C, T, B], *_credit("L-1005-2", "OUT-2026-1112-04")[:2]),
        ("credit-06", "outage-credit", "cust-1029",
         "Our internet was down for most of last week. Please credit my account.",
         [C, T, B], *_credit("L-1029-2", "OUT-2026-1112-04")[:2]),
        ("credit-07", "outage-credit", "cust-1013",
         "My internet was out for days last week and my bill shows no credit for it. Please fix that.",
         [C, T, B], *_credit("L-1013-2", "OUT-2026-1112-04")[:2]),
        ("credit-08", "outage-credit", "cust-1037",
         "Our home internet went down for a couple of days last week. Is there a credit for that?",
         [C, T, B], *_credit("L-1037-2", "OUT-2026-1112-04")[:2]),
        # --- credit asked for, policy says no. Tech-support knows the 4-hour rule, so billing is not
        #     needed (the first runs showed the ideal path had billing in it by mistake) -----------
        ("nocredit-01", "no-credit", "cust-1007",
         "My internet dropped for a couple of hours on Sunday afternoon. I want a credit.", [C, T], "no_credit", []),
        ("nocredit-02", "no-credit", "cust-1010",
         "Internet went out for a bit on Sunday afternoon. Can I get money back?", [C, T], "no_credit", []),
        ("nocredit-03", "no-credit", "cust-1026",
         "We lost internet for a couple of hours on Sunday. Credit please.", [C, T], "no_credit", []),
        ("nocredit-04", "no-credit", "cust-1031", "My internet was down yesterday. I want a credit.", [C, T],
         "no_credit", []),
        ("nocredit-05", "no-credit", "cust-1004", "My home internet was out this morning. I'd like a credit.", [C, T],
         "no_credit", []),
        # --- ongoing area outage: no technician during an outage ------------------------------
        ("outage-01", "ongoing-outage", "cust-1012", "My phone has had no signal since early this morning.", [C, T],
         "outage_confirmed", []),
        ("outage-02", "ongoing-outage", "cust-1036", "No mobile service at all today. Can you send someone?", [C, T],
         "outage_confirmed", []),
        ("outage-03", "ongoing-outage", "cust-1028", "My cell service has been dead since this morning.", [C, T],
         "outage_confirmed", []),
    ]


def routing_items() -> list[dict]:
    items = []
    for item_id, category, customer, message, path, outcome, mentions in _specs():
        first = path[1] if len(path) > 1 else None
        items.append({
            "id": f"routing-{item_id}",
            "input": {"message": message, "customer_id": customer},
            "expected_output": {"path": path, "first_handoff": first,
                                "required_fields": AGENTS[first].required_fields if first else [],
                                "outcome": outcome, "reply_mentions": mentions},
            "metadata": {"category": category, "item": item_id},
        })
    return items


# --- handoff-conversations: multi-turn goals for a simulated customer -----------------------

CONVERSATIONS = "handoff-conversations"


def _conv_specs() -> list[tuple]:
    # (id, customer, opening message, persona, goal, what the customer knows, first handoff, outcome, max turns)
    return [
        ("conv-01", "cust-1042", "My home internet has been down since last night. I want money back for it.",
         "Direct, a bit annoyed, short messages.",
         "Get a credit for last night's outage and know when it shows up.",
         "The internet is back now. You restarted the router this morning. \"It shows on your next bill\" is "
         "an acceptable answer to when the credit appears.", T,
         _credit("L-1042-2", "OUT-2026-1117-01")[0], 6),
        ("conv-02", "cust-1016", "My fiber router has a red light and nothing works.",
         "Patient but busy; answers questions accurately.",
         "Get a technician visit booked if the router can't be fixed remotely.",
         "You already restarted the router twice. The light is still red. Any day this week works.", T,
         "technician_booked:L-1016-2", 6),
        ("conv-03", "cust-1011", "My home internet router shows offline.",
         "Polite, not technical; does what the agent asks.",
         "Get the home internet working again.",
         "If asked to restart the router, you do it and it is STILL offline afterwards. You are happy with a "
         "technician visit.", T, "technician_booked:L-1011-2", 7),
        ("conv-04", "cust-1020", "Calls work but I have no mobile data since I got my new phone.",
         "Friendly, follows instructions.",
         "Get mobile data working.",
         "If the agent gives you APN settings to check, you apply them and mobile data then works.", T,
         "resolved", 6),
        ("conv-05", "cust-1007", "My internet dropped for a couple of hours on Sunday afternoon. I want a credit.",
         "Insistent at first, accepts a clear explanation.",
         "Get a credit, or understand clearly why there isn't one.",
         "It was out from about 1 pm to 3:30 pm on Sunday.", T, "no_credit", 5),
        ("conv-06", "cust-1013",
         "My internet was out for days last week and my bill shows no credit for it. What's going on?",
         "Frustrated, wants it fixed in one go.",
         "Get the credit you are owed for the multi-day outage.",
         "You want whatever credit you are owed applied.", T, _credit("L-1013-2", "OUT-2026-1112-04")[0], 6),
        ("conv-07", "cust-1001", "How much is my bill this month?",
         "Chatty; brings up a second issue after the first is answered.",
         "First learn your bill total; then get a credit for last night's internet outage.",
         "After the bill question is answered, say your internet was out all last night and ask for a credit.",
         B, _credit("L-1001-2", "OUT-2026-1117-01")[0], 7),
        ("conv-08", "cust-1036", "No mobile service at all today. Can you send someone?",
         "Worried, needs the phone for work.",
         "Get mobile service back, or know when it will be back.",
         "You will ask for a credit once you hear it is an outage.", T, "outage_confirmed", 6),
        ("conv-09", "cust-1008", "My home internet has been really slow all week.",
         "Curious, wants to understand why.",
         "Understand why the internet is slow and what to do about it.",
         "Speed tests show about 2 Mbps. You have not changed anything.", T, "resolved", 6),
        ("conv-10", "cust-1022", "What time do your stores open on Sunday?",
         "Easy-going; asks a second, technical question after the first.",
         "Learn the Sunday store hours, then get mobile data working.",
         "After the hours answer, say mobile data stopped working when you switched phones. If given APN "
         "settings, you apply them and data works.", T, "resolved", 7),
        ("conv-11", "cust-1030", "I want to switch my phone to a cheaper plan.",
         "Price-conscious.",
         "Learn what cheaper plan you could switch to and how.",
         "You are on Mobile Unlimited at $65 and want to pay less.", B, "info_answered", 5),
        ("conv-12", "cust-1025",
         "We had no internet from yesterday evening until this morning. Can you credit me?",
         "Forgetful; asks for the same thing twice.",
         "Get the outage credit, exactly once.",
         "After the credit is confirmed, ask again a little later whether you can get the credit for the "
         "outage (you forgot it was already done).", T, _credit("L-1025-2", "OUT-2026-1117-01")[0], 6),
    ]


def conversation_items() -> list[dict]:
    items = []
    for cid, customer, opening, persona, goal, facts, first, outcome, max_turns in _conv_specs():
        items.append({
            "id": f"conversation-{cid}",
            "input": {"customer_id": customer, "opening": opening, "persona": persona, "goal": goal,
                      "facts": facts},
            "expected_output": {"first_handoff": first, "outcome": outcome, "max_turns": max_turns,
                                "required_fields": AGENTS[first].required_fields if first else [],
                                "reply_mentions": [outcome.split(":")[2]] if outcome.startswith("credit:") else []},
            "metadata": {"item": cid, "kind": "conversation"},
        })
    return items

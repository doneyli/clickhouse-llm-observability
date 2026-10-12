"""Lesson 04 solution: observed signals, not general quality claims."""
def record(client, *, trace_id, observation_id, check, history, message):
    if not trace_id or not observation_id:
        return
    def score(name, value, comment):
        client.create_score(trace_id=trace_id, observation_id=observation_id,
                            name=name, value=int(value), data_type="BOOLEAN", comment=comment)
    score("guardrail-blocked", check["blocked"], "Runtime rules blocked this request.")
    score("pii-in-input", bool(check["pii_shared"]), "Regex detection; names/addresses may be missed.")
    letters = [c for c in message if c.isalpha()]
    all_caps = len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > .7
    score("all-caps-signal", all_caps, "Review signal only; capitals do not prove frustration.")

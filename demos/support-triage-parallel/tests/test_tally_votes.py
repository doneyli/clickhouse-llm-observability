"""Pure-code regression test for sql_voting.tally_votes's handling of the
"EXPLAIN-valid but zero usable signatures" degenerate case.

Live testing (real Anthropic + real sql-clickhouse.clickhouse.com execution)
found that a full-table aggregation candidate can pass EXPLAIN (fast, planning
only) but then time out during the actual execution used to compute its
result-signature. When this happens to EVERY candidate, ``compute_tally``
correctly reports ``empty=True`` (no votes were cast) — but ``tally_votes`` was
not checking that flag, so it fell into the "pick the winner by signature"
branch. Since every candidate's ``signature`` is ``None`` in that case, and
``winner_sig = tally["winner"]`` is also ``None``, ``next(c for c in valid if
c["signature"] == winner_sig)`` matched the FIRST candidate purely because
``None == None`` — fabricating a "winner" — and ``tally["votes"].get(None, 1)``
defaulted to ``1``, fabricating a nonzero ``consensus_confidence`` for a run
where zero candidates actually agreed on anything.

No network/LLM calls: ``ch_validator.execute_readonly`` is monkeypatched to
always fail (simulating the live timeout), matching how EXPLAIN can pass while
execution fails.
"""

import ch_validator
import sql_voting


def _explain_valid_candidate(sql: str, index: int) -> dict:
    # Mirrors sample_candidate()'s return shape after explain_ok() has set
    # valid=True; signature/result_signature are filled in by
    # _assign_signatures() inside tally_votes().
    return {"sql": sql, "sample_index": index, "valid": True, "signature": None}


def test_all_signatures_failing_reports_zero_confidence_not_fabricated(monkeypatch):
    monkeypatch.setattr(ch_validator, "execute_readonly", lambda sql: None)

    candidates = [_explain_valid_candidate("SELECT 1", i) for i in range(5)]
    result = sql_voting.tally_votes("irrelevant question", candidates,
                                     strategy="result-signature")

    assert result["consensus_confidence"] == 0.0
    assert result["winner"] is None
    assert result["winning_sql"] is None
    assert result["result_signature"] is None
    assert result["tally"] == {}
    assert result["tie_break_used"] is False
    # All 5 were EXPLAIN-valid; none produced a signature. They must NOT be
    # counted as "invalid" SQL (that would misreport sql_validity_rate's
    # partner metric) — they are correctly-planned candidates whose execution
    # failed, a distinct failure mode from a bad EXPLAIN.
    assert result["invalid"] == 0


def test_one_real_signature_among_failures_still_wins():
    # Sanity check the fix doesn't over-trigger: if even one candidate DID
    # produce a signature, that's a real (if lonely) vote and must still win —
    # this is not the degenerate "empty" case.
    candidates = [
        {"sql": "SELECT 1", "sample_index": 0, "valid": True, "signature": None},
        {"sql": "SELECT 2", "sample_index": 1, "valid": True, "signature": None},
    ]

    def fake_execute(sql):
        return [(1,)] if sql.strip().rstrip(";") == "SELECT 1" else None

    import ch_validator as chv
    orig = chv.execute_readonly
    chv.execute_readonly = fake_execute
    try:
        result = sql_voting.tally_votes("irrelevant question", candidates,
                                         strategy="result-signature")
    finally:
        chv.execute_readonly = orig

    assert result["winner"] is not None
    assert result["winning_sql"] == "SELECT 1"
    # 1 vote out of 2 EXPLAIN-valid candidates (the other's signature is None,
    # i.e. its execution failed but it still counts against the denominator).
    assert result["consensus_confidence"] == 0.5

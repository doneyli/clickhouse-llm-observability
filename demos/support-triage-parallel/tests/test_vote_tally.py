"""Pure-code tests for the vote tally math (sql_voting.compute_tally).

No LLM, no ClickHouse — candidates are hand-annotated with ``valid`` +
``signature`` so we test only the aggregation logic: clear majority, a 2-2-1
tie, all-invalid, N-1 degradation, and margin arithmetic. This doubles as the CI
target for the *aggregation-logic-bug* failure mode.
"""

from sql_voting import compute_tally


def _cand(sig, valid=True, i=0):
    return {"sql": f"SELECT {i}", "sample_index": i, "valid": valid, "signature": sig}


def test_clear_majority_5_0():
    cands = [_cand("sig-a", i=i) for i in range(5)]
    t = compute_tally(cands)
    assert t["winner"] == "sig-a"
    assert t["tie"] is False
    assert t["votes"] == {"sig-a": 5}
    assert t["margin"] == 5
    assert t["invalid"] == 0
    assert t["valid_count"] == 5


def test_majority_with_one_invalid():
    # 3x sig-a, 1x sig-b, 1 invalid -> winner sig-a, margin 2
    cands = [_cand("sig-a", i=0), _cand("sig-a", i=1), _cand("sig-a", i=2),
             _cand("sig-b", i=3), _cand(None, valid=False, i=4)]
    t = compute_tally(cands)
    assert t["winner"] == "sig-a"
    assert t["margin"] == 2
    assert t["invalid"] == 1
    assert t["valid_count"] == 4
    assert t["tie"] is False


def test_two_two_one_tie_fires_tiebreak():
    # 2x sig-a, 2x sig-b, 1x sig-c -> tie at the top, no winner
    cands = [_cand("sig-a", i=0), _cand("sig-a", i=1),
             _cand("sig-b", i=2), _cand("sig-b", i=3),
             _cand("sig-c", i=4)]
    t = compute_tally(cands)
    assert t["tie"] is True
    assert t["winner"] is None
    assert t["margin"] == 0
    assert t["valid_count"] == 5


def test_all_invalid_is_empty_and_graceful():
    cands = [_cand(None, valid=False, i=i) for i in range(5)]
    t = compute_tally(cands)
    assert t["empty"] is True
    assert t["winner"] is None
    assert t["votes"] == {}
    assert t["invalid"] == 5
    assert t["valid_count"] == 0


def test_n_minus_1_degradation_still_tallies():
    # One candidate dropped (invalid); the remaining 4 still produce a winner.
    cands = [_cand("sig-a", i=0), _cand("sig-a", i=1), _cand("sig-a", i=2),
             _cand("sig-b", i=3), _cand(None, valid=False, i=4)]
    t = compute_tally(cands)
    assert t["valid_count"] == 4
    assert t["winner"] == "sig-a"
    assert t["top"] == 3


def test_margin_is_top_minus_second():
    # 4x sig-a, 1x sig-b -> margin 3
    cands = [_cand("sig-a", i=i) for i in range(4)] + [_cand("sig-b", i=4)]
    t = compute_tally(cands)
    assert t["top"] == 4
    assert t["margin"] == 3


def test_single_valid_candidate():
    cands = [_cand("sig-a", i=0), _cand(None, valid=False, i=1),
             _cand(None, valid=False, i=2)]
    t = compute_tally(cands)
    assert t["winner"] == "sig-a"
    assert t["valid_count"] == 1
    assert t["margin"] == 1

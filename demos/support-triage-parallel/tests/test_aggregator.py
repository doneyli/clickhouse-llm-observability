"""Pure-code tests for the sectioning aggregator input builder
(triage_pipeline.build_synthesis_input).

Verifies graceful degradation: a missing / failed branch becomes
``"insufficient data"`` in the labeled outputs, the failed count is right, and
the ``degraded`` flag is set — the *aggregation must tolerate N-1 branches*
guarantee. No LLM involved.
"""

from triage_pipeline import build_synthesis_input


def _ok(branch, key, output):
    return {"branch": branch, "key": key, "ok": True, "output": output}


def _failed(branch, key):
    return {"branch": branch, "key": key, "ok": False, "output": None}


def _all_ok():
    return [
        _ok("branch-summary", "summary", "A two-sentence summary."),
        _ok("branch-sentiment-urgency", "sentiment", '{"sentiment":"negative","urgency":"high"}'),
        _ok("branch-category", "category", "query-performance"),
        _ok("branch-policy-guard", "policy", '{"flagged":false,"reasons":[]}'),
    ]


def test_all_branches_present_not_degraded():
    outputs, failed, degraded = build_synthesis_input(_all_ok())
    assert failed == 0
    assert degraded is False
    assert set(outputs) == {"summary", "sentiment", "category", "policy"}
    assert "insufficient data" not in outputs.values()


def test_one_branch_failed_is_degraded():
    results = _all_ok()
    results[1] = _failed("branch-sentiment-urgency", "sentiment")  # slow-branch fault
    outputs, failed, degraded = build_synthesis_input(results)
    assert failed == 1
    assert degraded is True
    assert outputs["sentiment"] == "insufficient data"
    assert outputs["summary"] != "insufficient data"


def test_none_output_treated_as_failed():
    results = _all_ok()
    results[2] = _ok("branch-category", "category", None)  # ok flag but empty output
    outputs, failed, degraded = build_synthesis_input(results)
    assert outputs["category"] == "insufficient data"
    assert failed == 1
    assert degraded is True


def test_multiple_failures_counted():
    results = [
        _failed("branch-summary", "summary"),
        _failed("branch-sentiment-urgency", "sentiment"),
        _ok("branch-category", "category", "billing"),
        _ok("branch-policy-guard", "policy", '{"flagged":true,"reasons":["email"]}'),
    ]
    outputs, failed, degraded = build_synthesis_input(results)
    assert failed == 2
    assert degraded is True
    assert outputs["summary"] == "insufficient data"
    assert outputs["sentiment"] == "insufficient data"
    assert outputs["category"] == "billing"


def test_key_derived_from_branch_name_when_missing():
    # A result missing an explicit 'key' should derive it from the branch name.
    results = [{"branch": "branch-summary", "ok": True, "output": "hi"}]
    outputs, failed, degraded = build_synthesis_input(results)
    assert "summary" in outputs
    assert outputs["summary"] == "hi"

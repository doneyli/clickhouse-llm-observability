"""Unit tests for the two gate functions (gates.py) — pure, no services."""

import conftest  # noqa: F401  (sets sys.path; no heavy deps needed for gates)

from gates import (
    GateResult, gate_database_selection, gate_response_quality, CATALOG_DATABASES,
)


class FakeGrader:
    """Grader stand-in: returns `value` and records whether it was invoked."""

    def __init__(self, value):
        self.value = value
        self.calls = []

    def invoke(self, inputs):
        self.calls.append(inputs)
        return self.value


# --------------- Gate 1: deterministic catalog membership ---------------

def test_gate1_pass_names_database():
    r = gate_database_selection("We should use the uk property dataset here.")
    assert r.passed is True
    assert r.details["databases"] == ["uk"]


def test_gate1_fail_no_database():
    r = gate_database_selection("I'll analyze general trends without a specific source.")
    assert r.passed is False
    assert r.details["databases"] == []
    assert "no database" in r.reason.lower()


def test_gate1_word_boundary_git_vs_github():
    # 'git' and 'github' are distinct catalog entries; both should match here.
    r = gate_database_selection("Check github events and the git history.")
    assert r.passed is True
    assert r.details["databases"] == ["git", "github"]


def test_gate1_no_partial_match():
    # 'githubbing' must NOT match 'github' (word-boundary), 'geographic' not 'geo'.
    r = gate_database_selection("Some githubbing and geographic musings.")
    assert r.passed is False


def test_gate1_handles_empty_analysis():
    assert gate_database_selection("").passed is False


def test_catalog_has_24_databases():
    assert len(CATALOG_DATABASES) == 24


# --------------- Gate 2a: deterministic SQL policy (fail-closed) ---------------

def test_gate2_destructive_fails_closed_without_calling_grader():
    grader = FakeGrader('{"verdict": "pass", "reason": "would-pass"}')
    r = gate_response_quality("q", "a", "ctx", "Example: DROP TABLE uk;", grader)
    assert r.passed is False
    assert r.details["check"] == "sql-policy"
    assert "DROP" in r.reason
    assert grader.calls == []  # short-circuited: never paid for the LLM grade


def test_gate2_reports_multiple_destructive_keywords():
    grader = FakeGrader('{"verdict": "pass", "reason": "x"}')
    r = gate_response_quality("q", "a", "c", "DELETE FROM x; UPDATE y SET z=1;", grader)
    assert r.passed is False
    assert "DELETE" in r.reason and "UPDATE" in r.reason


# --------------- Gate 2b: LLM-graded grounding ---------------

def test_gate2_grounding_pass():
    grader = FakeGrader('{"verdict": "pass", "reason": "grounded"}')
    r = gate_response_quality("q", "a", "c", "SELECT city FROM uk LIMIT 10", grader)
    assert r.passed is True
    assert r.details["check"] == "llm-grounding"
    assert len(grader.calls) == 1


def test_gate2_grounding_fail():
    grader = FakeGrader('{"verdict": "fail", "reason": "fabricated numbers"}')
    r = gate_response_quality("q", "a", "c", "The answer is 42.", grader)
    assert r.passed is False
    assert r.reason == "fabricated numbers"


def test_gate2_grounding_parses_fenced_json():
    grader = FakeGrader('```json\n{"verdict": "pass", "reason": "ok"}\n```')
    r = gate_response_quality("q", "a", "c", "SELECT 1 LIMIT 1", grader)
    assert r.passed is True


def test_gate2_grounding_fails_open_on_unparseable():
    grader = FakeGrader("Sure! I think the answer looks fine to me.")
    r = gate_response_quality("q", "a", "c", "SELECT 1 LIMIT 1", grader)
    assert r.passed is True  # fail OPEN — grader flakiness never blocks the app
    assert r.details["verdict_source"] == "parse-error"


def test_gate2_grounding_fails_open_on_non_string():
    grader = FakeGrader(12345)  # .strip() -> AttributeError
    r = gate_response_quality("q", "a", "c", "SELECT 1 LIMIT 1", grader)
    assert r.passed is True
    assert r.details["verdict_source"] == "parse-error"


# --------------- GateResult.as_output shape (read by chain-gate-check.ts) ------

def test_as_output_pass_shape():
    out = GateResult(True, "reason here", {"databases": ["uk"]}).as_output()
    assert out == {"verdict": "pass", "reason": "reason here", "databases": ["uk"]}


def test_as_output_fail_shape():
    out = GateResult(False, "bad", {"check": "sql-policy"}).as_output()
    assert out["verdict"] == "fail"
    assert out["check"] == "sql-policy"


if __name__ == "__main__":
    import sys
    sys.exit(conftest.run_tests(dict(globals())))

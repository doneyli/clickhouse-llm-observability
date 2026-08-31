"""Routing tests for the gated query(): retry, abort (Gate 1), escalate (Gate 2).

Builds the pipeline via object.__new__ and injects fake chains, so no LLM / MCP /
Langfuse is touched. Langfuse is disabled (conftest), so gate spans / trace tags
no-op; we capture tag_current_trace by patching the module symbol.
"""

import os

import conftest  # noqa: F401  (env + langchain stubs BEFORE importing sql_pipeline)
from conftest import FakeChain

import sql_pipeline
from sql_pipeline import ClickHouseSQLPipeline, GATE_MAX_ATTEMPTS

PASS_JSON = '{"verdict": "pass", "reason": "grounded"}'
CLEAN_SQL = "Here is a query: SELECT city, avg(price) FROM uk GROUP BY city LIMIT 10"


def make_pipeline(analysis_outputs, response_outputs=None, grader_outputs=None,
                  context="Available ClickHouse databases: uk, nyc_taxi"):
    p = object.__new__(ClickHouseSQLPipeline)
    p.analysis_chain = FakeChain(analysis_outputs)
    p.response_chain = FakeChain(response_outputs if response_outputs is not None else [CLEAN_SQL])
    p.gate_grounding_chain = FakeChain(grader_outputs if grader_outputs is not None else [PASS_JSON])
    p.retrieve_calls = []

    def _retrieve(q, a):
        p.retrieve_calls.append((q, a))
        return context

    p.retrieve_context = _retrieve
    return p


def run_query(pipeline, question="What are property prices in the UK?"):
    """Run query() while capturing tag_current_trace(...) calls."""
    tags = []
    orig = sql_pipeline.tag_current_trace
    sql_pipeline.tag_current_trace = lambda t: tags.append(list(t))
    try:
        result = pipeline.query(question)
    finally:
        sql_pipeline.tag_current_trace = orig
    return result, tags


# --------------- Happy path ---------------

def test_happy_path_single_attempt():
    p = make_pipeline(["Use the uk dataset."])
    result, tags = run_query(p)
    assert result == CLEAN_SQL
    assert len(p.analysis_chain.calls) == 1
    assert len(p.response_chain.calls) == 1
    assert len(p.retrieve_calls) == 1
    assert tags == []  # no abort / escalate
    verdicts = [(g["gate"], g["verdict"]) for g in p.gate_log]
    assert verdicts == [("gate-database-selection", "pass"),
                        ("gate-response-quality", "pass")]


# --------------- Gate 1: retry then pass ---------------

def test_gate1_retry_then_pass():
    p = make_pipeline(["General approach, no specific source.", "Use the uk dataset."])
    result, tags = run_query(p)
    assert result == CLEAN_SQL
    assert len(p.analysis_chain.calls) == 2
    # retry input carries the attempt marker + the failure reason fed back in
    retry_input = p.analysis_chain.calls[1]["inputs"]["question"]
    assert "[Retry 2:" in retry_input
    assert "no database" in retry_input.lower()
    # attempt metadata rides in config.metadata, not the (stable) span name
    assert p.analysis_chain.calls[1]["config"]["metadata"]["attempt"] == 2
    assert p.analysis_chain.calls[1]["config"]["metadata"]["gate_failure_reason"]
    assert tags == []


# --------------- Gate 1: exhausted -> ABORT ---------------

def test_gate1_abort_after_exhausting_retries():
    p = make_pipeline(["No source at all.", "Still nothing concrete."])
    result, tags = run_query(p)
    assert result.startswith("I couldn't ground this question")
    assert len(p.analysis_chain.calls) == GATE_MAX_ATTEMPTS
    # ABORT: never paid for MCP retrieve or the response step
    assert p.retrieve_calls == []
    assert p.response_chain.calls == []
    assert ["gate:aborted"] in tags
    assert all(g["verdict"] == "fail" for g in p.gate_log)


# --------------- Gate 2: destructive SQL, retry then pass ---------------

def test_gate2_retry_then_pass_after_destructive():
    p = make_pipeline(
        ["Use the uk dataset."],
        response_outputs=["Cleanup: DELETE FROM uk WHERE old;", CLEAN_SQL],
    )
    result, tags = run_query(p)
    assert result == CLEAN_SQL
    assert len(p.response_chain.calls) == 2
    retry_ctx = p.response_chain.calls[1]["inputs"]["context"]
    assert "[Retry 2:" in retry_ctx
    assert p.response_chain.calls[1]["config"]["metadata"]["attempt"] == 2
    assert tags == []


# --------------- Gate 2: exhausted -> ESCALATE ---------------

def test_gate2_escalate_after_exhausting_retries():
    p = make_pipeline(
        ["Use the uk dataset."],
        response_outputs=["DELETE FROM uk;", "DROP TABLE uk;"],
    )
    result, tags = run_query(p)
    assert result.startswith("[Unverified — routed for review:")
    assert "DROP TABLE uk;" in result  # the flagged answer is still returned
    assert len(p.response_chain.calls) == GATE_MAX_ATTEMPTS
    assert ["gate:escalated"] in tags
    g2 = [g for g in p.gate_log if g["gate"] == "gate-response-quality"]
    assert len(g2) == 2 and all(g["verdict"] == "fail" for g in g2)


# --------------- Gate 2: grounding fail then pass (LLM branch) ---------------

def test_gate2_grounding_retry_then_pass():
    p = make_pipeline(
        ["Use the uk dataset."],
        response_outputs=[CLEAN_SQL, CLEAN_SQL],
        grader_outputs=['{"verdict": "fail", "reason": "ungrounded"}', PASS_JSON],
    )
    result, tags = run_query(p)
    assert result == CLEAN_SQL
    assert len(p.response_chain.calls) == 2
    assert tags == []


# --------------- Fault injection helper ---------------

def test_apply_fault_vague_analysis():
    os.environ["DEMO_FAULT"] = "vague-analysis"
    try:
        p = object.__new__(ClickHouseSQLPipeline)
        out = p._apply_fault("Question", step="analysis")
        assert "do NOT name any specific database" in out
        assert p._apply_fault("Question", step="response") == "Question"
    finally:
        os.environ.pop("DEMO_FAULT", None)


def test_apply_fault_destructive_sql():
    os.environ["DEMO_FAULT"] = "destructive-sql"
    try:
        p = object.__new__(ClickHouseSQLPipeline)
        out = p._apply_fault("Question", step="response")
        assert "DELETE" in out
        assert p._apply_fault("Question", step="analysis") == "Question"
    finally:
        os.environ.pop("DEMO_FAULT", None)


def test_apply_fault_off_by_default():
    p = object.__new__(ClickHouseSQLPipeline)
    assert p._apply_fault("Question") == "Question"


if __name__ == "__main__":
    import sys
    sys.exit(conftest.run_tests(dict(globals())))

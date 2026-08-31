"""Plan schema round-trip + the retry-once-then-abort planner contract.

LLM-free: fake planner LLMs, pure pydantic. No langgraph needed.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph import (
    Plan, Task, PlanError, validate_plan, plan_with_retry,
    enforce_guards, should_replan,
)


# --------------------------- fakes ---------------------------
class _Structured:
    def __init__(self, obj=None, raises=False):
        self._obj = obj
        self._raises = raises

    def invoke(self, _text):
        if self._raises:
            raise ValueError("malformed structured output")
        return self._obj


class FakePlanner:
    def __init__(self, obj=None, raises=False):
        self._s = _Structured(obj, raises)

    def with_structured_output(self, _model):
        return self._s


# --------------------------- schema ---------------------------
def test_plan_round_trip():
    plan = Plan(tasks=[Task(analysis_type="slow_queries", focus="f", rationale="r")],
                reasoning="because")
    dumped = plan.model_dump()
    assert dumped["tasks"][0]["analysis_type"] == "slow_queries"
    assert Plan(**dumped).reasoning == "because"


def test_plan_requires_at_least_one_task():
    with pytest.raises(Exception):
        Plan(tasks=[], reasoning="x")


def test_plan_caps_at_eight_tasks():
    with pytest.raises(Exception):
        Plan(tasks=[Task(analysis_type="slow_queries") for _ in range(9)], reasoning="x")


def test_validate_plan_drops_unknown_analysis_types():
    plan = Plan(tasks=[Task(analysis_type="slow_queries"),
                       Task(analysis_type="not_a_real_analysis")], reasoning="x")
    valid = validate_plan(plan)
    assert [t.analysis_type for t in valid.tasks] == ["slow_queries"]


def test_validate_plan_raises_when_all_invalid():
    plan = Plan(tasks=[Task(analysis_type="bogus")], reasoning="x")
    with pytest.raises(PlanError):
        validate_plan(plan)


# --------------------------- retry-once-then-abort ---------------------------
def test_plan_with_retry_succeeds_first_try():
    good = Plan(tasks=[Task(analysis_type="parts_pressure")], reasoning="ok")
    out = plan_with_retry(FakePlanner(obj=good), "prompt")
    assert out.tasks[0].analysis_type == "parts_pressure"


def test_plan_with_retry_aborts_after_two_failures():
    with pytest.raises(PlanError):
        plan_with_retry(FakePlanner(raises=True), "prompt", attempts=2)


# --------------------------- guards ---------------------------
def test_enforce_guards_dedupes_within_wave_and_vs_prior():
    plan = Plan(tasks=[Task(analysis_type="slow_queries"),
                       Task(analysis_type="slow_queries"),   # dup within wave
                       Task(analysis_type="parts_pressure"),
                       Task(analysis_type="merge_backlog")], reasoning="x")
    kept = enforce_guards(plan, prior_types=["merge_backlog"], workers_spawned=1, max_workers=8)
    types = [t.analysis_type for t in kept.tasks]
    assert types == ["slow_queries", "parts_pressure"]  # dup + prior removed


def test_enforce_guards_caps_to_worker_budget():
    plan = Plan(tasks=[Task(analysis_type=k) for k in
                       ["slow_queries", "query_errors", "parts_pressure",
                        "merge_backlog", "insert_profile", "memory_pressure",
                        "disk_usage", "table_growth"]], reasoning="x")
    kept = enforce_guards(plan, prior_types=[], workers_spawned=6, max_workers=8)
    assert len(kept.tasks) == 2  # only 2 of the budget remain


def test_should_replan_respects_both_guards():
    # not sufficient, under both guards → replan
    assert should_replan(False, current_round=1, workers_spawned=2, max_rounds=2, max_workers=8)
    # sufficient → never replan
    assert not should_replan(True, current_round=1, workers_spawned=2, max_rounds=2, max_workers=8)
    # round guard hit
    assert not should_replan(False, current_round=2, workers_spawned=2, max_rounds=2, max_workers=8)
    # worker-budget guard hit
    assert not should_replan(False, current_round=1, workers_spawned=8, max_rounds=2, max_workers=8)

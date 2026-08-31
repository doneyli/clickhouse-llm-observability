"""Graph shape with mocked LLMs: a plan of N → exactly N worker invocations;
the re-plan guard stops the loop; fault:overplan reliably hits the worker cap.

Requires langgraph to compile the StateGraph (skipped if not installed). All
LLM + ClickHouse calls are faked, so this is LLM-free and DB-free.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("langgraph")

import langfuse_config as lf
lf.LANGFUSE_ENABLED = False  # force no-op instrumentation regardless of env

from graph import Investigator, Plan, Task, GateVerdict
from analysis_catalog import CATALOG


# --------------------------- fakes ---------------------------
class _Structured:
    def __init__(self, obj):
        self._obj = obj

    def invoke(self, _text):
        return self._obj


class FixedStructured:
    """Returns the same structured object every call (planner or gate)."""
    def __init__(self, obj):
        self._obj = obj

    def with_structured_output(self, _model):
        return _Structured(self._obj)

    def invoke(self, _text):
        return _Msg("unused")


class SeqPlanner:
    """Returns a different Plan on each successive planning round."""
    def __init__(self, plans):
        self._plans = plans
        self._i = 0

    def with_structured_output(self, _model):
        plan = self._plans[min(self._i, len(self._plans) - 1)]
        self._i += 1
        return _Structured(plan)


class _Msg:
    def __init__(self, content):
        self.content = content


class FakeText:
    def __init__(self, content="verdict: healthy [worker:x]"):
        self._c = content

    def invoke(self, _text):
        return _Msg(self._c)


def _catalog_keys(n):
    return list(CATALOG.keys())[:n]


def _fake_rows(_sql):
    return [{"metric": "Query", "value": "3"}]


def _investigator(planner, *, gate_sufficient=True, max_workers=8, max_rounds=2):
    return Investigator(
        planner_llm=planner,
        worker_llm=FakeText("finding: healthy, table system.parts looks fine"),
        gate_llm=FixedStructured(GateVerdict(sufficient=gate_sufficient, missing_analyses=[])),
        synth_llm=FakeText("Diagnosis: root cause is X [worker:slow_queries]. Next steps: ..."),
        ch_select=_fake_rows,
        max_workers=max_workers,
        max_rounds=max_rounds,
    )


# --------------------------- tests ---------------------------
@pytest.mark.parametrize("n", [1, 2, 4, 6])
def test_plan_of_n_yields_exactly_n_workers(n):
    plan = Plan(tasks=[Task(analysis_type=k, focus="f") for k in _catalog_keys(n)],
                reasoning="sized to symptom")
    inv = _investigator(FixedStructured(plan))
    res = inv.run(f"symptom needing {n} analyses")
    assert res["workers_spawned"] == n
    assert len(res["findings"]) == n
    # every finding maps to a distinct planned analysis
    assert {f["analysis_type"] for f in res["findings"]} == set(_catalog_keys(n))


def test_trace_shape_varies_between_two_symptoms():
    small = _investigator(FixedStructured(
        Plan(tasks=[Task(analysis_type=k) for k in _catalog_keys(2)], reasoning="narrow")))
    big = _investigator(FixedStructured(
        Plan(tasks=[Task(analysis_type=k) for k in _catalog_keys(6)], reasoning="broad")))
    assert small.run("narrow symptom")["workers_spawned"] == 2
    assert big.run("broad symptom")["workers_spawned"] == 6


def test_replan_loop_runs_second_wave_then_guard_stops():
    # gate always insufficient → loop until MAX_PLAN_ROUNDS stops it.
    plans = [
        Plan(tasks=[Task(analysis_type="slow_queries"), Task(analysis_type="query_errors")],
             reasoning="round 1"),
        Plan(tasks=[Task(analysis_type="parts_pressure"), Task(analysis_type="merge_backlog")],
             reasoning="round 2 delta"),
    ]
    inv = _investigator(SeqPlanner(plans), gate_sufficient=False, max_rounds=2)
    res = inv.run("mutations stuck and ALTERs never finish")
    assert res["rounds"] == 2               # two orchestrator visits
    assert res["workers_spawned"] == 4      # two waves of two workers
    assert len(res["findings"]) == 4


def test_replan_respects_worker_budget():
    # max_workers=3 caps total fan-out even when the gate keeps asking for more.
    plans = [
        Plan(tasks=[Task(analysis_type="slow_queries"), Task(analysis_type="query_errors")],
             reasoning="r1"),
        Plan(tasks=[Task(analysis_type="parts_pressure"), Task(analysis_type="merge_backlog")],
             reasoning="r2"),
    ]
    inv = _investigator(SeqPlanner(plans), gate_sufficient=False, max_workers=3, max_rounds=3)
    res = inv.run("broad instability")
    assert res["workers_spawned"] <= 3


def test_fault_overplan_hits_the_worker_cap():
    # fault:overplan is deterministic (no planner call) → reliably == MAX_WORKERS.
    inv = _investigator(FixedStructured(Plan(tasks=[Task(analysis_type="slow_queries")],
                                             reasoning="unused")), max_workers=8)
    res = inv.run("anything", fault="overplan")
    assert res["workers_spawned"] == 8
    assert res["fault"] == "overplan"

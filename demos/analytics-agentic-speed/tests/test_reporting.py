"""Report facts and metric checks, including the real trace's unsupported LGA claim."""
import pytest

from explorer import agent as A
from explorer.models import Lever, Plan, RoundRecord, ScenarioOutcome
from explorer.reporting import build_summary


def outcome(origin, buffer, metric, support=1000, error=None):
    return ScenarioOutcome({"origin": origin, "buffer": buffer}, metric, support, {}, 1, 2, 1000, error=error)


def record(outcomes, index=1):
    plan = Plan("SELECT ...", [Lever("origin", "String", ["LAX", "LGA"])], "padding", "minimize")
    return RoundRecord(index, plan, outcomes, 10)


def test_summary_cannot_extrapolate_a_null_airport_into_a_threshold_crossing():
    # From trace 87dbea...: LAX at 9 returned 9 / support 68,453;
    # LGA at the largest tested buffer (29) returned NULL / support 45,895.
    rounds = [record([outcome("LAX", 9, 9.0, 68453), outcome("ATL", 10, 10.0, 141280),
                      outcome("LGA", 29, None, 45895)])]
    summary, evidence = build_summary(rounds, {"origin": "LAX", "buffer": 9},
                                      grounded=True, stop_reason="answered")
    assert evidence["selected"] == {"round": 1, "scenario": {"origin": "LAX", "buffer": 9},
                                    "metric": 9.0, "support": 68453}
    assert evidence["with_metric"] == 2 and evidence["without_metric"] == 1
    assert evidence["scenario_queries"] == 3
    assert "68,453" in summary and "1 returned no usable metric" in summary
    assert "only the tested scenarios" in summary
    assert "every airport" not in summary.lower() and "85%" not in summary


def test_counts_include_repeats_errors_and_nulls_without_claiming_unique_coverage():
    rec = record([outcome("LAX", 9, 9.0), outcome("LGA", 29, None),
                  outcome("ORD", 0, None, error="query failed")])
    summary, e = build_summary([rec, record(rec.outcomes, index=2)], {"origin": "LAX", "buffer": 9},
                               grounded=True, stop_reason="answered")
    assert (e["scenario_queries"], e["rounds"], e["with_metric"], e["without_metric"], e["failed"]) == (6, 2, 2, 2, 2)
    assert "6 scenario queries" in summary and "unique" not in summary
    assert e["selected"]["round"] == 2


@pytest.mark.parametrize("metric,error", [(None, None), (float("nan"), None), (float("inf"), None), (9.0, "failed")])
def test_unusable_scenario_never_becomes_a_verified_report(metric, error):
    summary, e = build_summary([record([outcome("LGA", 29, metric, error=error)])],
                               {"origin": "LGA", "buffer": 29}, grounded=True, stop_reason="answered")
    assert e["selected"] is None and not e["metric_verified"]
    assert "do not support a verified recommendation" in summary


@pytest.mark.parametrize("grounded,stop", [(False, "answered"), (None, "answered"), (None, "budget"),
                                         (None, "max_rounds"), (None, "error")])
def test_unverified_and_stopped_runs_report_status_without_a_measured_recommendation(grounded, stop):
    summary, e = build_summary([record([outcome("LAX", 9, 9)])], {"origin": "LAX", "buffer": 9},
                               grounded=grounded, stop_reason=stop)
    assert not e["metric_verified"] and e["selected"] is None
    assert "the measured metric was" not in summary


def test_missing_selected_scenario_and_empty_runs_are_explicit():
    summary, e = build_summary([], {"origin": "LAX"}, grounded=True, stop_reason="answered")
    assert e["scenario_queries"] == 0 and e["selected"] is None
    assert "0 scenario queries" in summary


@pytest.mark.parametrize("actual,ok", [(0.8507, True), (0.850704, True), (0.85075, False), (0.854, False),
                                      (None, False), (True, False), (float("nan"), False), (float("inf"), False)])
def test_metric_check_permits_display_rounding_but_not_old_half_percent_slack(actual, ok):
    assert A._metric_matches(0.8507, actual) is ok


@pytest.mark.parametrize("claimed,rerun,ok", [(0.8507, 0.8507, True), (0.854, 0.854, False),
                                            (0.8507, 0.854, False), (0.8507, None, False)])
async def test_verification_requires_agreement_with_the_original_sweep(claimed, rerun, ok):
    from test_review_regressions import LF
    from explorer.ch_client import QueryResult, QueryStats
    from explorer.config import RunConfig

    class CH:
        async def query(self, *args, **kw):
            return QueryResult(["metric"], ["Float64"], [[rerun]], QueryStats(1, 2, 1000, 8000, 1, "q"))

    decision = {"decision": "answer", "metric_value": claimed,
                "best_scenario": [{"name": "origin", "value": "LAX"}, {"name": "buffer", "value": "9"}]}
    grounded = await A._verify(LF(), CH(), A.DbMeter(), RunConfig(),
                                [record([outcome("LAX", 9, 0.8507)])], decision)
    assert grounded is ok

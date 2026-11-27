"""
seed_all.py offline: the order of the steps, when the paid traffic step runs, and what
counts as a failure. The seeders themselves are replaced by a fake runner, so nothing
here touches the network, Langfuse or a model.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import seed_all as s  # noqa: E402


class Runner:
    """Stands in for run_script: records each call and returns a canned (exit code, output)."""

    def __init__(self, results=None):
        self.calls, self.results = [], results or {}

    def __call__(self, argv):
        self.calls.append(argv)
        return self.results.get(argv, (0, ["+ something", "= something else"]))


def never_counted():
    raise AssertionError("the corpus must not be read in this mode")


def keys(runner):
    by_argv = {step.argv: step.key for step in s.STEPS}
    return [by_argv[a] for a in runner.calls]


def test_every_step_names_a_script_that_exists():
    for step in s.STEPS:
        assert (ROOT / "scripts" / step.argv[0]).is_file(), step


def test_judges_are_seeded_before_traffic_and_the_queue_is_filled_last():
    order = [step.key for step in s.STEPS]
    assert order == ["prompts", "project", "evaluators", "traffic", "queue"]


def test_fresh_project_gets_traffic(capsys):
    run = Runner()
    assert s.main([], run=run, count_runs=lambda: 0) == 0
    assert keys(run) == ["prompts", "project", "evaluators", "traffic", "queue"]
    assert "$6-8" in capsys.readouterr().out   # the cost is said before the money is spent


@pytest.mark.parametrize("have", [s.MIN_RUNS, s.MIN_RUNS + 30])
def test_seeded_project_is_not_charged_again(have):
    run = Runner()
    assert s.main([], run=run, count_runs=lambda: have) == 0
    assert keys(run) == ["prompts", "project", "evaluators", "queue"]


def test_a_partly_seeded_project_is_topped_up():
    run = Runner()
    assert s.main([], run=run, count_runs=lambda: s.MIN_RUNS - 1) == 0
    assert "traffic" in keys(run)


def test_no_traffic_never_reads_the_corpus_or_runs_the_agent():
    run = Runner()
    assert s.main(["--no-traffic"], run=run, count_runs=never_counted) == 0
    assert keys(run) == ["prompts", "project", "evaluators", "queue"]


def test_traffic_flag_forces_it_without_reading_the_corpus():
    run = Runner()
    assert s.main(["--traffic"], run=run, count_runs=never_counted) == 0
    assert keys(run).count("traffic") == 1


def test_the_two_traffic_flags_exclude_each_other():
    with pytest.raises(SystemExit):
        s.main(["--traffic", "--no-traffic"], run=Runner(), count_runs=never_counted)


def test_dry_run_runs_nothing(capsys):
    run = Runner()
    assert s.main(["--dry-run"], run=run, count_runs=never_counted) == 0
    assert run.calls == []
    out = capsys.readouterr().out
    assert all(step.argv[0] in out for step in s.STEPS)


def test_a_failing_step_stops_the_run_before_any_money_is_spent():
    run = Runner({("seed_project.py", "all"): (1, ["RuntimeError: POST /score-configs -> 403"])})
    assert s.main([], run=run, count_runs=never_counted) == 1
    assert keys(run) == ["prompts", "project"]


def test_a_problem_line_fails_the_run_even_when_the_seeder_exits_zero():
    # seed_evaluators.py prints "! could not create evaluator …" and still returns 0.
    run = Runner({("seed_evaluators.py",): (0, ["+ LLM connection anthropic (200)",
                                                "! could not create evaluator request-fulfilled"])})
    assert s.main([], run=run, count_runs=never_counted) == 1
    assert keys(run) == ["prompts", "project", "evaluators"]


def test_failed_traffic_jobs_fail_the_run():
    run = Runner({("seed_traffic.py",): (1, ["FAILED ('scenario', 'faq-page', {}, False):"])})
    assert s.main(["--traffic"], run=run, count_runs=never_counted) == 1
    assert "queue" not in keys(run)


@pytest.mark.parametrize("lines,expected", [
    (["+ score config root-cause", "= score config reviewer-decision", "= dataset regression-cases"],
     {"changed": 1, "unchanged": 2, "problems": 0}),
    (["  ✓ Agent runs  (1 rows)", "  ✗ Slowest tools (p95 latency)  (bad filter)", "    widget rejected: 400"],
     {"changed": 0, "unchanged": 0, "problems": 2}),
    (["pricing-check     contoso   rejected   4 steps    41s  https://example.invalid/t/1"],
     {"changed": 0, "unchanged": 0, "problems": 0}),
])
def test_tally_reads_the_seeders_own_markers(lines, expected):
    assert s.tally(lines) == expected

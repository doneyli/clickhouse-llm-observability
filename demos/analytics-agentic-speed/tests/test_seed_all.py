"""scripts/seed_all.py, offline: the step plan, stop-at-first-failure and the exit code."""
from __future__ import annotations

import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
if str(DEMO / "scripts") not in sys.path:
    sys.path.insert(0, str(DEMO / "scripts"))

import seed_all  # noqa: E402


def names(steps):
    return [s.name for s, _ in steps]


def test_default_plan_runs_all_three_in_seed_order():
    steps = seed_all.plan()
    assert names(steps) == ["data", "langfuse", "dataset"]
    for step, cmd in steps:
        assert cmd[0] == sys.executable
        assert Path(cmd[1]) == DEMO / step.script
        assert Path(cmd[1]).is_file(), f"{step.script} is missing"
        assert cmd[2:] == []


def test_only_keeps_seed_order_and_skip_data_drops_the_load():
    assert names(seed_all.plan(only=["dataset", "langfuse"])) == ["langfuse", "dataset"]
    assert names(seed_all.plan(skip_data=True)) == ["langfuse", "dataset"]
    assert names(seed_all.plan(only=["data"], skip_data=True)) == []


def test_dry_run_is_passed_to_every_seeder():
    assert all(cmd[2:] == ["--dry-run"] for _, cmd in seed_all.plan(dry_run=True))


def test_every_seeder_accepts_dry_run():
    for step in seed_all.STEPS:
        assert '"--dry-run"' in (DEMO / step.script).read_text(), step.script


def test_stops_at_first_failure_and_exits_1(capsys):
    calls = []

    def runner(cmd):
        calls.append(Path(cmd[1]).name)
        return 1 if cmd[1].endswith("seed_langfuse.py") else 0

    assert seed_all.main([], runner=runner) == 1
    assert calls == ["load_data.py", "seed_langfuse.py"]
    out = capsys.readouterr().out
    assert "seed_all: data ok, langfuse failed, dataset not run" in out


def test_all_ok_exits_0(capsys):
    assert seed_all.main(["--skip-data"], runner=lambda cmd: 0) == 0
    assert "seed_all: langfuse ok, dataset ok" in capsys.readouterr().out


def test_nothing_to_run_is_not_a_failure(capsys):
    assert seed_all.main(["--only", "data", "--skip-data"], runner=lambda cmd: 1) == 0
    assert "nothing to run" in capsys.readouterr().out

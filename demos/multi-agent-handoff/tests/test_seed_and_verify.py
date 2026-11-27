"""
The seed entry point and the readiness check's verdict, with nothing live: no Langfuse, no
model calls, no services. The seeders and the readiness reads themselves are not run here.
"""

import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO / "scripts"))

import seed_all  # noqa: E402
import verify_demo  # noqa: E402


def test_seed_all_names_scripts_that_exist():
    for _, cmd in seed_all.STEPS:
        assert (DEMO / cmd[0]).is_file(), cmd[0]


def test_seed_all_runs_every_seeder_in_order_with_the_documented_flags():
    ran = []
    assert seed_all.seed_all(run=lambda argv: ran.append(list(argv[1:])) or 0, python="py") == 0
    assert ran == [
        ["scripts/seed_prompts.py", "--faults"],
        ["scripts/seed_datasets.py"],
        ["scripts/seed_evals.py", "--judges", "--queue"],
        ["scripts/seed_dashboards.py"],
    ]


def test_seed_all_stops_at_the_first_failure(capsys):
    ran = []

    def run(argv):
        ran.append(argv[1])
        return 3 if argv[1] == "scripts/seed_datasets.py" else 0

    assert seed_all.seed_all(run=run, python="py") == 1
    assert ran == ["scripts/seed_prompts.py", "scripts/seed_datasets.py"]
    assert "FAILED at step 2" in capsys.readouterr().out


def test_seed_all_plan_runs_nothing(capsys, monkeypatch):
    monkeypatch.setattr(seed_all, "_run", lambda argv: pytest.fail("--plan must not run a seeder"))
    assert seed_all.main(["--plan"]) == 0
    out = capsys.readouterr().out
    assert out.count("scripts/seed_") == len(seed_all.STEPS)


@pytest.fixture
def verdict(monkeypatch):
    """Run verify_demo.main() with the readiness reads replaced by the given checks."""
    def run(checks):
        monkeypatch.setattr(verify_demo, "FAIL", [])
        monkeypatch.setattr(verify_demo, "WARN", [])
        monkeypatch.setattr(verify_demo, "readiness", lambda: [verify_demo.check(*c) for c in checks])
        monkeypatch.setattr(sys, "argv", ["verify_demo.py"])
        verify_demo.main()
    return run


def test_verify_prints_ready_when_only_optional_checks_miss(verdict, capsys):
    verdict([(True, "Langfuse keys"), (False, "production traffic in the last 24 h", "0 turns", False)])
    out = capsys.readouterr().out
    assert "[WARN] production traffic in the last 24 h" in out
    assert "0 required failure(s), 1 warning(s)" in out
    assert out.rstrip().endswith("READY") and "NOT READY" not in out


def test_verify_prints_not_ready_and_exits_1_on_a_required_failure(verdict, capsys):
    with pytest.raises(SystemExit) as exc:
        verdict([(False, "A2A service billing", "start them: ./scripts/run_services.sh")])
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "FAILED: A2A service billing" in out
    assert out.rstrip().endswith("NOT READY")

"""scripts/seed_all.py, offline: the step plan, env isolation, stop-at-first-failure, exit codes."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent
for p in (DEMO, DEMO / "scripts", DEMO / "tests"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import seed_all  # noqa: E402
import seed_langfuse  # noqa: E402
from fakes_langfuse import FakeSeedAPI  # noqa: E402

FULL_ENV = """\
# a filled-in .env
CH_HOST=svc.example.clickhouse.cloud
CH_PORT=8443
CH_USER=scenario_agent
CH_PASSWORD="s3cret"
CH_ADMIN_USER=default
CH_ADMIN_PASSWORD=admin-pw
ANTHROPIC_API_KEY=sk-ant-test
export LANGFUSE_PUBLIC_KEY=pk-lf-demo
LANGFUSE_SECRET_KEY=sk-lf-demo
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
"""


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-OTHER")  # hostile shell exports
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-OTHER")
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example")
    monkeypatch.delenv("EXPLORER_TARGET", raising=False)
    p = tmp_path / ".env"
    p.write_text(FULL_ENV)
    return p


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


def test_stops_at_first_failure_and_exits_1(env_file, capsys):
    calls = []

    def runner(cmd, env):
        calls.append(Path(cmd[1]).name)
        return 1 if cmd[1].endswith("seed_langfuse.py") else 0

    assert seed_all.main([], runner=runner, env_file=env_file) == 1
    assert calls == ["load_data.py", "seed_langfuse.py"]
    assert "seed_all: data ok, langfuse failed, dataset not run" in capsys.readouterr().out


def test_all_ok_exits_0(env_file, capsys):
    assert seed_all.main(["--skip-data"], runner=lambda cmd, env: 0, env_file=env_file) == 0
    assert "seed_all: langfuse ok, dataset ok" in capsys.readouterr().out


def test_nothing_to_run_is_not_a_failure(capsys):
    assert seed_all.main(["--only", "data", "--skip-data"], runner=lambda cmd, env: 1,
                         env_file=Path("/nonexistent/.env")) == 0
    assert "nothing to run" in capsys.readouterr().out


# ---------------------------------------------------------------- environment

def test_children_never_inherit_langfuse_vars(env_file):
    seen = []
    assert seed_all.main([], runner=lambda cmd, env: seen.append(dict(env)) or 0, env_file=env_file) == 0
    assert len(seen) == 3
    for env in seen:
        assert not [k for k in env if k.startswith("LANGFUSE_")]
        assert env.get("PATH") == os.environ.get("PATH")  # everything else is passed through


def test_missing_env_file_runs_nothing(tmp_path, capsys):
    calls = []
    assert seed_all.main([], runner=lambda cmd, env: calls.append(cmd) or 0, env_file=tmp_path / ".env") == 1
    assert calls == [] and "no .env at" in capsys.readouterr().err


def test_partial_env_runs_nothing_and_names_the_gaps(env_file, capsys):
    env_file.write_text(FULL_ENV.replace("LANGFUSE_SECRET_KEY=sk-lf-demo\n", "")
                        .replace("CH_ADMIN_PASSWORD=admin-pw", "CH_ADMIN_PASSWORD="))
    calls = []
    assert seed_all.main([], runner=lambda cmd, env: calls.append(cmd) or 0, env_file=env_file) == 1
    err = capsys.readouterr().err
    assert calls == []
    assert "CH_ADMIN_PASSWORD" in err and "LANGFUSE_SECRET_KEY" in err  # the shell's secret doesn't count


def test_placeholders_from_env_example_count_as_missing(env_file, capsys):
    env_file.write_text(FULL_ENV.replace("svc.example.clickhouse.cloud", "<your-service>.<region>.aws.clickhouse.cloud"))
    assert seed_all.main(["--only", "langfuse"], runner=lambda c, e: 0, env_file=env_file) == 0  # no CH needed
    assert seed_all.main(["--only", "dataset"], runner=lambda c, e: 0, env_file=env_file) == 1
    assert "CH_HOST" in capsys.readouterr().err


def test_the_real_env_example_is_not_seedable(capsys):
    example = DEMO / ".env.example"
    assert seed_all.main([], runner=lambda c, e: 0, env_file=example) == 1
    err = capsys.readouterr().err
    for key in ("CH_HOST", "CH_PASSWORD", "LANGFUSE_PUBLIC_KEY"):
        assert key in err


def test_parse_env_file_matches_dotenv_basics():
    parsed = seed_all.parse_env_file('A=1\nexport B="two"\n# C=3\nD=four # note\nE=\nnot a line\n')
    assert parsed == {"A": "1", "B": "two", "D": "four", "E": ""}


# ---------------------------------------------------------------- the real helper, through the wrapper

def in_process(fake: FakeSeedAPI, monkeypatch):
    """A runner that runs seed_langfuse.main for real (on the fake API), stubs the other two."""
    monkeypatch.setattr(seed_langfuse.API, "from_env", classmethod(lambda cls: fake))
    ran = []

    def runner(cmd, env):
        ran.append(Path(cmd[1]).name)
        if cmd[1].endswith("seed_langfuse.py"):
            return seed_langfuse.main(cmd[2:])
        return 0
    return runner, ran


def test_score_config_conflict_in_the_helper_fails_the_wrapper(env_file, monkeypatch, capsys):
    fake = FakeSeedAPI()
    fake.seed_everything()
    next(c for c in fake.configs if c["name"] == "recommendation_grounded")["dataType"] = "NUMERIC"
    runner, ran = in_process(fake, monkeypatch)
    assert seed_all.main([], runner=runner, env_file=env_file) == 1
    assert ran == ["load_data.py", "seed_langfuse.py"]  # the dataset never ran on a broken project
    assert "seed_all: data ok, langfuse failed, dataset not run" in capsys.readouterr().out


def test_helper_read_back_failure_fails_the_wrapper(env_file, monkeypatch):
    fake = FakeSeedAPI()
    fake.seed_everything()
    next(c for c in fake.configs if c["name"] == "llm_calls")["description"] = "old"
    fake.ignore_patches = True
    runner, _ = in_process(fake, monkeypatch)
    assert seed_all.main(["--only", "langfuse"], runner=runner, env_file=env_file) == 1


def test_healthy_helper_passes_the_wrapper(env_file, monkeypatch):
    runner, ran = in_process(FakeSeedAPI(), monkeypatch)
    assert seed_all.main([], runner=runner, env_file=env_file) == 0
    assert ran == ["load_data.py", "seed_langfuse.py", "seed_dataset.py"]

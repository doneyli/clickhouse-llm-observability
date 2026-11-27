"""seed_all.py offline: where the keys come from (host vs container), and the reconcile of the
prompts (through the fake Langfuse at the urllib level) and the datasets (through a fake SDK
client). No network, Langfuse, Docker or model calls."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import seed_all
import seed_datasets
import seed_prompts
import sql_voting
import verify_demo
from fake_langfuse import FakeLangfuse


@pytest.fixture(autouse=True)
def restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# --- where the keys come from ------------------------------------------------------------

def test_container_is_recognised_positively(tmp_path):
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    assert seed_all.in_container(Path("/app"), marker) is True
    assert seed_all.in_container(Path("/app"), tmp_path / "absent") is False     # no Docker marker
    assert seed_all.in_container(tmp_path, marker) is False                       # not the compose mount
    assert seed_all.in_container() is False                                        # this host checkout


def test_host_uses_the_root_env_over_hostile_shell_keys(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\n"
                                   "LANGFUSE_BASE_URL=http://localhost:3001\nLANGFUSE_HOST=http://stale:3001\n")
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    env = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "LANGFUSE_SECRET_KEY": "sk-shell", "PATH": "/bin"}
    assert seed_all.prepare_env(env, container=False) == str(tmp_path / ".env")
    assert env["LANGFUSE_PUBLIC_KEY"] == "pk-file" and env["LANGFUSE_SECRET_KEY"] == "sk-file"
    assert env["LANGFUSE_HOST"] == env["LANGFUSE_BASE_URL"] == "http://localhost:3001"
    assert env["PATH"] == "/bin"


def test_host_without_a_root_env_fails_and_drops_shell_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)          # no .env here
    env = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "LANGFUSE_SECRET_KEY": "sk-shell",
           "LANGFUSE_BASE_URL": "https://other.example"}
    with pytest.raises(SystemExit, match="No root .env"):
        seed_all.prepare_env(env, container=False)
    assert not any(k.startswith("LANGFUSE_") for k in env)


def test_host_with_a_partial_root_env_fails(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=http://localhost:3001\n")
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit, match="LANGFUSE_SECRET_KEY"):
        seed_all.prepare_env({"LANGFUSE_SECRET_KEY": "sk-shell"}, container=False)


def test_container_keeps_the_compose_env(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    env = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk", "LANGFUSE_HOST": "http://langfuse-web:3000"}
    assert seed_all.prepare_env(env, container=True) == "compose"
    assert env["LANGFUSE_PUBLIC_KEY"] == "pk" and env["LANGFUSE_BASE_URL"] == "http://langfuse-web:3000"


def test_container_without_keys_fails(tmp_path):
    with pytest.raises(SystemExit, match="LANGFUSE_PUBLIC_KEY"):
        seed_all.prepare_env({"LANGFUSE_HOST": "http://langfuse-web:3000"}, container=True)


def test_missing_host_env_stops_main_before_any_request(tmp_path, monkeypatch):
    lf = FakeLangfuse().install(monkeypatch)
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(seed_all, "in_container", lambda *a: False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell")
    with pytest.raises(SystemExit):
        seed_all.main()
    assert lf.requests == []


# --- a fake SDK client for the datasets ----------------------------------------------------

class NotFound(Exception):
    status_code = 404


class FakeSdk:
    def __init__(self):
        self.datasets = {}          # name -> description
        self.items = {}             # name -> {id: SimpleNamespace}
        self.writes = []
        self.fail_items = set()
        self.fail_reads = False
        sdk = self

        class _Datasets:
            def get(self, name):
                if sdk.fail_reads:
                    raise RuntimeError("HTTP 500")
                if name not in sdk.datasets:
                    raise NotFound(name)
                return SimpleNamespace(name=name, description=sdk.datasets[name])

        class _Items:
            def list(self, dataset_name, page, limit):
                rows = list(sdk.items.get(dataset_name, {}).values())
                total_pages = max(1, -(-len(rows) // 3))
                return SimpleNamespace(data=rows[(page - 1) * 3:page * 3],
                                       meta=SimpleNamespace(total_pages=total_pages))

        self.api = SimpleNamespace(datasets=_Datasets(), dataset_items=_Items())

    def create_dataset(self, name, description):
        self.writes.append(("dataset", name))
        self.datasets[name] = description

    def create_dataset_item(self, dataset_name, id, input, expected_output, status):
        if id in self.fail_items:
            raise RuntimeError("HTTP 500")
        self.writes.append(("item", id))
        self.items.setdefault(dataset_name, {})[id] = SimpleNamespace(
            id=id, status=status, input=input, expected_output=expected_output, metadata=None)

    def flush(self):
        pass


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """Host run: a root .env, hostile shell keys, the fake HTTP API for prompts and the fake
    SDK for datasets; the playground pins `sig-<first word>` for every reference query."""
    (tmp_path / ".env").write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n"
                                   "LANGFUSE_BASE_URL=https://lf.test\n")
    monkeypatch.setattr(verify_demo, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(seed_all, "in_container", lambda *a: False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-other-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example")
    http = FakeLangfuse().install(monkeypatch)
    sdk = FakeSdk()
    monkeypatch.setattr(seed_datasets, "_client", lambda: sdk)
    monkeypatch.setattr(sql_voting, "_result_signature", lambda sql: f"sig-{len(sql)}")
    return SimpleNamespace(http=http, sdk=sdk)


def seed(capsys):
    code = seed_all.main()
    return code, capsys.readouterr().out


def test_fresh_project_is_created_then_an_unchanged_rerun_writes_nothing(stack, capsys):
    code, out = seed(capsys)
    assert code == 0, out
    assert "created    support-triage-summary" in out
    assert len(stack.http.posts) == 8             # 6 prompts + voter v1 baseline + voter production
    assert stack.http.prompts["support-triage-sql-voter"][0]["labels"] == []
    assert set(stack.http.auth) == {"Basic cGs6c2s="}  # pk:sk from the file, never the shell's key
    assert len(stack.sdk.writes) == 2 + 12 + 10
    assert stack.http.requests  # every request went to the file's host: the fake refuses any other

    stack.http.posts.clear()
    stack.sdk.writes.clear()
    code, out = seed(capsys)
    assert code == 0, out
    assert stack.http.posts == [] and stack.sdk.writes == []
    assert out.count("unchanged  ") == 7
    assert "0 created, 0 updated, 12 unchanged" in out and "0 created, 0 updated, 10 unchanged" in out


def test_config_only_prompt_drift_is_updated(stack, capsys):
    seed(capsys)
    stack.http.posts.clear()
    stack.http.prompts["support-triage-summary"][-1]["config"] = {"model": "claude-haiku-4-5", "temperature": 0.9}
    code, out = seed(capsys)
    assert code == 0 and "updated    support-triage-summary" in out
    assert [p["name"] for p in stack.http.posts] == ["support-triage-summary"]
    assert stack.http.posts[0]["config"] == {"model": seed_prompts.BRANCH_MODEL, "temperature": 0.3}


def test_text_drift_and_a_missing_label_are_reconciled_without_a_second_baseline(stack, capsys):
    seed(capsys)
    stack.http.posts.clear()
    stack.http.prompts["support-triage-synthesis"][-1]["prompt"] = "edited in a rehearsal"
    for version in stack.http.prompts["support-triage-sql-voter"]:
        version["labels"] = []                    # someone removed production
    code, out = seed(capsys)
    assert code == 0
    assert sorted(p["name"] for p in stack.http.posts) == ["support-triage-sql-voter", "support-triage-synthesis"]
    voter = [p for p in stack.http.posts if p["name"] == "support-triage-sql-voter"]
    assert voter[0]["prompt"] == seed_prompts.SQL_VOTER_V2 and voter[0]["labels"] == ["production"]


def test_a_partial_prompt_write_failure_fails_the_run_but_seeds_the_rest(stack, capsys):
    stack.http.fail_posts_for = {"support-triage-category"}
    code, out = seed(capsys)
    assert code == 1
    assert "failed     support-triage-category" in out and "1 failed" in out
    assert "created    support-triage-summary" in out
    assert len(stack.sdk.writes) == 24            # datasets still seeded


def test_a_total_prompt_write_failure_fails_the_run(stack, capsys):
    stack.http.fail = {"/api/public/v2/prompts": 500}
    code, out = seed(capsys)
    assert code == 1 and "7 failed" in out


def test_archived_and_changed_items_are_reactivated_and_others_left_alone(stack, capsys):
    seed(capsys)
    stack.sdk.writes.clear()
    items = stack.sdk.items[seed_datasets.CATEGORY_DATASET]
    items["cat-03"].status = "ARCHIVED"
    items["cat-05"].expected_output = {"category": "billing"}
    code, out = seed(capsys)
    assert code == 0
    assert stack.sdk.writes == [("item", "cat-03"), ("item", "cat-05")]
    assert str(items["cat-03"].status).endswith("ACTIVE")
    assert "0 created, 2 updated, 10 unchanged" in out


def test_a_playground_outage_keeps_signatures_pinned_earlier(stack, capsys, monkeypatch):
    seed(capsys)
    stack.sdk.writes.clear()
    monkeypatch.setattr(sql_voting, "_result_signature", lambda sql: None)
    code, out = seed(capsys)
    assert code == 0 and stack.sdk.writes == []


def test_no_pinned_signature_on_a_fresh_project_fails(stack, capsys, monkeypatch):
    monkeypatch.setattr(sql_voting, "_result_signature", lambda sql: None)
    code, out = seed(capsys)
    assert code == 1 and "no pinned result signature" in out


def test_dataset_item_write_failures_fail_the_run(stack, capsys):
    stack.sdk.fail_items = {"vote-02"}
    code, out = seed(capsys)
    assert code == 1 and "failed  support-triage/sql-voting vote-02" in out


def test_a_dataset_read_failure_fails_the_run_but_prompts_are_seeded(stack, capsys):
    stack.sdk.fail_reads = True
    code, out = seed(capsys)
    assert code == 1 and "failed  dataset support-triage/category-branch" in out
    assert len(stack.http.posts) == 8

"""The seeders offline: seed_all's host/container hand-off and env rules, and the real
reconcile paths of seed_prompts, seed_dataset and seed_monitors against a fake Langfuse SDK
client and a fake Monitors HTTP layer. No Docker, no Langfuse, no model calls."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import langfuse_config as lf
import seed_all as s
import seed_dataset
import seed_monitors
import seed_prompts
import verify_demo as v
from fake_services import FakeServer

GOOD_ENV = "LANGFUSE_PUBLIC_KEY=pk-lf-file\nLANGFUSE_SECRET_KEY=sk-lf-file\nLANGFUSE_BASE_URL=http://localhost:3001\n"


class NotFound(Exception):
    status_code = 404


class ServerError(Exception):
    status_code = 500


class FakeSDK:
    """The Langfuse SDK methods the seeders call, over in-memory prompts and datasets."""

    def __init__(self):
        self.versions = []           # prompt versions: dicts
        self.dataset = None          # {"description", "metadata"}
        self.items = {}              # id -> dict
        self.writes = []
        self.fail_items = set()      # item ids whose write raises
        self.drop_items = set()      # item ids whose write "succeeds" but is not stored
        self.fail_get_prompt = None

    def flush(self):
        pass

    # prompts
    def get_prompt(self, name, *, label, type="text", cache_ttl_seconds=None):
        if self.fail_get_prompt == (name, label):
            raise ServerError("HTTP 500")
        for p in self.versions:
            if p["name"] == name and label in p["labels"]:
                return SimpleNamespace(**p)
        raise NotFound(f"{name}@{label}")

    def _take(self, name, labels):
        for p in self.versions:
            if p["name"] == name:
                p["labels"] = [x for x in p["labels"] if x not in labels]

    def create_prompt(self, *, name, prompt, labels, type, config, commit_message):
        self.writes.append(("create_prompt", name, tuple(labels)))
        self._take(name, labels)
        version = 1 + max([p["version"] for p in self.versions if p["name"] == name] or [0])
        self.versions.append({"name": name, "prompt": prompt, "type": type, "config": config,
                              "labels": list(labels), "version": version})
        return SimpleNamespace(version=version)

    def update_prompt(self, *, name, version, new_labels):
        self.writes.append(("update_prompt", name, version, tuple(new_labels)))
        assert "latest" not in new_labels
        self._take(name, new_labels)
        target = next(p for p in self.versions if p["name"] == name and p["version"] == version)
        target["labels"] = sorted(set(target["labels"]) | set(new_labels))

    # datasets
    def get_dataset(self, name):
        if self.dataset is None:
            raise NotFound(name)
        items = [SimpleNamespace(**i) for i in self.items.values()]
        return SimpleNamespace(items=items, **self.dataset)

    def create_dataset(self, *, name, description, metadata):
        self.writes.append(("create_dataset", name))
        self.dataset = {"description": description, "metadata": metadata}

    def create_dataset_item(self, *, id, dataset_name, input, expected_output, metadata, status):
        self.writes.append(("create_dataset_item", id))
        if id in self.fail_items:
            raise ServerError(f"HTTP 500 writing {id}")
        if id not in self.drop_items:
            self.items[id] = {"id": id, "input": input, "expected_output": expected_output,
                              "metadata": metadata, "status": status}


@pytest.fixture
def sdk(monkeypatch):
    client = FakeSDK()
    monkeypatch.setattr(lf, "is_langfuse_enabled", lambda: True)
    monkeypatch.setattr(lf, "get_client", lambda: client)
    return client


@pytest.fixture
def monitors_v3(monkeypatch):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path))
        if path == "/api/public/health":
            return 200, json.dumps({"status": "OK", "version": "3.221.1"}).encode()
        return 404, b"not found"

    monkeypatch.setattr(seed_monitors, "_api", api)
    return calls


@pytest.fixture(autouse=True)
def restore_env():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


# --- seed_all: where it runs and what it needs ---------------------------------------------

def test_container_is_identified_positively(tmp_path):
    marker = tmp_path / ".dockerenv"
    env = {"TUNING_CH_HOST": "clickhouse-tuning"}
    assert s.in_container(env, (str(marker),)) is False          # compose env, no runtime marker
    marker.write_text("")
    assert s.in_container({}, (str(marker),)) is False           # a marker alone is not this service
    assert s.in_container(env, (str(marker),)) is True


@pytest.mark.parametrize("content,needle", [(None, "is missing"),
                                            ("LANGFUSE_PUBLIC_KEY=pk\n", "does not set LANGFUSE_SECRET_KEY"),
                                            ("LANGFUSE_PUBLIC_KEY=\nLANGFUSE_SECRET_KEY=\n", "does not set LANGFUSE_PUBLIC_KEY")])
def test_host_run_fails_before_docker_without_a_complete_root_env(monkeypatch, tmp_path, capsys, content, needle):
    env = tmp_path / ".env"
    if content is not None:
        env.write_text(content)
    monkeypatch.setattr(s, "ROOT_ENV", env)
    monkeypatch.setattr(s, "in_container", lambda: False)
    monkeypatch.setattr(s.subprocess, "call", lambda *a, **k: pytest.fail("docker must not run"))
    assert s.main([]) == 1
    assert needle in capsys.readouterr().out


def test_host_run_hands_off_to_the_container_without_shell_keys(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(GOOD_ENV)
    calls = []
    monkeypatch.setattr(s, "ROOT_ENV", env)
    monkeypatch.setattr(s, "in_container", lambda: False)
    monkeypatch.setattr(s.subprocess, "call", lambda cmd, cwd, env: calls.append((cmd, cwd, env)) or 7)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    monkeypatch.setenv("LANGFUSE_INTERNAL_URL", "https://other.example")
    assert s.main([]) == 7                                       # the container's exit code
    cmd, cwd, child_env = calls[0]
    assert cmd == s.CONTAINER_CMD and cwd == str(s.REPO_ROOT)
    assert not any(k.startswith("LANGFUSE_") for k in child_env)


def test_missing_docker_is_a_failure(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(GOOD_ENV)
    monkeypatch.setattr(s, "ROOT_ENV", env)
    monkeypatch.setattr(s, "in_container", lambda: False)

    def no_docker(*a, **k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(s.subprocess, "call", no_docker)
    assert s.main([]) == 1


def test_local_run_replaces_hostile_shell_keys_with_the_root_env(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(GOOD_ENV)
    monkeypatch.setattr(s, "ROOT_ENV", env)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-shell-other-project")
    monkeypatch.setenv("LANGFUSE_HOST", "https://other.example")
    assert s.load_local_env() is True
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-file"
    assert os.environ["LANGFUSE_HOST"] == "http://localhost:3001"


def test_missing_keys_fail_instead_of_skipping(monkeypatch, capsys):
    monkeypatch.setattr(lf, "is_langfuse_enabled", lambda: False)
    assert s.run_seeders() == 1
    assert "nothing can be seeded" in capsys.readouterr().out


# --- prompts --------------------------------------------------------------------------------

def test_fresh_prompts_are_created_then_unchanged(sdk, capsys):
    assert seed_prompts.main() == 0
    assert [w[0] for w in sdk.writes] == ["create_prompt"] * 3
    assert "3 created, 0 updated, 0 unchanged, 0 failed" in capsys.readouterr().out
    sdk.writes.clear()
    assert seed_prompts.main() == 0
    assert sdk.writes == []                                      # no new versions, no label moves
    assert "0 created, 0 updated, 3 unchanged" in capsys.readouterr().out


def test_missing_secondary_label_is_reattached_without_a_new_version(sdk, capsys):
    seed_prompts.main()
    v2 = next(p for p in sdk.versions if "v2-disciplined" in p["labels"])
    v2["labels"].remove("v2-disciplined")
    sdk.writes.clear()
    assert seed_prompts.main() == 0
    assert sdk.writes == [("update_prompt", "query-tuner-system", v2["version"], ("production", "v2-disciplined"))]
    assert "v2-disciplined attached to" in capsys.readouterr().out
    assert sdk.get_prompt("query-tuner-system", label="v2-disciplined").version == v2["version"]


def test_unrelated_labels_are_kept_when_reattaching(sdk):
    seed_prompts.main()
    v2 = next(p for p in sdk.versions if "production" in p["labels"] and p["name"] == "query-tuner-system")
    v2["labels"] = ["production", "staging", "latest"]
    seed_prompts.main()
    assert set(v2["labels"]) >= {"production", "v2-disciplined", "staging"}


@pytest.mark.parametrize("drift", [{"config": {"temperature": 1}}, {"prompt": "edited in the UI"}])
def test_drift_creates_a_new_version_with_every_label(sdk, capsys, drift):
    seed_prompts.main()
    goal = next(p for p in sdk.versions if p["name"] == "query-tuner-goal")
    goal.update(drift)
    sdk.writes.clear()
    assert seed_prompts.main() == 0
    assert sdk.writes == [("create_prompt", "query-tuner-goal", ("production",))]
    assert "query-tuner-goal [production] updated (new v2)" in capsys.readouterr().out


def test_a_failed_prompt_read_fails_but_the_rest_reconcile(sdk, capsys):
    sdk.fail_get_prompt = ("query-tuner-system", "v1-naive")
    assert seed_prompts.main() == 1
    assert [w[1] for w in sdk.writes] == ["query-tuner-system", "query-tuner-goal"]
    assert "1 failed" in capsys.readouterr().out


# --- dataset --------------------------------------------------------------------------------

def test_fresh_dataset_is_created_then_unchanged(sdk, capsys):
    assert seed_dataset.main() == 0
    assert sdk.writes == [("create_dataset", seed_dataset.DATASET_NAME)] + [("create_dataset_item", f"qt-q{i}") for i in (1, 2, 3)]
    assert all(i["status"] == "ACTIVE" for i in sdk.items.values())
    sdk.writes.clear()
    assert seed_dataset.main() == 0
    assert sdk.writes == []
    assert "0 created, 0 updated, 4 unchanged, 0 failed" in capsys.readouterr().out


def test_archived_and_changed_items_are_restored(sdk):
    seed_dataset.main()
    sdk.items["qt-q1"]["status"] = "ARCHIVED"
    sdk.items["qt-q2"]["metadata"] = {"edited": True}
    sdk.dataset["description"] = "edited"
    sdk.writes.clear()
    assert seed_dataset.main() == 0
    assert sorted(sdk.writes) == [("create_dataset", seed_dataset.DATASET_NAME),
                                  ("create_dataset_item", "qt-q1"), ("create_dataset_item", "qt-q2")]
    assert sdk.items["qt-q1"]["status"] == "ACTIVE"


@pytest.mark.parametrize("fail", [{"qt-q2"}, {"qt-q1", "qt-q2", "qt-q3"}])
def test_item_write_failures_fail_the_seed(sdk, capsys, fail):
    sdk.fail_items = fail
    assert seed_dataset.main() == 1
    assert f"{len(fail)} failed" in capsys.readouterr().out
    assert [w[1] for w in sdk.writes if w[0] == "create_dataset_item"] == ["qt-q1", "qt-q2", "qt-q3"]


def test_a_write_that_does_not_land_fails_the_read_back(sdk, capsys):
    sdk.drop_items = {"qt-q3"}
    assert seed_dataset.main() == 1
    assert "read-back: qt-q3 not stored ACTIVE" in capsys.readouterr().out


def test_item_failure_fails_seed_all_and_the_other_seeders_still_run(sdk, monitors_v3, capsys):
    sdk.fail_items = {"qt-q2"}
    assert s.run_seeders() == 1
    out = capsys.readouterr().out
    assert "FAILED: dataset" in out
    assert ("GET", "/api/public/health") in monitors_v3                 # monitors still ran
    assert any(w[0] == "create_prompt" for w in sdk.writes)             # prompts still ran


def test_full_seed_succeeds_and_verify_accepts_what_it_wrote(sdk, monitors_v3, monkeypatch, tmp_path, capsys):
    assert s.run_seeders() == 0
    server = FakeServer("3.221.1").seed_ready()
    server.prompts = {(p["name"], label): dict(p, tags=[]) for p in sdk.versions for label in p["labels"]}
    server.items = [{"id": i["id"], "status": i["status"], "input": i["input"], "expectedOutput": i["expected_output"],
                     "metadata": i["metadata"], "datasetName": seed_dataset.DATASET_NAME} for i in sdk.items.values()]
    del v.RESULTS[:]
    del v.ACTS[:]
    env = tmp_path / ".env"
    env.write_text(GOOD_ENV + "ANTHROPIC_API_KEY=x\n")
    (tmp_path / "checkpoints").mkdir()
    monkeypatch.setattr(v, "env_file", lambda profile: env)
    monkeypatch.setattr(v, "CHECKPOINTS", tmp_path / "checkpoints")
    monkeypatch.setattr(v, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(v.urllib.request, "urlopen", server)
    assert v.main([]) == 0, capsys.readouterr().out


# --- monitors -------------------------------------------------------------------------------

def test_v3_server_has_no_monitors_to_create(sdk, monitors_v3, capsys):
    assert seed_monitors.main() == 0
    assert monitors_v3 == [("GET", "/api/public/health")]
    assert "Act 4 runs on its trace-cost Fallback" in capsys.readouterr().out


def test_unreadable_health_fails(sdk, monkeypatch):
    monkeypatch.setattr(seed_monitors, "_api", lambda method, path, body=None: (None, b"connection refused"))
    assert seed_monitors.main() == 1


def test_v4_server_creates_the_missing_monitor_and_reports_failures(sdk, monkeypatch, capsys):
    posted = []

    def api(method, path, body=None):
        if path == "/api/public/health":
            return 200, b'{"version": "4.30.0"}'
        if method == "GET":
            return 200, json.dumps({"data": [{"name": "query-tuner runaway cost"}]}).encode()
        posted.append(body["name"])
        return 500, b"boom"

    monkeypatch.setattr(seed_monitors, "_api", api)
    assert seed_monitors.main() == 1
    assert posted == ["query-tuner turn count"]
    assert "query-tuner runaway cost unchanged" in capsys.readouterr().out


def test_verify_expects_the_seeded_names():
    import agent_loop
    assert v.TRACE_NAME == agent_loop.TRACE_NAME
    assert v.MONITORS == [m["name"] for m in seed_monitors.MONITORS]

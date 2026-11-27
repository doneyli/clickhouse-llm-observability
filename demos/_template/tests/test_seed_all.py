"""seed_all.py offline: the reconcile decision for one prompt, against a fake Langfuse."""
from __future__ import annotations

import copy

import pytest

import seed_all as s
import verify_demo as v

SPEC = s.PROMPTS[0]


class FakeLangfuse:
    def __init__(self, served=None, status=404):
        self.served, self.status, self.posts = served, status, []

    def get(self, path, **params):
        if self.served is None:
            raise v.ApiError(self.status, "not found")
        return self.served

    def post(self, path, body):
        self.posts.append(body)


def served(**changes):
    row = {"type": SPEC["type"], "prompt": SPEC["prompt"], "config": copy.deepcopy(SPEC["config"]), "version": 2}
    row.update(changes)
    return row


def test_missing_prompt_is_created_with_its_label():
    lf = FakeLangfuse()
    assert s.reconcile_prompt(lf, SPEC) == "created"
    assert lf.posts[0]["labels"] == [SPEC["label"]] and lf.posts[0]["prompt"] == SPEC["prompt"]


def test_identical_prompt_is_left_unchanged():
    lf = FakeLangfuse(served())
    assert s.reconcile_prompt(lf, SPEC) == "unchanged"
    assert lf.posts == []


@pytest.mark.parametrize("change", [{"prompt": "edited in the UI"}, {"config": {"model": "other"}}])
def test_drift_adds_a_version(change):
    lf = FakeLangfuse(served(**change))
    assert s.reconcile_prompt(lf, SPEC) == "updated"
    assert len(lf.posts) == 1


def test_dry_run_writes_nothing():
    lf = FakeLangfuse(served(prompt="drifted"))
    assert s.reconcile_prompt(lf, SPEC, dry_run=True) == "updated"
    assert lf.posts == []


def test_other_errors_propagate():
    with pytest.raises(v.ApiError):
        s.reconcile_prompt(FakeLangfuse(status=500), SPEC)


def test_verify_expects_what_seed_creates():
    assert {p["name"]: p["label"] for p in s.PROMPTS} == v.EXPECTED_PROMPTS

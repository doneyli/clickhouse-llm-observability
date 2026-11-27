"""seed_all.py offline: the review gate, reconcile decisions, read-back and failure propagation.

Reads go through verify_demo's real HTTP client to tests/fake_langfuse.py; the wrapped
`workshop.py` sub-commands are a fake subprocess that applies the same writes to the fake
project (or fails, or silently does nothing). The real helpers.validate_review gate runs.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import seed_all as s  # noqa: E402
import verify_demo as v  # noqa: E402
from tests.fake_langfuse import FakeAPI  # noqa: E402

ENV = ("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\n"
       "LANGFUSE_BASE_URL=https://lf.example\nWORKSHOP_ID=t-bank\n")


def fresh_project():
    api = FakeAPI()
    api.prompts, api.dataset, api.rows, api.experiments = {}, None, [], []
    return api


class SeedBase(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        os.environ.pop("WORKSHOP_ID", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text(ENV)
        self.api = FakeAPI()
        self.runs = []
        self.fail_on = set()     # sub-command words that exit non-zero
        self.no_effect = set()   # sub-command words that exit 0 but write nothing

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.clear()
        os.environ.update(self.saved)

    def fake_run(self, cmd, **kwargs):
        args = cmd[cmd.index("--env-file") + 2:]
        self.runs.append(args)
        key = f"{args[0]} {args[-1]}"
        if args[0] in self.fail_on or key in self.fail_on:
            return SimpleNamespace(returncode=1, stdout="", stderr="Traceback...\nValueError: refused by helper")
        if args[0] not in self.no_effect:
            self.api.apply_workshop(args)
        return SimpleNamespace(returncode=0, stdout="Published", stderr="")

    def seed(self, *args):
        with patch.object(v.urllib.request, "urlopen", self.api.urlopen), \
                patch.object(s.subprocess, "run", self.fake_run), redirect_stdout(io.StringIO()) as out:
            code = s.main(["--env-file", str(self.env), *args])
        return code, out.getvalue()


class GateTests(SeedBase):
    # 129-1: an unchanged dataset is no way around the reviewed-reference gate
    def test_reviewed_run_after_a_draft_seed_fails_without_writing(self):
        code, out = self.seed("--allow-draft")          # the fake project already matches every item
        self.assertEqual(code, 0, out)
        self.assertIn("unchanged  dataset", out)
        code, out = self.seed()                          # default --reviewed, markers still pending
        self.assertEqual(code, 1, out)
        self.assertIn("failed     dataset bank-workshop/t-bank/regression-v1 (8 items, --reviewed): "
                      "Mark each item's reference_review_required=false", out)
        self.assertEqual(self.runs, [])

    def test_gate_also_applies_to_dry_runs(self):
        code, out = self.seed("--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("failed     dataset", out)

    def test_reviewed_items_pass_the_gate(self):
        reviewed = [dict(i, metadata=dict(i["metadata"], reference_review_required=False)) for i in v.local_items()]
        with patch.object(v, "local_items", lambda: reviewed):
            self.api.items = reviewed
            self.api.rows = []
            code, out = self.seed()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.runs, [["dataset", "--reviewed"]])

    def test_allow_draft_is_limited_to_synthetic_items(self):
        real = [dict(i, metadata=dict(i["metadata"], synthetic=False)) for i in v.local_items()]
        with patch.object(v, "local_items", lambda: real):
            code, out = self.seed("--allow-draft")
        self.assertEqual(code, 1)
        self.assertIn("--allow-draft is limited to synthetic rehearsal examples", out)


class ReconcileTests(SeedBase):
    def test_fresh_project_creates_everything_in_order(self):
        self.api = fresh_project()
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.runs, [["prompt", "--variant", "baseline", "--label", "workshop-baseline"],
                                     ["prompt", "--variant", "candidate", "--label", "workshop-candidate"],
                                     ["dataset", "--allow-draft"]])
        self.assertIn("3 created, 0 updated, 0 unchanged, 0 failed", out)

    def test_unchanged_reseed_writes_nothing(self):
        versions = {k: p["version"] for k, p in self.api.prompts.items()}
        code, out = self.seed("--allow-draft")
        self.assertEqual((code, self.runs), (0, []), out)
        self.assertIn("0 created, 0 updated, 3 unchanged, 0 failed", out)
        self.assertEqual({k: p["version"] for k, p in self.api.prompts.items()}, versions)

    def test_config_only_drift_is_updated(self):
        self.api.prompts["workshop-candidate"]["config"] = {}
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.runs, [["prompt", "--variant", "candidate", "--label", "workshop-candidate"]])
        self.assertIn("updated    prompt bank-workshop/t-bank/assistant (label workshop-candidate", out)

    def test_changed_text_or_type_is_updated(self):
        self.api.prompts["workshop-baseline"]["prompt"] = "old text"
        self.api.prompts["workshop-candidate"]["type"] = "chat"
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 0, out)
        self.assertIn("2 updated", out)

    def test_changed_or_archived_items_are_updated(self):
        self.api.rows[0]["metadata"] = {"changed": True}
        self.api.rows[1]["status"] = "ARCHIVED"
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.runs, [["dataset", "--allow-draft"]])
        self.assertIn("updated    dataset", out)
        self.assertEqual(v.dataset_diff(self.api.dataset_name, v.local_items(), self.api.rows), [])

    def test_extra_remote_items_are_kept_and_not_drift(self):
        self.api.rows.append({"id": "someone-elses", "status": "ACTIVE"})
        code, out = self.seed("--allow-draft")
        self.assertEqual((code, self.runs), (0, []), out)
        self.assertIn("someone-elses", [r["id"] for r in self.api.rows])

    def test_dry_run_reports_drift_and_writes_nothing(self):
        self.api = fresh_project()
        code, out = self.seed("--allow-draft", "--dry-run")
        self.assertEqual((code, self.runs), (0, []), out)
        self.assertIn("3 created", out)

    def test_desired_text_is_what_the_helper_publishes(self):
        from northwind import prompts
        self.assertEqual(s.desired_prompts(), {"baseline": prompts.V1_BASELINE, "candidate": prompts.WORKSHOP_CANDIDATE})


class FailureTests(SeedBase):
    def test_partial_write_failure_fails_the_run_and_continues(self):
        self.api = fresh_project()
        self.fail_on = {"prompt workshop-candidate"}
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 1)
        self.assertIn("failed     prompt bank-workshop/t-bank/assistant (label workshop-candidate, candidate): "
                      "ValueError: refused by helper", out)
        self.assertIn("2 created, 0 updated, 0 unchanged, 1 failed", out)

    def test_total_write_failure(self):
        self.api = fresh_project()
        self.fail_on = {"prompt", "dataset"}
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 1)
        self.assertIn("0 created, 0 updated, 0 unchanged, 3 failed", out)

    def test_a_write_that_does_not_take_effect_is_a_failure(self):
        self.api = fresh_project()
        self.no_effect = {"prompt", "dataset"}
        code, out = self.seed("--allow-draft")
        self.assertEqual(code, 1)
        self.assertIn("still does not serve the baseline definition", out)
        self.assertIn("uploaded, but some items are still missing", out)

    def test_required_read_failures_fail_the_item(self):
        for endpoint in ("prompt", "dataset", "dataset-items"):
            self.api, self.runs = FakeAPI(), []
            self.api.fail[endpoint] = 500
            code, out = self.seed("--allow-draft")
            self.assertEqual(code, 1, endpoint)
            self.assertIn("HTTP 500", out)
            self.assertEqual(self.runs, [])

    def test_missing_or_partial_env_fails_before_any_api_use(self):
        os.environ.update(LANGFUSE_PUBLIC_KEY="pk-shell", LANGFUSE_SECRET_KEY="sk-shell",
                          LANGFUSE_BASE_URL="https://shell.example")
        for text in (None, "LANGFUSE_PUBLIC_KEY=pk-file\n"):
            if text is None:
                self.env.unlink()
            else:
                self.env.write_text(text)
            self.api.calls = []
            with patch("sys.stderr", io.StringIO()):
                code, out = self.seed()
            self.assertEqual((code, self.api.calls, self.runs), (1, [], []))

    def test_unreadable_local_dataset_fails(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text(json.dumps([{"id": "x"}]))
        with patch.object(v.local_items, "__defaults__", (bad,)), patch("sys.stderr", io.StringIO()):
            code, _ = self.seed("--allow-draft")
        self.assertEqual((code, self.api.calls), (1, []))


if __name__ == "__main__":
    unittest.main()

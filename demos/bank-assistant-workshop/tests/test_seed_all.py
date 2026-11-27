"""seed_all.py offline: the reconcile decisions, against a fake Langfuse and fake sub-commands."""
import copy
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

DATASET = "bank-workshop/t-bank/regression-v1"
ITEMS = json.loads(v.DATASET_FILE.read_text())


def remote_rows(items=ITEMS, dataset=DATASET):
    return [{"id": s.item_id(dataset, i["id"]), "status": "ACTIVE", "input": i["input"],
             "expectedOutput": i["expected_output"], "metadata": i["metadata"]} for i in items]


class FakeLangfuse:
    def __init__(self, prompt=None, dataset=True, rows=None):
        self.prompt, self.dataset, self.rows = prompt, dataset, rows if rows is not None else remote_rows()

    def get(self, path, **params):
        if path.startswith("/api/public/v2/prompts/"):
            if self.prompt is None:
                raise v.ApiError(404, "not found")
            return self.prompt
        if path.startswith("/api/public/v2/datasets/"):
            if not self.dataset:
                raise v.ApiError(404, "not found")
            return {"id": "d1"}
        if path == "/api/public/dataset-items":
            return {"data": self.rows, "meta": {"totalPages": 1}}
        raise AssertionError(path)


class PromptTests(unittest.TestCase):
    def reconcile(self, current, dry_run=False):
        calls = []
        result = s.reconcile_prompt(FakeLangfuse(prompt=current), "bank-workshop/t/assistant", "workshop-baseline",
                                    "baseline", "TEXT", lambda var, lab: calls.append((var, lab)), dry_run=dry_run)
        return result, calls

    def test_missing_label_is_created(self):
        self.assertEqual(self.reconcile(None), ("created", [("baseline", "workshop-baseline")]))

    def test_same_text_and_variant_is_unchanged_and_publishes_nothing(self):
        current = {"type": "text", "prompt": "TEXT", "config": {"variant": "baseline"}}
        self.assertEqual(self.reconcile(current), ("unchanged", []))

    def test_drifted_text_or_variant_is_updated(self):
        for current in ({"type": "text", "prompt": "OLD", "config": {"variant": "baseline"}},
                        {"type": "text", "prompt": "TEXT", "config": {"variant": "candidate"}},
                        {"type": "chat", "prompt": "TEXT", "config": {"variant": "baseline"}}):
            self.assertEqual(self.reconcile(current), ("updated", [("baseline", "workshop-baseline")]))

    def test_dry_run_writes_nothing(self):
        self.assertEqual(self.reconcile(None, dry_run=True), ("created", []))

    def test_other_api_errors_propagate(self):
        class Broken(FakeLangfuse):
            def get(self, path, **params):
                raise v.ApiError(500, "boom")
        with self.assertRaises(v.ApiError):
            s.reconcile_prompt(Broken(), "n", "l", "baseline", "T", lambda *a: None)

    def test_desired_text_is_what_the_helper_publishes(self):
        from northwind import prompts
        self.assertEqual(s.desired_prompts(), {"baseline": prompts.V1_BASELINE,
                                               "candidate": prompts.WORKSHOP_CANDIDATE})


class DatasetTests(unittest.TestCase):
    def test_item_id_matches_the_helper(self):
        import hashlib
        self.assertEqual(s.item_id(DATASET, "g01"), hashlib.sha256(f"{DATASET}/g01".encode()).hexdigest()[:32])

    def test_missing_dataset_is_created(self):
        self.assertEqual(s.dataset_drift(FakeLangfuse(dataset=False), DATASET, ITEMS), "created")

    def test_matching_items_are_unchanged(self):
        self.assertEqual(s.dataset_drift(FakeLangfuse(), DATASET, ITEMS), "unchanged")

    def test_extra_remote_items_do_not_count_as_drift(self):
        rows = remote_rows() + [{"id": "extra", "status": "ACTIVE"}]
        self.assertEqual(s.dataset_drift(FakeLangfuse(rows=rows), DATASET, ITEMS), "unchanged")

    def test_missing_changed_or_archived_items_are_updated(self):
        changed = copy.deepcopy(remote_rows())
        changed[0]["expectedOutput"] = "something else"
        archived = copy.deepcopy(remote_rows())
        archived[1]["status"] = "ARCHIVED"
        for rows in (remote_rows()[1:], changed, archived):
            self.assertEqual(s.dataset_drift(FakeLangfuse(rows=rows), DATASET, ITEMS), "updated")


class MainTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\n"
                            "LANGFUSE_BASE_URL=https://lf.example\nWORKSHOP_ID=t-bank\n")

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.clear()
        os.environ.update(self.saved)

    def run_main(self, lf, args=(), returncode=0):
        runs = []

        def fake_run(cmd, **kwargs):
            runs.append(cmd[cmd.index("--env-file") + 2:])
            return SimpleNamespace(returncode=returncode, stdout="", stderr="ValueError: review first")

        texts = {"baseline": "B", "candidate": "C"}
        with patch.object(s.v, "Langfuse", lambda *a: lf), patch.object(s, "desired_prompts", lambda: texts), \
                patch.object(s.subprocess, "run", fake_run), redirect_stdout(io.StringIO()) as out:
            code = s.main(["--env-file", str(self.env), *args])
        return code, runs, out.getvalue()

    def test_fresh_project_runs_both_prompts_then_the_dataset(self):
        lf = FakeLangfuse(dataset=False)
        code, runs, out = self.run_main(lf, ["--allow-draft"])
        self.assertEqual(code, 0, out)
        self.assertEqual(runs, [["prompt", "--variant", "baseline", "--label", "workshop-baseline"],
                                ["prompt", "--variant", "candidate", "--label", "workshop-candidate"],
                                ["dataset", "--allow-draft"]])
        self.assertIn("3 created, 0 updated, 0 unchanged, 0 failed", out)

    def test_second_run_changes_nothing(self):
        class Seeded(FakeLangfuse):
            def get(self, path, **params):
                if path.startswith("/api/public/v2/prompts/"):
                    variant = v.PROMPT_LABELS[params["label"]]
                    return {"type": "text", "prompt": {"baseline": "B", "candidate": "C"}[variant],
                            "config": {"variant": variant}}
                return super().get(path, **params)
        code, runs, out = self.run_main(Seeded(rows=remote_rows(dataset=DATASET)))
        self.assertEqual((code, runs), (0, []), out)
        self.assertIn("0 created, 0 updated, 3 unchanged, 0 failed", out)

    def test_dataset_defaults_to_reviewed_and_a_refusal_fails_the_run(self):
        code, runs, out = self.run_main(FakeLangfuse(dataset=False), returncode=1)
        self.assertEqual(code, 1)
        self.assertEqual(runs[-1], ["dataset", "--reviewed"])
        self.assertIn("failed     dataset", out)
        self.assertIn("ValueError: review first", out)

    def test_missing_env_file_fails(self):
        with redirect_stdout(io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(s.main(["--env-file", "/nonexistent/.env"]), 1)


if __name__ == "__main__":
    unittest.main()

"""scripts/prompt_label.py — label logic and the promote / rollback / set-previous flows.

No network: the pure functions take plain dicts, and the CLI flows run against an
in-memory fake of the prompt-version API (labels are unique across versions, exactly
like Langfuse). Run: .venv/bin/python -m unittest discover -s tests -t . -v
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR / "scripts"))
sys.path.insert(0, str(DEMO_DIR))

import prompt_label as pl  # noqa: E402

# The layout the live project had before the remediation: both labels on v4, an untitled v6.
COLLISION = {1: ["baseline"], 3: ["development"], 4: ["production", "previous-production"],
             5: ["staging"], 6: ["latest"]}
HEALTHY = {1: ["baseline"], 3: ["development"], 4: ["previous-production"], 5: ["production", "staging"]}


class RollbackTarget(unittest.TestCase):
    def test_collision_has_no_target(self):
        self.assertIsNone(pl.rollback_target(COLLISION))

    def test_missing_previous_has_no_target(self):
        self.assertIsNone(pl.rollback_target({1: ["production"], 2: ["staging"]}))

    def test_distinct_versions_roll_back_to_previous(self):
        self.assertEqual(pl.rollback_target(HEALTHY), 4)

    def test_no_production_but_previous_is_still_a_target(self):
        self.assertEqual(pl.rollback_target({1: ["previous-production"]}), 1)


class LabelAnomalies(unittest.TestCase):
    META = {1: {"commitMessage": "v1 baseline"}, 3: {"commitMessage": "v3 growth"},
            4: {"commitMessage": "candidate"}, 5: {"commitMessage": "release"},
            6: {"commitMessage": None}}

    def test_collision_and_untitled_version(self):
        out = pl.label_anomalies(COLLISION, self.META)
        self.assertEqual(len(out), 2)
        self.assertIn("production and previous-production are both on v4", out[0])
        self.assertEqual(out[1], "v6: untitled, unlabelled — delete it in the UI")

    def test_healthy_layout_has_no_anomalies(self):
        self.assertEqual(pl.label_anomalies(HEALTHY, self.META), [])

    def test_meta_none_skips_the_untitled_check(self):
        out = pl.label_anomalies(COLLISION, None)
        self.assertEqual(len(out), 1)
        self.assertIn("both on v4", out[0])

    def test_latest_only_version_with_a_commit_message_is_fine(self):
        vs = {1: ["production"], 2: ["previous-production"], 3: ["latest"]}
        self.assertEqual(pl.label_anomalies(vs, {3: {"commitMessage": "wip: new wording"}}), [])

    def test_unlabelled_old_version_with_no_message_is_flagged(self):
        vs = {1: ["production"], 2: [], 3: ["previous-production"], 4: ["latest"]}
        meta = {2: {"commitMessage": ""}, 4: {"commitMessage": "x"}}
        self.assertEqual(pl.label_anomalies(vs, meta), ["v2: untitled, unlabelled — delete it in the UI"])

    def test_whitespace_commit_message_counts_as_empty(self):
        self.assertTrue(pl.is_untitled(["latest"], "   "))
        self.assertTrue(pl.is_untitled([], None))
        self.assertFalse(pl.is_untitled(["staging"], None))
        self.assertFalse(pl.is_untitled(["latest"], "named"))


class FakePromptApi:
    """In-memory stand-in for config.api(): list / get-by-version / get-by-label / PATCH newLabels."""

    def __init__(self, versions, untitled=()):
        self.v = {n: {"labels": list(labels), "commitMessage": None if n in untitled else f"msg {n}"}
                  for n, labels in versions.items()}
        self.patches = []

    def __call__(self, method, path, body=None, timeout=30, params=None):
        base = f"/api/public/v2/prompts/{pl.NAME}"
        if method == "GET" and path == "/api/public/v2/prompts":
            return {"data": [{"name": pl.NAME, "versions": sorted(self.v)}]}
        if method == "GET" and path == base:
            if "version" in (params or {}):
                n = params["version"]
                return {"name": pl.NAME, "version": n, **self.v[n]}
            for n, p in self.v.items():
                if params["label"] in p["labels"]:
                    return {"name": pl.NAME, "version": n, **p}
            raise RuntimeError(f"GET {path} → 404: not found")
        if method == "PATCH" and path.startswith(base + "/versions/"):
            n = int(path.rsplit("/", 1)[1])
            self.patches.append((n, list(body["newLabels"])))
            for label in body["newLabels"]:
                for p in self.v.values():  # labels are unique across versions
                    if label in p["labels"]:
                        p["labels"].remove(label)
                self.v[n]["labels"].append(label)
            return {}
        raise AssertionError(f"unexpected API call {method} {path}")

    def labels(self):
        return {n: sorted(p["labels"]) for n, p in self.v.items()}


class CliFlows(unittest.TestCase):
    def run_cli(self, versions, *argv, untitled=()):
        """Run prompt_label.main() against the fake API → (exit code, stdout, fake)."""
        fake = FakePromptApi(versions, untitled)
        out = io.StringIO()
        code = 0
        with mock.patch.object(pl.config, "api", fake), \
                mock.patch.object(pl.config, "_project_id", "test-project"), \
                mock.patch.object(sys, "argv", ["prompt_label.py", *argv]), \
                contextlib.redirect_stdout(out):
            try:
                pl.main()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1  # sys.exit("message") → exit 1
                if isinstance(e.code, str):
                    out.write(e.code)
        return code, out.getvalue(), fake

    def test_show_reads_only_and_warns(self):
        code, out, fake = self.run_cli(COLLISION, "--show", untitled=(6,))
        self.assertEqual(code, 0)
        self.assertEqual(fake.patches, [])
        self.assertIn("v4: production, previous-production", out)
        self.assertIn("⚠ production and previous-production are both on v4", out)
        self.assertIn("⚠ v6: untitled, unlabelled — delete it in the UI", out)

    def test_show_is_quiet_when_healthy(self):
        code, out, fake = self.run_cli(HEALTHY, "--show")
        self.assertEqual(code, 0)
        self.assertNotIn("⚠", out)

    def test_rollback_is_refused_when_labels_collide(self):
        code, out, fake = self.run_cli(COLLISION, "--rollback")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])  # no PATCH: nothing was changed
        self.assertIn("nothing to roll back to: previous-production is on the production version (v4)", out)
        self.assertIn("--promote <label> first, or --set-previous N", out)

    def test_rollback_is_refused_when_previous_production_is_missing(self):
        code, out, fake = self.run_cli({1: ["production"], 2: ["staging"]}, "--rollback")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])
        self.assertIn("no version carries previous-production", out)

    def test_rollback_swaps_the_two_labels(self):
        code, out, fake = self.run_cli(HEALTHY, "--rollback")
        self.assertEqual(code, 0)
        self.assertEqual(fake.labels()[4], ["production"])
        self.assertEqual(fake.labels()[5], ["previous-production", "staging"])
        self.assertIn("production: v4 · previous-production: v5", out)

    def test_promote_from_a_collision_separates_the_labels(self):
        code, out, fake = self.run_cli(COLLISION, "--promote", "staging")
        self.assertEqual(code, 0)
        self.assertIn("production: v5 · previous-production: v4", out)
        self.assertEqual(fake.labels()[5], ["production", "staging"])
        self.assertEqual(fake.labels()[4], ["previous-production"])

    def test_promote_to_the_current_production_exits_1(self):
        code, out, fake = self.run_cli(HEALTHY, "--promote", "production")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])
        self.assertIn("production is already v5", out)

    def test_set_production_to_a_missing_version_exits_1(self):
        code, out, fake = self.run_cli(HEALTHY, "--set-production", "99")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])
        self.assertIn("v99 does not exist", out)

    def test_set_previous_moves_the_label_and_unblocks_rollback(self):
        code, out, fake = self.run_cli(COLLISION, "--set-previous", "3")
        self.assertEqual(code, 0)
        self.assertEqual(fake.labels()[3], ["development", "previous-production"])
        self.assertEqual(fake.labels()[4], ["production"])  # left the version it was on
        self.assertIn("production: v4 · previous-production: v3", out)
        self.assertEqual(pl.rollback_target({n: p for n, p in fake.labels().items()}), 3)

    def test_set_previous_refuses_the_production_version(self):
        code, out, fake = self.run_cli(HEALTHY, "--set-previous", "5")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])
        self.assertIn("v5 is the production version", out)

    def test_set_previous_refuses_a_missing_version(self):
        code, out, fake = self.run_cli(HEALTHY, "--set-previous", "42")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])

    def test_set_previous_noop_exits_1(self):
        code, out, fake = self.run_cli(HEALTHY, "--set-previous", "4")
        self.assertEqual(code, 1)
        self.assertEqual(fake.patches, [])
        self.assertIn("already v4", out)

    def test_inconsistent_result_exits_1(self):
        # No production yet and the target already holds previous-production: the move
        # leaves both labels on one version — the post-move assertion must catch it.
        code, out, fake = self.run_cli({1: ["previous-production"], 2: ["staging"]}, "--set-production", "1")
        self.assertEqual(code, 1)
        self.assertIn("labels are inconsistent", out)


if __name__ == "__main__":
    unittest.main()

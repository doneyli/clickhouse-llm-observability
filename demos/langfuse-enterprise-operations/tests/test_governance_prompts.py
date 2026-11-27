"""reconcile_governed_prompts (workflows.py) offline: recorded versions are kept only while they
exist and match their definition; drift is replaced, unchanged runs create nothing."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import workflows as wf  # noqa: E402
from fakes import prompt  # noqa: E402


class FakeContext:
    def __init__(self, live):
        self.live, self.created, self.reads, self.notes, self.saves, self.next = live, [], [], [], 0, 10

    def call(self, label, resource, action, ids=(), body=None, query=(), env=None, expected=(200, 201)):
        if action == "get":
            self.reads.append(query)
            version = int(query[1])
            return dict(self.live[version]) if version in self.live else {"message": "Prompt not found"}
        assert (resource, action) == ("prompts", "create")
        self.next += 1
        self.created.append(body)
        return {"id": "p" + str(self.next), "version": self.next}

    def note(self, label, detail):
        self.notes.append(label)

    def save(self):
        self.saves += 1


class Reconcile(unittest.TestCase):
    def test_unchanged_versions_are_kept_and_nothing_is_created(self):
        ctx = FakeContext({1: prompt(1, 1), 2: prompt(2, 2)})
        versions = [{"id": "p1", "version": 1}, {"id": "p2", "version": 2}]
        wf.reconcile_governed_prompts(ctx, "env", versions)
        self.assertEqual((ctx.created, ctx.saves), ([], 0))
        self.assertEqual(versions, [{"id": "p1", "version": 1}, {"id": "p2", "version": 2}])
        self.assertEqual(ctx.reads, [("--version", "1"), ("--version", "2")])

    def test_text_drift_with_two_existing_versions_replaces_that_slot(self):
        live = {1: prompt(1, 1), 2: dict(prompt(2, 2), prompt="Synthetic release candidate (old wording)")}
        ctx = FakeContext(live)
        versions = [{"id": "p1", "version": 1}, {"id": "p2", "version": 2}]
        wf.reconcile_governed_prompts(ctx, "env", versions)
        self.assertEqual(ctx.created, [wf.governance_prompt(2)])
        self.assertEqual(versions, [{"id": "p1", "version": 1}, {"id": "p11", "version": 11}])
        self.assertEqual(len(ctx.notes), 1)

    def test_config_only_drift_and_deleted_versions_are_replaced(self):
        ctx = FakeContext({1: dict(prompt(1, 1), config={"temperature": 0})})
        versions = [{"id": "p1", "version": 1}, {"id": "p2", "version": 2}]
        wf.reconcile_governed_prompts(ctx, "env", versions)
        self.assertEqual(ctx.created, [wf.governance_prompt(1), wf.governance_prompt(2)])
        self.assertEqual([v["version"] for v in versions], [11, 12])

    def test_first_run_creates_both_slots_in_order(self):
        ctx = FakeContext({})
        versions = []
        wf.reconcile_governed_prompts(ctx, "env", versions)
        self.assertEqual([b["labels"] for b in ctx.created], [["production"], ["development"]])
        self.assertEqual(ctx.reads, [])

    def test_matches_ignores_labels_and_treats_missing_config_as_empty(self):
        live = dict(prompt(1, 1), labels=["production", "staging"], config=None)
        self.assertTrue(wf.prompt_matches(live, wf.governance_prompt(1)))
        self.assertFalse(wf.prompt_matches(dict(live, type="chat"), wf.governance_prompt(1)))
        self.assertFalse(wf.prompt_matches("Prompt not found", wf.governance_prompt(1)))


if __name__ == "__main__":
    unittest.main()

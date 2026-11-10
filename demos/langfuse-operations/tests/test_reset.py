import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflows import Context, WorkflowError, remove_project_key_files, reset_demo, reset_preview, validate


class ResetAPI:
    """A small API fixture with independent project inventory and organization grants."""
    def __init__(self):
        self.projects = {"owned": {"id": "owned", "name": "Demo", "metadata": {"demo": "langfuse-admin-portal"}},
                         "other": {"id": "other", "name": "Real application", "metadata": None}}
        self.members = {"demo-user": {"userId": "demo-user", "email": "demo@example.invalid", "role": "NONE"}}
        self.grants = {"other": []}
        self.deletions = []
        self.pending = False
        self.fail_project = False
        self.fail_user = False

    def call(self, label, resource, action, ids=(), expected=(200, 201), **kwargs):
        status = 200
        if (resource, action) == ("organizations", "list-projects"):
            body = {"projects": list(self.projects.values())}
        elif (resource, action) == ("organizations", "list-memberships"):
            body = {"memberships": list(self.members.values())}
        elif (resource, action) == ("projects", "list-memberships"):
            body = {"memberships": self.grants.get(ids[0], [])}
        elif (resource, action) == ("projects", "delete"):
            if self.fail_project:
                raise WorkflowError("Project deletion unavailable")
            self.deletions.append((resource, ids[0]))
            if not self.pending:
                self.projects.pop(ids[0], None)
            status, body = 202, {"success": True}
        elif (resource, action) == ("scim", "delete-user"):
            if self.fail_user:
                raise WorkflowError("Membership deletion unavailable")
            self.deletions.append((resource, ids[0]))
            self.members.pop(ids[0], None)
            body = {}
        elif (resource, action) == ("scim", "get-user"):
            status, body = (200, self.members[ids[0]]) if ids[0] in self.members else (404, {})
        else:
            raise AssertionError((resource, action, ids))
        if status not in expected:
            raise WorkflowError("Unexpected fixture response " + str(status))
        return copy.deepcopy(body)


class ResetChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ctx = Context(self.directory.name, str(Path(self.directory.name) / "org.env"), "reset-test", emit=lambda event: None)
        self.ctx.state = {"applications": {"owned": {"id": "owned", "name": "Demo", "members": []}},
                          "users": {"demo@example.invalid": {"id": "demo-user", "email": "demo@example.invalid"}}, "organizationId": None}
        self.api = ResetAPI()
        self.ctx.call = self.api.call

    def params(self):
        return {"confirmation": "RESET DEMO", "previewFingerprint": reset_preview(self.ctx)["fingerprint"]}

    def test_reset_requires_explicit_confirmation_and_preview(self):
        for params in ({}, {"confirmation": "reset demo"}, {"confirmation": "RESET DEMO", "previewFingerprint": "wrong"}):
            with self.subTest(params=params), self.assertRaises(WorkflowError):
                validate("reset", params)
        valid = validate("reset", {**self.params(), "projectId": "other"})
        self.assertNotIn("projectId", valid)

    def test_preview_is_read_only_and_scoped_to_the_manifest(self):
        preview = reset_preview(self.ctx)
        self.assertTrue(preview["canReset"])
        self.assertEqual([p["id"] for p in preview["projects"]], ["owned"])
        self.assertEqual(preview["preservedProjectCount"], 1)
        self.assertEqual(self.api.deletions, [])

    def test_missing_live_ownership_marker_blocks_all_deletion(self):
        self.api.projects["owned"]["metadata"] = None
        params = self.params()
        self.assertFalse(reset_preview(self.ctx)["canReset"])
        with self.assertRaises(WorkflowError):
            reset_demo(self.ctx, params)
        self.assertEqual(self.api.deletions, [])

    def test_stale_preview_cannot_delete_a_new_or_changed_inventory(self):
        params = self.params()
        self.ctx.state["applications"]["owned"]["name"] = "Changed after preview"
        with self.assertRaisesRegex(WorkflowError, "inventory changed"):
            reset_demo(self.ctx, params)
        self.assertEqual(self.api.deletions, [])

    def test_success_cleans_only_owned_projects_memberships_and_key_files(self):
        keys = Path(self.directory.name) / "keys"
        keys.mkdir()
        for name in ("owned-new.env", "owned-old.env", "other-key.env", "owned-other-key.env"):
            (keys / name).touch()
        org = Path(self.ctx.org_env)
        org.touch()
        result = reset_demo(self.ctx, self.params())
        self.assertEqual(result["removedProjectIds"], ["owned"])
        self.assertEqual(self.ctx.state["applications"], {})
        self.assertEqual(self.ctx.state["users"], {})
        self.assertEqual(set(self.api.projects), {"other"})
        self.assertEqual(self.api.deletions, [("projects", "owned"), ("scim", "demo-user")])
        self.assertFalse((keys / "owned-new.env").exists())
        self.assertFalse((keys / "owned-old.env").exists())
        self.assertTrue((keys / "other-key.env").exists())
        self.assertTrue((keys / "owned-other-key.env").exists())
        self.assertTrue(org.exists())
        self.assertIn("Global user accounts", result["message"])

    def test_membership_in_another_project_is_preserved(self):
        self.api.grants["other"] = [{"userId": "demo-user", "role": "VIEWER"}]
        preview = reset_preview(self.ctx)
        self.assertEqual(preview["users"], [])
        self.assertEqual(len(preview["protectedUsers"]), 1)
        reset_demo(self.ctx, self.params())
        self.assertIn("demo-user", self.api.members)
        self.assertIn("demo@example.invalid", self.ctx.state["users"])
        self.assertNotIn(("scim", "demo-user"), self.api.deletions)

    def test_elevated_organization_role_is_preserved(self):
        self.api.members["demo-user"]["role"] = "ADMIN"
        self.assertTrue(reset_preview(self.ctx)["protectedUsers"])
        reset_demo(self.ctx, self.params())
        self.assertNotIn(("scim", "demo-user"), self.api.deletions)

    def test_pending_project_retains_its_manifest_and_keys_for_retry(self):
        self.api.pending = True
        key = Path(self.directory.name) / "keys" / "owned-active.env"
        key.parent.mkdir()
        key.touch()
        with patch("workflows.time.sleep"):
            result = reset_demo(self.ctx, self.params())
        self.assertEqual(result["pendingProjectIds"], ["owned"])
        self.assertEqual(self.ctx.state["applications"]["owned"]["resetStatus"], "deletion_requested")
        self.assertTrue(key.exists())

    def test_failed_deletion_preserves_the_project_record_for_retry(self):
        self.api.fail_project = True
        with patch("workflows.time.sleep"), self.assertRaisesRegex(WorkflowError, "partially completed"):
            reset_demo(self.ctx, self.params())
        self.assertIn("owned", self.ctx.state["applications"])
        self.assertIn("owned", self.api.projects)

    def test_failed_membership_cleanup_preserves_the_user_record(self):
        self.api.fail_user = True
        with self.assertRaisesRegex(WorkflowError, "partially completed"):
            reset_demo(self.ctx, self.params())
        self.assertIn("demo@example.invalid", self.ctx.state["users"])

    def test_generated_key_cleanup_never_removes_organization_credentials(self):
        keys = Path(self.directory.name) / "keys"
        keys.mkdir()
        self.ctx.org_env = str(keys / "owned-org.env")
        Path(self.ctx.org_env).touch()
        (keys / "owned-generated.env").touch()
        self.assertEqual(remove_project_key_files(self.ctx, "owned"), 1)
        self.assertTrue(Path(self.ctx.org_env).exists())

    def test_repeated_preview_after_success_has_nothing_to_delete(self):
        reset_demo(self.ctx, self.params())
        preview = reset_preview(self.ctx)
        self.assertFalse(preview["canReset"])
        self.assertEqual(preview["projects"], [])
        self.assertEqual(preview["users"], [])


if __name__ == "__main__":
    unittest.main()

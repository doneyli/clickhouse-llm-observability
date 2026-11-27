import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflows import Context, load_state, project_setup_access, write_json


class StateMigrationChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkout = Path(temporary.name).resolve()
        self.old = self.checkout / "demos/langfuse-operations/private"
        self.new = self.checkout / "demos/langfuse-enterprise-operations/private"
        self.old.mkdir(parents=True)
        self.new.parent.mkdir(parents=True)
        root = patch("workflows.ROOT", self.new.parent)
        root.start()
        self.addCleanup(root.stop)

    def test_moved_state_keeps_project_previous_org_and_persona_credentials_usable(self):
        before = Context(self.old, self.old / "org.env", "before", emit=lambda event: None)
        app = {"id": "project", "name": "Fixture", "keyRole": "ADMIN", "keyId": "current"}
        for field, key_id in (("envFile", "current"), ("previousEnvFile", "previous")):
            app[field] = before.store_key(app, {"id": key_id, "publicKey": "fixture-public", "secretKey": "fixture-secret"})
        org = {"id": "child", "name": "Child organization"}
        org["envFile"] = before.store_key(org, {"id": "org-key", "publicKey": "fixture-public", "secretKey": "fixture-secret"})
        personas = {}
        for role in ("MEMBER", "ADMIN"):
            credential = self.old / "personas" / (role.lower() + ".env")
            credential.parent.mkdir(exist_ok=True)
            credential.write_text("DEMO_EMAIL=fixture@example.invalid\nDEMO_PASSWORD=fixture\n")
            credential.chmod(0o600)
            personas[role] = {"envFile": str(credential)}
        before.state.update(applications={"project": app}, organizations={"child": org}, governance={"personas": personas})
        before.save()
        shutil.move(str(self.old), str(self.new))
        state_path = self.new / "state.json"
        original_bytes, original_mtime = state_path.read_bytes(), state_path.stat().st_mtime_ns
        after = Context(self.new, self.new / "org.env", "after", emit=lambda event: None)

        # Exercise the consumers, not just the migrated JSON. No live API is called.
        def request(argv, **kwargs):
            if argv[0] == "node":
                credential = Path(argv[1].removeprefix("--env-file="))
            else:
                credential = Path(argv[argv.index("--env-file") + 1])
            self.assertTrue(credential.is_file(), str(credential))
            self.assertTrue(credential.is_relative_to(self.new))
            status = 401 if credential.name == "project-previous.env" else 200
            return Mock(stdout=json.dumps({"status": status, "body": {}}), stderr="", returncode=0)

        with patch("workflows.subprocess.run", side_effect=request) as run:
            current = after.managed_app("project")
            with project_setup_access(after, current) as credential:
                after.call("Read project", "prompts", "list", env=credential)
            after.call("Verify previous key rejection", "prompts", "list", env=current["previousEnvFile"], expected=(401,))
            after.call("Read child memberships", "organizations", "list-memberships", env=after.state["organizations"]["child"]["envFile"])
            for persona in after.state["governance"]["personas"].values():
                after.native("Read protected labels", "prompts.getProtectedLabels", {"projectId": "project"}, persona)
            self.assertEqual(run.call_count, 5)

        # Portal reads do not rewrite state outside the workflow lock.
        self.assertEqual(state_path.read_bytes(), original_bytes)
        self.assertEqual(state_path.stat().st_mtime_ns, original_mtime)
        after.save()
        self.assertEqual(json.loads(state_path.read_text()), after.state)
        self.assertEqual(load_state(self.new), after.state)
        self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)

    def test_custom_unrelated_relative_and_missing_paths_are_preserved(self):
        self.new.mkdir()
        external = self.checkout / "custom/keys/external.env"
        another_checkout = self.checkout / "other/demos/langfuse-operations/private/keys/external.env"
        similar_prefix = self.old.with_name("private-backup") / "keys/external.env"
        for credential in (external, another_checkout, similar_prefix, self.new / "keys/external.env"):
            credential.parent.mkdir(parents=True, exist_ok=True)
            credential.touch()
        state = {"applications": {
            "custom": {"envFile": str(external), "previousEnvFile": None},
            "other": {"envFile": str(another_checkout)},
            "similar": {"envFile": str(similar_prefix)},
            "relative": {"envFile": "keys/external.env"},
            "missing": {"envFile": str(self.old / "keys/missing.env")},
            "unrelated": {"name": str(self.old / "keys/external.env")},
        }, "users": {}, "organizationId": None}
        write_json(self.new / "state.json", state)
        self.assertEqual(load_state(self.new), state)

    def test_migration_cannot_redirect_credentials_outside_the_state_directory(self):
        self.new.mkdir()
        outside = self.new.parent / "outside.env"
        outside.touch()
        (self.new / "keys").mkdir()
        (self.new / "keys/linked.env").symlink_to(outside)
        state = {"applications": {
            "traversal": {"envFile": str(self.old / "../outside.env")},
            "symlink": {"envFile": str(self.old / "keys/linked.env")},
        }}
        write_json(self.new / "state.json", state)
        self.assertEqual(load_state(self.new), state)


if __name__ == "__main__":
    unittest.main()

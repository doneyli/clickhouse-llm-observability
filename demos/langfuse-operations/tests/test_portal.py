import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflows import Context, WorkflowError, issue_project_key, project_setup_access, public_state, rotate_key, scrub, validate, write_json


class PortalChecks(unittest.TestCase):
    def test_redaction_is_recursive_before_events_are_serialized(self):
        record = {"body": {"secretKey": "sensitive", "nested": [{"authorization": "Basic credential"}]}, "error": "sk-lf-secret-value"}
        result = json.dumps(scrub(record))
        self.assertNotIn("sensitive", result)
        self.assertNotIn("Basic credential", result)
        self.assertNotIn("sk-lf-secret-value", result)

    def test_app_retention_rejects_fractional_short_or_boolean_values(self):
        for value in (1, 2, -1, 3.5, True, "30"):
            with self.subTest(value=value), self.assertRaises(WorkflowError):
                validate("application", {"applicationName": "Demo", "teamName": "Team", "retentionDays": value})
        for value in (0, 3, 30):
            self.assertEqual(validate("application", {"applicationName": "Demo", "teamName": "Team", "retentionDays": value, "keyRole": "ADMIN"})["retentionDays"], value)

    def test_key_permissions_are_explicit_and_cannot_fall_back_to_full_access(self):
        for workflow, base in (("application", {"applicationName": "Demo", "teamName": "Team"}), ("keys", {"projectId": "demo"})):
            for role in (None, "", "OWNER", "MEMBER", {}, True):
                with self.subTest(workflow=workflow, role=role), self.assertRaises(WorkflowError):
                    validate(workflow, {**base, "keyRole": role})
            for role in ("ADMIN", "VIEWER", "INGEST", "SCORES_INGEST"):
                self.assertEqual(validate(workflow, {**base, "keyRole": role})["keyRole"], role)

    def test_unconfirmed_key_role_is_revoked_before_credentials_are_stored(self):
        ctx = Mock()
        ctx.call.side_effect = [{"id": "unverified", "role": "ADMIN"}, {}]
        with self.assertRaises(WorkflowError):
            issue_project_key(ctx, {"id": "project"}, "VIEWER", "Create", "Reporting")
        self.assertEqual(ctx.call.call_args_list[0].kwargs["body"]["role"], "VIEWER")
        self.assertEqual(ctx.call.call_args_list[1].args[2:4], ("delete-api-key", ("project", "unverified")))
        ctx.store_key.assert_not_called()

    def test_temporary_setup_key_is_revoked_even_when_setup_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "temporary.env"
            env.touch()
            ctx = Mock()
            app = {"id": "project", "name": "Demo", "keyRole": "INGEST"}
            with patch("workflows.issue_project_key", return_value=({"id": "temporary"}, str(env))):
                with self.assertRaisesRegex(WorkflowError, "setup failed"):
                    with project_setup_access(ctx, app):
                        raise WorkflowError("setup failed")
            self.assertEqual(ctx.call.call_args.args[2:4], ("delete-api-key", ("project", "temporary")))
            self.assertEqual(app["pendingSetupKeyIds"], [])
            self.assertFalse(env.exists())

    def test_failed_replacement_check_preserves_the_current_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "replacement.env"
            env.touch()
            app = {"id": "project", "name": "Demo", "keyId": "current", "envFile": "/current.env"}
            ctx = Mock()
            ctx.managed_app.return_value = app
            with patch("workflows.issue_project_key", return_value=({"id": "replacement"}, str(env))), patch("workflows.verify_project_key", side_effect=WorkflowError("verification failed")):
                with self.assertRaises(WorkflowError):
                    rotate_key(ctx, {"projectId": "project", "keyRole": "INGEST"})
            self.assertEqual(app["keyId"], "current")
            self.assertEqual(ctx.call.call_args.args[2:4], ("delete-api-key", ("project", "replacement")))
            ctx.save.assert_not_called()

    def test_bulk_creation_is_bounded(self):
        with self.assertRaises(WorkflowError):
            validate("application", {"applicationName": "Demo", "teamName": "Team", "applicationCount": 100})

    def test_project_name_limits_include_the_batch_suffix(self):
        base={"applicationName":"x"*60,"teamName":"Team","keyRole":"ADMIN"}
        self.assertEqual(validate("application",base)["applicationName"],base["applicationName"])
        for params in ({**base,"applicationCount":2},{**base,"applicationName":"ab"}):
            with self.assertRaises(WorkflowError):validate("application",params)

    def test_team_rejects_duplicate_emails_and_unrecognized_roles(self):
        for members in ([{"email": "demo@example.invalid", "role": "MEMBER"}, {"email": "DEMO@example.invalid", "role": "VIEWER"}], [{"email": "demo@example.invalid", "role": "ROOT"}], ["not-an-object"]):
            with self.assertRaises(WorkflowError):
                validate("team", {"projectId": "demo", "members": members})

    def test_browser_state_never_contains_credential_file_paths(self):
        result = public_state({"applications": {"a": {"id": "a", "name": "App", "envFile": "/private/secret.env", "previousEnvFile": "/private/old.env"}}})
        self.assertNotIn(".env", json.dumps(result))

    def test_unmanaged_project_cannot_be_selected_for_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            context = Context(directory, "/unused.env", "test", emit=lambda event: None)
            with self.assertRaises(WorkflowError):
                context.managed_app("existing-unmanaged-project")

    def test_state_files_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_json(path, {"applications": {}})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cli_arguments_preserve_literal_user_values(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            context = Context(directory, "/org.env", "test", emit=events.append)
            payload = {"name": "Treasury's $(not-a-command) app", "retention": 30, "secretKey": "request-secret-value"}
            response = type("Response", (), {"stdout": json.dumps({"status": 200, "body": {"id": "created", "secretKey": "secret-value"}}), "stderr": "", "returncode": 0})()
            with patch("workflows.subprocess.run", return_value=response) as run:
                context.call("Create", "projects", "create", body=payload)
                args = run.call_args.args[0]
                self.assertEqual(json.loads(args[args.index("--body-json") + 1]), payload)
                self.assertNotIn("shell", run.call_args.kwargs)
            self.assertNotIn("secret-value", json.dumps(events))


if __name__ == "__main__":
    unittest.main()

"""verify_demo.py offline: verdicts, the credential-file probe, every live governance read-back, the
declared organization's ownership, and full runs against a fake CLI and adapter. No network,
Langfuse, Node or Docker."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import verify_demo as v  # noqa: E402
import workflows as wf  # noqa: E402
from fakes import Completed, FakeLangfuse, ready_state  # noqa: E402


def failed(checks, required=True):
    return [what for ok, what, req, _ in checks if not ok and req == required]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.private = Path(self.tmp.name).resolve()
        self.state = ready_state(self.private)
        self.fake = FakeLangfuse(self.private)
        self.org_env = self.private / "org.env"
        del v.RESULTS[:]

    def tearDown(self):
        self.tmp.cleanup()

    def projects(self):
        return {p["id"]: p for p in self.fake.projects}

    def governance(self):
        return v.governance_checks(self.state, self.projects(), self.org_env, self.fake)


class Results(unittest.TestCase):
    def setUp(self):
        del v.RESULTS[:]

    def test_check_records_pass_fail_and_warn(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(v.check(True, "a"))
            self.assertFalse(v.check(False, "b"))
            self.assertFalse(v.check(False, "c", required=False, hint="do x"))
        self.assertEqual([s for s, _, _ in v.RESULTS], ["PASS", "FAIL", "WARN"])
        self.assertTrue(out.getvalue().splitlines()[2].endswith("->  do x"))

    def test_summary_ready_only_when_no_required_check_fails(self):
        self.assertEqual(v.summary([("PASS", True, "a"), ("WARN", False, "b")]), ("READY (1/2 checks passed, 1 warning)", 0))
        self.assertEqual(v.summary([("PASS", True, "a"), ("FAIL", True, "b"), ("WARN", False, "c"), ("WARN", False, "d")]),
                         ("NOT READY (1/4 checks passed, 1 required failed, 2 warnings)", 1))
        self.assertEqual(v.summary([])[1], 1)

    def test_record_is_false_only_for_required_failures(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(v.record([(True, "a", True, ""), (False, "b", False, "")]))
            self.assertFalse(v.record([(False, "c", True, ""), (True, "d", True, "")]))
        self.assertEqual([s for s, _, _ in v.RESULTS], ["PASS", "WARN", "FAIL", "PASS"])


class Constants(unittest.TestCase):
    def test_pins_match_the_code_they_mirror(self):
        self.assertIn("'" + v.SERVER_VERSION + "'", (ROOT / "scripts" / "platform-api.mjs").read_text())
        source = (ROOT / "workflows.py").read_text()
        for value in (v.PRESENTER_EMAIL, v.PROJECT_MARKER, v.ORG_MARKER, v.GOVERNANCE_PROMPT):
            self.assertIn('"' + value + '"', source)
        self.assertIn("default=8091", (ROOT / "server.py").read_text())

    def test_declared_target_is_preserved(self):
        text = (ROOT / "demo.yaml").read_text()
        self.assertEqual(v.manifest_value(text, "langfuse", "project"), "Operations Demo Organization")
        self.assertIn('value="Operations Demo Organization"', (ROOT / "public" / "index.html").read_text())
        self.assertEqual(v.manifest_value(text, "", "profile"), "standalone")

    def test_major_version(self):
        self.assertEqual(v.major_version("v24.13.0"), 24)
        self.assertIsNone(v.major_version(""))


class EnvFiles(Fixture):
    def test_present_file_with_every_name_passes(self):
        self.assertTrue(v.env_check(str(self.org_env), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"), "org", self.fake)[0])

    def test_missing_file_placeholder_and_unloadable_fail(self):
        self.assertFalse(v.env_check(str(self.private / "nope.env"), ("A",), "x", self.fake)[0])
        self.assertFalse(v.env_check(None, ("A",), "x", self.fake)[0])
        self.fake.missing_names[str(self.org_env)] = ["LANGFUSE_SECRET_KEY"]
        ok, what, required, _ = v.env_check(str(self.org_env), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"), "org", self.fake)
        self.assertEqual((ok, required), (False, True))
        self.assertIn("missing LANGFUSE_SECRET_KEY", what)
        self.fake.unloadable.add(str(self.org_env))
        self.assertIn("could not load", v.env_check(str(self.org_env), ("A",), "org", self.fake)[1])

    def test_hostile_shell_exports_never_reach_a_child(self):
        hostile = {"LANGFUSE_PUBLIC_KEY": "pk-other", "LANGFUSE_SECRET_KEY": "sk-other",
                   "LANGFUSE_HOST": "https://cloud.example", "LANGFUSE_BASE_URL": "https://cloud.example", "LANGFUSE_CLI_BIN": "langfuse"}
        with patch.dict(os.environ, hostile):
            v.clear_shell_keys()
            self.assertEqual([k for k in os.environ if k.startswith("LANGFUSE_")], ["LANGFUSE_CLI_BIN"])
            os.environ["LANGFUSE_SECRET_KEY"] = "re-exported"
            self.assertNotIn("LANGFUSE_SECRET_KEY", v.child_env())
            # The fake asserts the probe's child environment lacks the names it checks.
            self.assertTrue(v.env_check(str(self.org_env), ("LANGFUSE_SECRET_KEY",), "org", self.fake)[0])


class Governance(Fixture):
    def test_ready_governance_reads_back_every_prerequisite(self):
        checks = self.governance()
        self.assertEqual(failed(checks), [], checks)
        reads = [c[6:8] + c[8:-1] for c in self.fake.api_calls() if c[0] == str(wf.WRAPPER)]
        self.assertIn(["projects", "list-memberships", "gov"], reads)
        self.assertIn(["prompts", "get", wf.GOVERNANCE_PROMPT, "--version", "1"], reads)
        self.assertIn(["prompts", "get", wf.GOVERNANCE_PROMPT, "--label", "production"], reads)
        self.assertTrue(any(c[0] == "node" and "-e" not in c for c in self.fake.api_calls()), "policy not read")

    def test_no_governance_recorded(self):
        self.state.pop("governance")
        self.assertEqual(len(failed(self.governance())), 1)

    def test_missing_application_record_fails_instead_of_skipping(self):
        self.state["applications"].pop("gov")
        self.assertIn("governance application record in private/state.json", failed(self.governance()))

    def test_missing_key_file_fails_and_skips_prompt_reads(self):
        Path(self.state["applications"]["gov"]["envFile"]).unlink()
        checks = self.governance()
        self.assertTrue(any("governance project key" in what for what in failed(checks)))
        self.assertFalse([c for c in self.fake.api_calls() if "prompts" in c])

    def test_missing_persona_file_fails_and_skips_the_policy_read(self):
        Path(self.state["governance"]["personas"]["ADMIN"]["envFile"]).unlink()
        self.assertTrue(any("Admin persona sign-in" in what for what in failed(self.governance())))
        self.assertFalse([c for c in self.fake.api_calls() if c[0] == "node" and "-e" not in c])

    def test_cached_registered_flag_without_a_scim_user_fails(self):
        self.state["users"].pop("m@example.invalid")
        self.assertTrue(any("Member persona recorded" in what for what in failed(self.governance())))

    def test_wrong_or_missing_membership_fails(self):
        self.fake.project_members["gov"] = [{"userId": "u-admin", "role": "ADMIN"}, {"userId": "u-member", "role": "VIEWER"}]
        self.assertTrue(any("Member persona has project role MEMBER (VIEWER)" in w for w in failed(self.governance())))
        self.fake.project_members["gov"] = [{"userId": "u-admin", "role": "ADMIN"}]
        self.assertTrue(any("(None)" in w for w in failed(self.governance())))

    def test_unreadable_memberships_fail_both_roles(self):
        self.fake.status[("projects", "list-memberships")] = 500
        self.assertEqual(len([w for w in failed(self.governance()) if "read failed: 500" in w]), 2)

    def test_policy_without_production_or_unreadable_fails(self):
        self.fake.policy = ["staging"]
        self.assertTrue(any("protected-label policy" in w for w in failed(self.governance())))
        self.fake.policy = ["production"]
        self.fake.status[("adapter", "rpc")] = "timeout"
        self.assertTrue(any("protected-label policy" in w for w in failed(self.governance())))

    def test_prompt_text_or_config_drift_with_two_recorded_versions_fails(self):
        self.fake.prompts[2]["prompt"] = "Edited by hand"
        self.assertTrue(any("slot 2 definition" in w for w in failed(self.governance())))
        self.fake.prompts[2]["prompt"] = wf.governance_prompt(2)["prompt"]
        self.fake.prompts[1]["config"] = {"temperature": 0}
        self.assertTrue(any("slot 1 definition" in w for w in failed(self.governance())))

    def test_deleted_version_and_unrecorded_production_fail(self):
        del self.fake.prompts[1]
        self.assertTrue(any("v1 matches the slot 1 definition (404)" in w for w in failed(self.governance())))
        self.fake.prompts[1] = {**self.fake.prompts[2], "version": 1}
        self.fake.prompts[1].update({k: wf.governance_prompt(1)[k] for k in ("prompt",)})
        self.fake.prompts[7] = dict(self.fake.prompts[2], version=7)
        self.fake.production = 7
        self.assertTrue(any("production = v7" in w for w in failed(self.governance())))

    def test_one_recorded_version_fails(self):
        self.state["governance"]["versions"] = self.state["governance"]["versions"][:1]
        self.assertIn(f"{wf.GOVERNANCE_PROMPT} slot 2 recorded", failed(self.governance()))

    def test_unlisted_or_unmarked_project_fails(self):
        self.fake.projects[0]["metadata"] = {}
        self.assertTrue(any("listed with metadata.demo" in w for w in failed(self.governance())))
        self.fake.projects = self.fake.projects[1:]
        self.assertTrue(any("not in the organization" in w for w in failed(self.governance())))

    def test_fake_rejects_undeclared_query_parameters(self):
        with self.assertRaisesRegex(AssertionError, "invalid query parameter --bogus"):
            v.cli(self.org_env, "prompts", "get", wf.GOVERNANCE_PROMPT, "--bogus", "1", runner=self.fake)

    def test_other_applications_only_warn(self):
        self.fake.projects[1]["metadata"] = {}
        self.state["organizations"]["org9"]["resetStatus"] = "deletion_requested"
        checks = v.application_checks(self.state, self.projects())
        self.assertEqual(failed(checks), [])
        self.assertEqual(len(failed(checks, required=False)), 2)


class DeclaredOrganization(Fixture):
    def org(self):
        return v.declared_org_checks(self.state, "Operations Demo Organization", self.private / "instance.env", self.fake)

    def test_recorded_and_owned_passes(self):
        self.assertEqual([what for ok, what, _, _ in self.org() if not ok], [])

    def test_not_provisioned_only_warns(self):
        self.state["organizations"] = {}
        checks = self.org()
        self.assertEqual(failed(checks), [])
        self.assertIn("declared organization 'Operations Demo Organization' provisioned", failed(checks, required=False))

    def test_missing_marker_wrong_name_or_missing_starter_fails(self):
        for change in ({"metadata": {}}, {"name": "Renamed"}, {"projects": []}):
            with self.subTest(change=change):
                fake = FakeLangfuse(self.private)
                fake.instance_orgs["org9"].update(change)
                checks = v.declared_org_checks(self.state, "Operations Demo Organization", self.private / "instance.env", fake)
                self.assertTrue(any("owned" in w for w in failed(checks)))

    def test_recorded_without_instance_key_or_unreadable_fails(self):
        self.fake.status[("adapter", "instance")] = 500
        self.assertTrue(any("owned" in w for w in failed(self.org())))
        (self.private / "instance.env").unlink()
        self.assertTrue(any("instance key" in w for w in failed(self.org())))


class FullRun(Fixture):
    def run_main(self, http=None):
        calls = []

        def default_http(url, headers=None, timeout=5):
            calls.append(url)
            return (200, {"version": "4.76.0"}) if url.endswith("/health") else (200, {"mode": "live"})

        wf.write_json(self.private / "state.json", self.state)
        with patch.object(v, "http_json", http or default_http), contextlib.redirect_stdout(io.StringIO()) as out:
            code = v.main(["--state-dir", str(self.private), "--links"], runner=self.fake)
        return code, out.getvalue(), calls

    def test_ready_when_everything_reads_back(self):
        code, out, _ = self.run_main()
        self.assertEqual(code, 0, out)
        self.assertIn("\nREADY (", out)
        self.assertNotIn("FAIL", out)
        self.assertIn("Act 4 · governed prompt", out)

    def test_missing_org_env_fails_before_any_api_use(self):
        self.org_env.unlink()
        code, out, http_calls = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual((http_calls, self.fake.api_calls()), ([], []))
        self.assertIn("NOT READY", out)

    def test_placeholder_org_env_fails_before_any_api_use(self):
        self.fake.missing_names[str(self.org_env)] = ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
        code, out, http_calls = self.run_main()
        self.assertEqual((code, http_calls, self.fake.api_calls()), (1, [], []))

    def test_missing_node_fails_before_any_api_use(self):
        self.fake.node = ""
        code, _, http_calls = self.run_main()
        self.assertEqual((code, http_calls), (1, []))

    def test_corrupt_state_file_is_a_fail_not_a_traceback(self):
        wf.write_json(self.private / "state.json", self.state)
        with patch.object(wf, "load_state", side_effect=json.JSONDecodeError("bad", "", 0)):
            code, out, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("private/state.json: unexpected JSONDecodeError", out)

    def test_subprocess_network_and_http_failures_reach_the_verdict(self):
        for key, value in ((("organizations", "list-projects"), "timeout"), (("organizations", "list-projects"), "empty"),
                           (("organizations", "list-projects"), 401), (("scim", "get-service-provider-config"), 403)):
            with self.subTest(key=key, value=value):
                self.fake = FakeLangfuse(self.private)
                self.fake.status[key] = value
                code, out, _ = self.run_main()
                self.assertEqual(code, 1, out)

    def test_langfuse_down_or_wrong_version_or_portal_down(self):
        for health, portal in (((None, "URLError"), (200, {"mode": "live"})), ((200, {"version": "4.80.0"}), (200, {"mode": "live"})),
                               ((200, {"version": "4.76.0"}), (None, "URLError"))):
            with self.subTest(health=health, portal=portal):
                code, _, _ = self.run_main(lambda url, headers=None, timeout=5: health if url.endswith("/health") else portal)
                self.assertEqual(code, 1)

    def test_hostile_shell_exports_are_cleared(self):
        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-other", "LANGFUSE_HOST": "https://cloud.example"}):
            code, out, _ = self.run_main()
            self.assertNotIn("LANGFUSE_PUBLIC_KEY", os.environ)
        self.assertEqual(code, 0, out)

    def test_cli_timeout_returns_no_status(self):
        def timeout(*a, **k):
            raise subprocess.TimeoutExpired("cli", 1)

        self.assertIsNone(v.cli(self.org_env, "organizations", "list-projects", runner=timeout)[0])
        self.assertIsNone(v.cli(self.org_env, "organizations", "list-projects", runner=lambda *a, **k: Completed("", "boom"))[0])


if __name__ == "__main__":
    unittest.main()

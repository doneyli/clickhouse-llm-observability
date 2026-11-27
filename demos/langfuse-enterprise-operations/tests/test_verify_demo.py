"""verify_demo.py offline: results and exit code, the manifest checks, the CLI and adapter calls,
the act links, and a full run against fakes. No network, no Langfuse, no Node, no Docker."""
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
import verify_demo as v  # noqa: E402
import workflows as wf  # noqa: E402


class Completed:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def ready_state(tmp):
    """A manifest as the governance and application workflows leave it."""
    gov_env = Path(tmp) / "keys" / "gov-1.env"
    return {
        "applications": {
            "gov": {"id": "gov", "name": "Governance Demo", "envFile": str(gov_env), "keyRole": "ADMIN"},
            "app1": {"id": "app1", "name": "Support assistant", "envFile": str(Path(tmp) / "keys" / "app1-1.env")},
        },
        "users": {},
        "organizationId": None,
        "governance": {"projectId": "gov", "versions": [{"id": "p1", "version": 1}, {"id": "p2", "version": 2}],
                       "personas": {"MEMBER": {"email": "m@example.invalid", "registered": True, "envFile": "x"},
                                    "ADMIN": {"email": "a@example.invalid", "registered": True, "envFile": "y"}}},
        "organizations": {"org9": {"id": "org9", "name": "Operations Demo Organization", "projectId": "starter"}},
    }


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


class Constants(unittest.TestCase):
    def test_pins_match_the_code_they_mirror(self):
        self.assertIn("'" + v.SERVER_VERSION + "'", (ROOT / "scripts" / "platform-api.mjs").read_text())
        source = (ROOT / "workflows.py").read_text()
        self.assertIn('"' + v.PRESENTER_EMAIL + '"', source)
        self.assertIn('"' + v.GOVERNANCE_PROMPT + '"', source)
        self.assertIn('default=8091', (ROOT / "server.py").read_text())

    def test_manifest_names_the_organization_the_key_belongs_to(self):
        text = (ROOT / "demo.yaml").read_text()
        self.assertEqual(v.manifest_value(text, "langfuse", "project"), "Demo Organization")
        self.assertEqual(v.manifest_value(text, "", "profile"), "standalone")

    def test_major_version(self):
        self.assertEqual(v.major_version("v24.13.0"), 24)
        self.assertEqual(v.major_version("22.1"), 22)
        self.assertIsNone(v.major_version(""))


class ManifestChecks(unittest.TestCase):
    def test_ready_manifest_passes_every_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = v.manifest_checks(ready_state(tmp), {"gov", "app1"})
        self.assertTrue(all(ok for ok, _, _, _ in results), results)

    def test_empty_manifest_fails_the_required_governance_checks(self):
        results = v.manifest_checks({"applications": {}}, set())
        failed_required = [what for ok, what, required, _ in results if not ok and required]
        self.assertEqual(len(failed_required), 3)
        self.assertTrue(all(ok for ok, _, required, _ in results if not required))

    def test_stale_application_and_pending_org_only_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ready_state(tmp)
            state["organizations"]["org9"]["resetStatus"] = "deletion_requested"
            results = v.manifest_checks(state, {"gov"})
        failed = [(what, required) for ok, what, required, _ in results if not ok]
        self.assertEqual([r for _, r in failed], [False, False])
        self.assertIn("0 of 1", failed[0][0])

    def test_unregistered_persona_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ready_state(tmp)
            state["governance"]["personas"]["MEMBER"]["registered"] = False
            ok = v.manifest_checks(state, {"gov", "app1"})[1][0]
        self.assertFalse(ok)

    def test_governance_version_and_org_name(self):
        gov = {"versions": [{"version": 1}, {"version": 2}]}
        self.assertTrue(v.governance_version_ok({"version": 2}, gov))
        self.assertFalse(v.governance_version_ok({"version": 3}, gov))
        self.assertFalse(v.governance_version_ok("not found", gov))
        orgs = [{"name": "Other", "projects": [{"id": "x"}]}, {"name": "Demo Organization", "projects": [{"id": "gov"}]}]
        self.assertEqual(v.org_name_for(orgs, {"gov"}), "Demo Organization")
        self.assertIsNone(v.org_name_for(orgs, {"missing"}))

    def test_links_follow_act_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            links = v.act_links("http://localhost:3001", "http://127.0.0.1:8091", ready_state(tmp))
        labels = [label for label, _ in links]
        self.assertEqual(labels[:3], ["Portal (every act)", "Langfuse", "Act 1 · Support assistant"])
        self.assertIn(("Act 4 · governed prompt", "http://localhost:3001/project/gov/prompts/governance-demo%2Frelease"), links)
        self.assertIn(("Act 5 · native audit log", "http://localhost:3001/project/gov/settings/audit-logs"), links)
        self.assertEqual(labels[-1], "Act 6 · Operations Demo Organization")


class Calls(unittest.TestCase):
    def test_cli_uses_the_wrapper_and_strips_shell_keys(self):
        seen = {}

        def runner(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return Completed(json.dumps({"status": 200, "body": {"projects": []}}))

        with patch.dict(os.environ, {"LANGFUSE_PUBLIC_KEY": "pk-shell", "LANGFUSE_HOST": "https://other"}):
            status, body = v.cli(Path("/x/org.env"), "organizations", "list-projects", runner=runner)
        self.assertEqual((status, body), (200, {"projects": []}))
        self.assertEqual(seen["argv"][:5], [str(wf.WRAPPER), "--env-file", "/x/org.env", "--host", wf.HOST])
        self.assertEqual(seen["argv"][-1], "--json")
        self.assertFalse([k for k in seen["env"] if k.startswith("LANGFUSE_") and k != "LANGFUSE_CLI_BIN"])

    def test_cli_failures_return_no_status(self):
        def timeout(*a, **k):
            raise subprocess.TimeoutExpired("cli", 1)

        self.assertIsNone(v.cli(Path("e"), "a", "b", runner=timeout)[0])
        self.assertEqual(v.cli(Path("e"), "a", "b", runner=lambda *a, **k: Completed("", "boom"))[0], None)

    def test_instance_get_hides_secrets_from_the_child(self):
        seen = {}

        def runner(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return Completed(json.dumps({"status": 200, "body": {"organizations": []}}))

        with patch.dict(os.environ, {"ADMIN_API_KEY": "secret"}):
            status, _ = v.instance_get(Path("/x/instance.env"), "/api/admin/organizations", runner)
        self.assertEqual(status, 200)
        self.assertEqual(seen["argv"][:2], ["node", "--env-file=/x/instance.env"])
        self.assertNotIn("ADMIN_API_KEY", seen["env"])
        self.assertEqual(json.loads(seen["input"])["mode"], "instance")


class FullRun(unittest.TestCase):
    """main() end to end, with the CLI, Node and HTTP replaced by fakes."""

    def run_main(self, tmp, state, with_org_env=True):
        private = Path(tmp)
        if with_org_env:
            (private / "org.env").write_text("placeholder\n")
            (private / "org.env").chmod(0o600)
            (private / "instance.env").write_text("placeholder\n")
        wf.write_json(private / "state.json", state)

        def runner(argv, **kwargs):
            if argv[:2] == ["node", "--version"]:
                return Completed("v24.13.0\n")
            if argv[0] == "node":
                path = json.loads(kwargs["input"])["path"]
                body = {"organizations": [{"id": "demo-org", "name": "Demo Organization", "projects": [{"id": "gov"}]}]} \
                    if path == "/api/admin/organizations" else {}
                return Completed(json.dumps({"status": 200, "body": body}))
            resource, action = argv[6], argv[7]
            bodies = {("organizations", "list-projects"): {"projects": [{"id": "gov"}, {"id": "app1"}]},
                      ("scim", "get-service-provider-config"): {},
                      ("organizations", "list-memberships"): {"memberships": [{"email": "demo@example.com"}]},
                      ("prompts", "get"): {"version": 2}}
            return Completed(json.dumps({"status": 200, "body": bodies[(resource, action)]}))

        def http(url, headers=None, timeout=5):
            return (200, {"version": "4.76.0"}) if url.endswith("/health") else (200, {"mode": "live"})

        with patch.object(v, "http_json", http), contextlib.redirect_stdout(io.StringIO()) as out:
            code = v.main(["--state-dir", tmp, "--links"], runner=runner)
        return code, out.getvalue()

    def test_ready_when_everything_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(tmp, ready_state(tmp))
        self.assertEqual(code, 0, out)
        self.assertIn("READY (", out)
        self.assertNotIn("FAIL", out)
        self.assertIn("organization key belongs to 'Demo Organization'", out)
        self.assertIn("Act 4 · governed prompt", out)

    def test_not_ready_without_the_organization_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = self.run_main(tmp, {"applications": {}}, with_org_env=False)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  organization key file", out)
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("NOT READY"))


if __name__ == "__main__":
    unittest.main()

"""seed_all.py offline: private/ setup, the live drift check that decides whether to write, the
identity-based report, workflow failure propagation and the post-run re-check. Fake CLI, adapter and
workflow process; no network, Langfuse, Node or Docker."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import seed_all as s  # noqa: E402
import workflows as wf  # noqa: E402
from fakes import FakeLangfuse, prompt, ready_state  # noqa: E402


def snapshot(versions=((1, "p1"), (2, "p2")), personas=("ADMIN", "MEMBER")):
    return {"projectId": "gov", "projectName": "Governance Demo",
            "personas": {r: r.lower() + "@example.invalid" for r in personas},
            "versions": [(pid, n) for n, pid in versions]}


class Report(unittest.TestCase):
    def test_first_run_reports_everything_created(self):
        before = s.governance_snapshot({"applications": {}})
        statuses = [st for st, _ in s.report(before, snapshot(), proof_ran=True)]
        self.assertEqual(statuses, ["created"] * 5 + ["updated"])

    def test_unchanged_reseed_reports_unchanged_and_no_proof(self):
        statuses = [st for st, _ in s.report(snapshot(), snapshot(), proof_ran=False)]
        self.assertEqual(statuses, ["unchanged"] * 5)

    def test_drifted_definition_reports_updated_by_identity(self):
        lines = s.report(snapshot(), snapshot(versions=((1, "p1"), (3, "p3"))), proof_ran=True)
        self.assertIn(("updated", f"prompt {wf.GOVERNANCE_PROMPT} slot 2: v2 -> v3 (definition drifted)"), lines)
        self.assertEqual(lines[3][0], "unchanged")

    def test_failed_first_run_reports_nothing_invented(self):
        empty = s.governance_snapshot({"applications": {}})
        self.assertEqual(s.report(empty, empty, proof_ran=False), [])

    def test_governance_name_reuses_the_recorded_project(self):
        state = {"applications": {"gov": {"name": "Governance Demo"}}, "governance": {"projectId": "gov"}}
        self.assertEqual(s.governance_name(state, None), "Governance Demo")
        self.assertEqual(s.governance_name({"applications": {}}, None), s.DEFAULT_GOVERNANCE_NAME)
        self.assertEqual(s.governance_name({"governance": {"pendingName": "Half made"}}, None), "Half made")
        self.assertEqual(s.governance_name(state, "Explicit"), "Explicit")

    def test_event_lines(self):
        self.assertIn("Create governed prompt", s.event_line({"type": "request", "label": "Create governed prompt", "method": "POST", "path": "/p"}))
        self.assertEqual(s.event_line({"type": "response", "ok": False, "status": 403}).strip(), "FAILED HTTP 403")
        self.assertIsNone(s.event_line({"type": "complete"}))


class Private(unittest.TestCase):
    def test_missing_org_env_is_copied_from_the_example_and_stops_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "private"
            lines, present = s.ensure_private(state_dir, state_dir / "org.env")
            self.assertFalse(present)
            self.assertEqual([st for st, _ in lines], ["created", "created"])
            self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual((state_dir / "org.env").stat().st_mode & 0o777, 0o600)
            self.assertEqual((state_dir / "org.env").read_text(), (ROOT / "org.env.example").read_text())

    def test_loose_modes_are_tightened_then_left_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            state_dir.chmod(0o755)
            (state_dir / "org.env").write_text("x\n")
            (state_dir / "org.env").chmod(0o644)
            first, present = s.ensure_private(state_dir, state_dir / "org.env")
            second, _ = s.ensure_private(state_dir, state_dir / "org.env")
        self.assertTrue(present)
        self.assertEqual([st for st, _ in first], ["updated", "updated"])
        self.assertEqual([st for st, _ in second], ["unchanged", "unchanged"])


class FakeWorkflow:
    """Stands in for subprocess.Popen(scripts/protected-labels.sh ...)."""

    def __init__(self, events, code=0, effect=None):
        self.events, self.code, self.effect, self.commands = events, code, effect, []

    def __call__(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if self.effect:
            self.effect(command)
        outer = self

        class Process:
            stdout = iter(json.dumps(e) + "\n" for e in outer.events)

            def wait(self):
                return outer.code

        return Process()


class Workflow(unittest.TestCase):
    def test_runs_the_portal_script_with_a_params_file(self):
        runner = FakeWorkflow([{"type": "request", "label": "x", "method": "GET", "path": "/"},
                               {"type": "complete", "result": {"message": "Protected production verified."}}])
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            ok, message = s.run_governance(Path(tmp), Path(tmp) / "org.env", "Governance Demo", runner)
            command, kwargs = runner.commands[0]
            params = Path(command[command.index("--params-file") + 1])
            self.assertEqual(json.loads(params.read_text()), {"governanceName": "Governance Demo"})
        self.assertEqual((ok, message), (True, "Protected production verified."))
        self.assertTrue(command[0].endswith("scripts/protected-labels.sh"))
        self.assertFalse([k for k in kwargs["env"] if k.startswith("LANGFUSE_") and k != "LANGFUSE_CLI_BIN"])

    def test_error_nonzero_exit_and_start_failure_fail_the_seed(self):
        cases = [FakeWorkflow([{"type": "error", "message": "[Errno 35] Resource temporarily unavailable"}], code=1),
                 FakeWorkflow([{"type": "complete", "result": {}}], code=1),
                 FakeWorkflow([{"type": "complete", "result": {}}, {"type": "error", "message": "late"}])]
        for runner in cases:
            with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(s.run_governance(Path(tmp), Path(tmp) / "org.env", "G", runner)[0])

        def broken(*a, **k):
            raise OSError("exec format error")

        with tempfile.TemporaryDirectory() as tmp:
            ok, message = s.run_governance(Path(tmp), Path(tmp) / "org.env", "G", broken)
        self.assertFalse(ok)
        self.assertIn("could not start", message)


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.private = Path(self.tmp.name).resolve()
        self.state = ready_state(self.private)
        wf.write_json(self.private / "state.json", self.state)
        self.fake = FakeLangfuse(self.private)

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, workflow, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = s.main(["--state-dir", str(self.private), *extra], cli_runner=self.fake, popen=workflow)
        return code, out.getvalue()

    def test_unchanged_reseed_writes_nothing(self):
        workflow = FakeWorkflow([])
        code, out = self.run_main(workflow)
        self.assertEqual(code, 0, out)
        self.assertEqual(workflow.commands, [])
        self.assertIn("nothing written", out)
        self.assertNotIn("created", out)
        self.assertEqual(json.loads((self.private / "state.json").read_text()), self.state)

    def test_reprove_runs_the_workflow_even_when_unchanged(self):
        workflow = FakeWorkflow([{"type": "complete", "result": {"message": "verified"}}])
        code, out = self.run_main(workflow, "--reprove")
        self.assertEqual((code, len(workflow.commands)), (0, 1), out)
        self.assertIn("Admin promoted", out)

    def test_changed_definition_runs_the_workflow_and_rechecks(self):
        self.fake.prompts[2]["prompt"] = "An older definition"

        def reconcile(command):  # what reconcile_governed_prompts does: a new version replaces slot 2
            state = json.loads((self.private / "state.json").read_text())
            state["governance"]["versions"][1] = {"id": "p3", "version": 3}
            wf.write_json(self.private / "state.json", state)
            self.fake.prompts[3] = prompt(3, 2)
            self.fake.production = 3

        workflow = FakeWorkflow([{"type": "complete", "result": {"message": "verified"}}], effect=reconcile)
        code, out = self.run_main(workflow)
        self.assertEqual(code, 0, out)
        self.assertIn("missing   " + wf.GOVERNANCE_PROMPT + " v2 matches the slot 2 definition", out)
        self.assertIn("slot 2: v2 -> v3 (definition drifted)", out)

    def test_run_that_leaves_drift_fails(self):
        self.fake.prompts[2]["prompt"] = "An older definition"
        workflow = FakeWorkflow([{"type": "complete", "result": {"message": "verified"}}])
        code, out = self.run_main(workflow)
        self.assertEqual(code, 1)
        self.assertIn("FAILED    " + wf.GOVERNANCE_PROMPT + " v2 matches", out)

    def test_partial_workflow_failure_propagates(self):
        self.fake.policy = []
        workflow = FakeWorkflow([{"type": "request", "label": "Protect the production label as Admin", "method": "POST", "path": "/"},
                                 {"type": "error", "message": "Platform adapter: 403"}], code=1)
        code, out = self.run_main(workflow)
        self.assertEqual(code, 1)
        self.assertIn("FAILED    Platform adapter: 403", out)

    def test_bad_key_file_or_inventory_stops_before_any_write(self):
        self.fake.missing_names[str(self.private / "org.env")] = ["LANGFUSE_SECRET_KEY"]
        workflow = FakeWorkflow([])
        self.assertEqual(self.run_main(workflow)[0], 1)
        self.fake.missing_names = {}
        self.fake.status[("organizations", "list-projects")] = 401
        self.assertEqual(self.run_main(workflow)[0], 1)
        self.assertEqual(workflow.commands, [])


if __name__ == "__main__":
    unittest.main()

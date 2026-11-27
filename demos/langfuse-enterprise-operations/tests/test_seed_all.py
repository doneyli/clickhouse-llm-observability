"""seed_all.py offline: the private/ setup, the created/unchanged report, the governance name rule
and the workflow event stream. No network, no Langfuse, no Node."""
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
import seed_all as s  # noqa: E402


def governance_state(registered=("ADMIN", "MEMBER"), versions=(1, 2)):
    return {"applications": {"gov": {"id": "gov", "name": "Governance Demo"}},
            "governance": {"projectId": "gov", "versions": [{"version": n} for n in versions],
                           "personas": {r: {"registered": True} for r in registered}}}


class Report(unittest.TestCase):
    def test_first_run_reports_everything_created(self):
        before = s.governance_snapshot({"applications": {}})
        lines = s.report(before, s.governance_snapshot(governance_state()))
        self.assertEqual([status for status, _ in lines], ["created", "created", "created", "created", "created", "updated"])

    def test_rerun_reports_unchanged_and_the_repeated_proof(self):
        snap = s.governance_snapshot(governance_state())
        lines = s.report(snap, snap)
        self.assertEqual([status for status, _ in lines], ["unchanged"] * 5 + ["updated"])
        self.assertIn("Admin promoted v2", lines[-1][1])

    def test_governance_name_reuses_the_recorded_project(self):
        self.assertEqual(s.governance_name(governance_state(), None), "Governance Demo")
        self.assertEqual(s.governance_name({"applications": {}}, None), s.DEFAULT_GOVERNANCE_NAME)
        self.assertEqual(s.governance_name({"governance": {"pendingName": "Half made"}}, None), "Half made")
        self.assertEqual(s.governance_name(governance_state(), "Explicit"), "Explicit")

    def test_event_lines(self):
        self.assertIn("Create governed prompt", s.event_line({"type": "request", "label": "Create governed prompt", "method": "POST", "path": "/p"}))
        self.assertEqual(s.event_line({"type": "response", "ok": False, "status": 403}).strip(), "FAILED HTTP 403")
        self.assertEqual(s.event_line({"type": "governance", "checks": ["a", "b"]}).count("check:"), 2)
        self.assertIsNone(s.event_line({"type": "complete"}))


class Private(unittest.TestCase):
    def test_missing_org_env_is_copied_from_the_example_and_stops_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "private"
            lines, usable = s.ensure_private(state_dir, state_dir / "org.env")
            self.assertFalse(usable)
            self.assertEqual([status for status, _ in lines], ["created", "created"])
            self.assertEqual(state_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual((state_dir / "org.env").stat().st_mode & 0o777, 0o600)
            self.assertEqual((state_dir / "org.env").read_text(), (ROOT / "org.env.example").read_text())

    def test_loose_modes_are_tightened_then_left_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            state_dir.chmod(0o755)
            (state_dir / "org.env").write_text("x\n")
            (state_dir / "org.env").chmod(0o644)
            first, usable = s.ensure_private(state_dir, state_dir / "org.env")
            second, _ = s.ensure_private(state_dir, state_dir / "org.env")
        self.assertTrue(usable)
        self.assertEqual([status for status, _ in first], ["updated", "updated"])
        self.assertEqual([status for status, _ in second], ["unchanged", "unchanged"])


class Workflow(unittest.TestCase):
    def fake(self, events, code=0):
        seen = {}

        class Process:
            def __init__(self, command, **kwargs):
                seen.update(command=command, **kwargs)
                self.stdout = iter(json.dumps(e) + "\n" for e in events)

            def wait(self):
                return code

        return Process, seen

    def test_runs_the_portal_script_with_a_params_file(self):
        runner, seen = self.fake([{"type": "request", "label": "x", "method": "GET", "path": "/"},
                                  {"type": "complete", "result": {"message": "Protected production verified."}}])
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            ok, message = s.run_governance(Path(tmp), Path(tmp) / "org.env", "Governance Demo", runner)
            params = Path(seen["command"][seen["command"].index("--params-file") + 1])
            self.assertEqual(json.loads(params.read_text()), {"governanceName": "Governance Demo"})
        self.assertEqual((ok, message), (True, "Protected production verified."))
        self.assertTrue(seen["command"][0].endswith("scripts/protected-labels.sh"))
        self.assertFalse([k for k in seen["env"] if k.startswith("LANGFUSE_") and k != "LANGFUSE_CLI_BIN"])

    def test_error_event_fails_the_seed(self):
        runner, _ = self.fake([{"type": "error", "message": "[Errno 35] Resource temporarily unavailable"}], code=1)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            ok, message = s.run_governance(Path(tmp), Path(tmp) / "org.env", "G", runner)
        self.assertFalse(ok)
        self.assertIn("workflow.lock", message)


if __name__ == "__main__":
    unittest.main()

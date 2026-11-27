"""Exercise the actual dispatch and gate shell steps without services or model calls."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/handoff-prompt-gate.yml"


@unittest.skipUnless(shutil.which("bash") and shutil.which("jq"), "requires bash and jq (Actions runner tools)")
class HandoffWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["gate"]["steps"]

    def select(self, prompt=None, *, manual=None):
        step = next(s for s in self.steps if s.get("id") == "which")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text(json.dumps({"client_payload": {"prompt": prompt or {}}}))
            output = root / "output"
            summary = root / "summary"
            env = {**os.environ, "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
                   "GITHUB_STEP_SUMMARY": str(summary),
                   "EVENT_NAME": "workflow_dispatch" if manual else "repository_dispatch",
                   "MANUAL_NAME": manual[0] if manual else "",
                   "MANUAL_LABEL": manual[1] if manual else ""}
            subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=True,
                           capture_output=True, text=True)
            return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_dispatch_retains_event_version_and_finds_deployable_label(self):
        result = self.select({"name": "concierge", "version": 3,
                              "labels": ["latest", "workshop-note", "candidate"]})
        self.assertEqual(result["version"], "3")
        self.assertEqual(result["label"], "candidate")
        self.assertEqual(result["skip"], "")

    def test_production_is_preferred_and_retains_dispatch_version(self):
        result = self.select({"name": "billing", "version": 7,
                              "labels": ["candidate", "production"]})
        self.assertEqual(result["label"], "production")
        self.assertEqual(result["version"], "7")
        self.assertEqual(result["is_production"], "true")

    def test_dispatch_without_valid_version_is_skipped(self):
        for version in (None, 0, -1, "3\nname=billing", "abc"):
            with self.subTest(version=version):
                result = self.select({"name": "concierge", "version": version, "labels": ["candidate"]})
                self.assertTrue(result["skip"])
                self.assertEqual(result["version"], "")

    def test_prompt_allowlist_is_literal_and_outputs_cannot_be_injected(self):
        for name in ("concierge\\|billing", "concierge\nlabel=production", ".*"):
            with self.subTest(name=name):
                result = self.select({"name": name, "version": 3, "labels": ["candidate"]})
                self.assertTrue(result["skip"])
                self.assertEqual(result["name"], "")
                self.assertEqual(result["label"], "candidate")

    def test_manual_run_resolves_version_in_gate_and_validates_inputs(self):
        result = self.select(manual=("tech-support", "fault-f3"))
        self.assertEqual(result["skip"], "")
        self.assertEqual(result["version"], "")
        for manual in ((".*", "candidate"), ("billing", "candidate\nname=concierge")):
            with self.subTest(manual=manual):
                self.assertTrue(self.select(manual=manual)["skip"])

    def test_gate_passes_immutable_version_as_one_argument(self):
        step = next(s for s in self.steps if s.get("name", "").startswith("Gate —"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / ".venv/bin/python"
            binary.parent.mkdir(parents=True)
            binary.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                              "from pathlib import Path\n"
                              "Path(os.environ['CAPTURE_ARGS']).write_text(json.dumps(sys.argv[1:]))\n")
            binary.chmod(0o755)
            captured = root / "args.json"
            for version in ("3", ""):
                with self.subTest(version=version):
                    env = {**os.environ, "PROMPT_NAME": "concierge$(printf injected)",
                           "PROMPT_LABEL": "candidate", "PROMPT_VERSION": version,
                           "GITHUB_RUN_NUMBER": "12", "CAPTURE_ARGS": str(captured)}
                    subprocess.run(["bash", "-e", "-c", step["run"]], cwd=root, env=env,
                                   check=True, capture_output=True, text=True)
                    args = json.loads(captured.read_text())
                    self.assertEqual(args[args.index("--prompt") + 1], env["PROMPT_NAME"])
                    if version:
                        self.assertEqual(args[args.index("--version") + 1], version)
                    else:
                        self.assertNotIn("--version", args)


if __name__ == "__main__":
    unittest.main()

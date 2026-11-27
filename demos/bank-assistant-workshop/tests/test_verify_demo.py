"""verify_demo.py offline, through its real HTTP client against tests/fake_langfuse.py.

No network, Langfuse, Docker or model calls: urllib.request.urlopen and subprocess.run are
patched. Covers the helpers, the env rule, every check's pass and fail states, cursor
paging, failure propagation and the verdict.
"""
import io
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_demo as v  # noqa: E402
from tests.fake_langfuse import FakeAPI  # noqa: E402

ENV = ("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk-file\n"
       "LANGFUSE_BASE_URL=https://lf.example\nWORKSHOP_ID=t-bank\n")
HOSTILE_SHELL = {"LANGFUSE_PUBLIC_KEY": "pk-shell-other", "LANGFUSE_SECRET_KEY": "sk-shell-other",
                 "LANGFUSE_HOST": "https://shell.example", "LANGFUSE_BASE_URL": "https://shell.example"}


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        for k in [k for k in os.environ if k in ("WORKSHOP_ID", "DEMO_LANGFUSE_PROJECT", "WORKSHOP_PORT",
                                                 "NORTHWIND_MCP_PORT")]:
            del os.environ[k]
        del v.RESULTS[:]
        self.tmp = tempfile.TemporaryDirectory()
        self.env = Path(self.tmp.name) / ".env"
        self.env.write_text(ENV)
        self.api = FakeAPI()
        self.preflight = SimpleNamespace(returncode=0, stdout="Preflight passed.\n", stderr="")
        self.subprocess_calls = []

    def tearDown(self):
        self.tmp.cleanup()
        del v.RESULTS[:]
        os.environ.clear()
        os.environ.update(self.saved)

    def run_main(self, *args, env_text=None):
        if env_text is not None:
            self.env.write_text(env_text)

        def fake_run(cmd, **kwargs):
            self.subprocess_calls.append(cmd)
            return self.preflight

        with patch.object(v.urllib.request, "urlopen", self.api.urlopen), \
                patch.object(v.subprocess, "run", fake_run), redirect_stdout(io.StringIO()) as out:
            code = v.main(["--env-file", str(self.env), *args])
        text = out.getvalue()
        verdict = [line for line in text.splitlines() if line.startswith(("READY", "NOT READY"))]
        self.assertEqual(len(verdict), 1, text)
        return code, text, verdict[0]

    def assertReady(self, *args, **kw):
        code, text, verdict = self.run_main(*args, **kw)
        self.assertEqual(code, 0, text)
        self.assertTrue(verdict.startswith("READY"), text)
        return text

    def assertNotReady(self, needle, *args, **kw):
        code, text, verdict = self.run_main(*args, **kw)
        self.assertEqual(code, 1, text)
        self.assertTrue(verdict.startswith("NOT READY"), text)
        self.assertIn(needle, text)
        return text


class HelperTests(Base):
    def test_check_and_summary(self):
        with redirect_stdout(io.StringIO()) as out:
            v.check(True, "a")
            v.check(False, "b", required=False, hint="do x")
        self.assertTrue(out.getvalue().splitlines()[1].endswith("->  do x"))
        self.assertEqual(v.summary(), ("READY (1/2 checks passed, 1 warning)", 0))
        self.assertEqual(v.summary([("FAIL", True, "x"), ("WARN", False, "y")]),
                         ("NOT READY (0/2 checks passed, 1 required failed, 1 warning)", 1))

    def test_parse_env_handles_comments_quotes_and_export(self):
        text = '# c\n\nexport A=1\nB="two words"  \nC=three # trailing\nD=\'x # y\'\nE=\nnot a line\n'
        self.assertEqual(v.parse_env(text), {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""})

    def test_load_env_drops_every_shell_langfuse_key_even_without_a_file(self):
        environ = dict(HOSTILE_SHELL, OTHER="kept")
        self.assertTrue(v.load_env(self.env, environ))
        self.assertEqual(environ["LANGFUSE_PUBLIC_KEY"], "pk-file")
        self.assertNotIn("LANGFUSE_HOST", environ)
        environ = dict(HOSTILE_SHELL, OTHER="kept")
        self.assertFalse(v.load_env(Path(self.tmp.name) / "missing", environ))
        self.assertEqual(environ, {"OTHER": "kept"})

    def test_manifest_keeps_the_default_project(self):
        text = (v.DEMO_DIR / "demo.yaml").read_text()
        self.assertEqual(v.manifest_value(text, "langfuse", "project"), "bank-workshop")
        self.assertEqual(v.manifest_value(text, "", "profile"), "workshop")
        self.assertEqual(v.env_file("workshop"), v.DEMO_DIR / ".env")

    def test_names_and_local_definitions_match_the_app(self):
        import helpers  # noqa: F401  (scripts/ is on sys.path; loads northwind.config)
        from northwind import agent, config, prompts
        names = v.workshop_names({"WORKSHOP_ID": config.WORKSHOP_ID})
        self.assertEqual(names["prompt"], prompts.PROMPT_NAME)
        self.assertEqual(names["dataset"], helpers.DATASET_NAME)
        self.assertEqual(v.ROOT_NAME, agent.TRACE_NAME)
        self.assertEqual(v.local_prompts(), {"baseline": prompts.V1_BASELINE, "candidate": prompts.WORKSHOP_CANDIDATE})
        self.assertEqual(v.local_items(), helpers.load_data())

    def test_local_items_rejects_unusable_files(self):
        for body in ("[]", '{"a": 1}', '[{"id": "x"}]', '[{"id": "x", "input": {}, "expected_output": "y",'
                     ' "metadata": {}}, {"id": "x", "input": {}, "expected_output": "y", "metadata": {}}]'):
            path = Path(self.tmp.name) / "d.json"
            path.write_text(body)
            with self.assertRaises(ValueError):
                v.local_items(path)

    def test_parse_time_accepts_api_timestamps(self):
        self.assertEqual(v.parse_time("2026-11-26T10:00:00.123Z").microsecond, 123000)
        self.assertEqual(v.parse_time("2026-11-26T10:00:00.123456789+00:00").microsecond, 123456)


class PagingTests(Base):
    def client(self):
        return v.Langfuse("https://lf.example", "pk", "sk")

    def read(self, fn):
        with patch.object(v.urllib.request, "urlopen", self.api.urlopen):
            return fn(self.client())

    def test_cursor_follows_empty_pages(self):
        self.api.page_size, self.api.empty_pages = 2, 2
        rows = self.read(lambda lf: lf.cursor("/api/public/v3/scores", {"traceId": "t1", "fields": "subject"}))
        self.assertEqual(len(rows), 3)
        self.assertEqual([p.get("cursor") for e, p in self.api.calls], [None, "c1", "c2", "c3"])

    def test_repeated_cursor_is_an_error(self):
        self.api.page_size, self.api.repeat_cursor = 1, True
        with self.assertRaisesRegex(v.ApiError, "repeated cursor"):
            self.read(lambda lf: lf.cursor("/api/public/v3/scores", {"traceId": "t1"}))

    def test_traversal_is_bounded(self):
        self.api.page_size = 1
        with self.assertRaisesRegex(v.ApiError, "still paging"):
            self.read(lambda lf: lf.cursor("/api/public/v3/scores", {"traceId": "t1"}, max_pages=2))

    def test_dataset_items_follows_pages(self):
        with patch.object(v.urllib.request, "urlopen", self.api.urlopen):
            orig = self.client().get
            lf = self.client()
            lf.get = lambda path, **p: orig(path, **dict(p, limit=3))  # force three pages of 3/3/2
            rows = v.dataset_items(lf, self.api.dataset_name)
        self.assertEqual(len(rows), 8)

    def test_invalid_query_parameters_are_rejected_like_the_api(self):
        with self.assertRaises(v.ApiError) as e:
            self.read(lambda lf: lf.get("/api/public/v3/scores", traceId="t1", page=1))
        self.assertEqual(e.exception.status, 400)

    def test_network_failure_is_an_api_error(self):
        def refuse(*a, **k):
            raise urllib.error.URLError("connection refused")
        with patch.object(v.urllib.request, "urlopen", refuse), self.assertRaises(v.ApiError) as e:
            self.client().get("/api/public/projects")
        self.assertIsNone(e.exception.status)


class VerdictTests(Base):
    def test_everything_in_place_is_ready_with_links(self):
        text = self.assertReady("--links")
        self.assertIn("/project/p1/traces/t1", text)
        self.assertIn("/project/p1/prompts/bank-workshop%2Ft-bank%2Fassistant", text)
        self.assertIn("/datasets/d1/runs/run-workshop-candidate", text)
        calls = dict(self.api.calls)
        self.assertEqual(calls["scores"]["fields"], "subject")
        self.assertEqual(calls["experiments"]["fields"], "core,metadata")
        self.assertIn("basic", [p for e, p in self.api.calls if e == "observations"][0]["fields"])

    def test_preflight_runs_workshop_py_with_the_same_env_file(self):
        self.assertReady()
        cmd = self.subprocess_calls[0]
        self.assertEqual(cmd[2:], ["--env-file", str(self.env.resolve()), "preflight"])

    def test_failed_preflight_is_not_ready(self):
        self.preflight = SimpleNamespace(returncode=1, stdout="", stderr="RuntimeError: Set OPENAI_API_KEY")
        self.assertNotReady("FAIL  workshop.py preflight")

    # 129-2: the workshop services are required
    def test_stopped_services_are_not_ready(self):
        self.api.portal, self.api.mcp = None, None
        text = self.assertNotReady("FAIL  portal on 127.0.0.1:8090/health: HTTP None")
        self.assertIn("FAIL  banking MCP server on 127.0.0.1:8765/mcp: HTTP None", text)

    def test_stopped_mcp_server_alone_is_not_ready(self):
        self.api.mcp = None
        self.assertNotReady("FAIL  banking MCP server")

    def test_portal_from_an_untraced_checkpoint_or_other_workshop_is_not_ready(self):
        for health in ({"status": "ok", "tracing": False, "workshop": "t-bank"},
                       {"status": "ok", "tracing": True, "workshop": "someone-else"}):
            self.api.portal = (200, health)
            self.assertNotReady("FAIL  portal")

    def test_services_follow_the_configured_ports(self):
        self.assertNotReady("portal on 127.0.0.1:9999", env_text=ENV + "WORKSHOP_PORT=9999\n")

    def test_prep_mode_skips_services_and_says_so(self):
        self.api.portal, self.api.mcp = None, None
        text = self.assertReady("--prep")
        self.assertIn("preparation only: portal and MCP server not checked", text.splitlines()[-1])
        self.assertNotIn("portal on", text)

    def test_prep_mode_still_fails_on_everything_else(self):
        self.api.prompts.pop("workshop-candidate")
        self.assertNotReady("has no 'workshop-candidate' label", "--prep")

    # 129-3: the default project stays bank-workshop; the rehearsal project is an explicit override
    def test_rehearsal_project_needs_the_explicit_override(self):
        self.api.projects = [{"id": "p1", "name": "banking-workshop"}]
        self.assertNotReady("keys map to project 'banking-workshop' (want 'bank-workshop')")
        self.assertReady(env_text=ENV + "DEMO_LANGFUSE_PROJECT=banking-workshop\n")

    def test_wrong_or_empty_project_stops_before_artifact_reads(self):
        for projects in ([{"id": "p9", "name": "other"}], []):
            self.api.projects, self.api.calls = projects, []
            self.assertNotReady("keys map to project")
            self.assertEqual([e for e, _ in self.api.calls], ["projects"])

    def test_missing_env_with_hostile_shell_makes_no_api_call(self):
        os.environ.update(HOSTILE_SHELL)
        self.env.unlink()
        text = self.assertNotReady("FAIL  env loaded")
        self.assertIn("FAIL  LANGFUSE_PUBLIC_KEY", text)
        self.assertEqual((self.api.calls, self.subprocess_calls), ([], []))

    def test_partial_env_with_hostile_shell_makes_no_api_call(self):
        os.environ.update(HOSTILE_SHELL)
        self.assertNotReady("FAIL  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL set",
                            env_text="LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=https://lf.example\n")
        self.assertEqual((self.api.calls, self.subprocess_calls), ([], []))

    def test_unauthorised_keys_are_not_ready(self):
        self.api.fail["projects"] = 401
        self.assertNotReady("keys authenticate against https://lf.example: HTTP 401")

    def test_missing_label_is_not_ready(self):
        self.api.prompts.pop("workshop-baseline")
        self.assertNotReady("has no 'workshop-baseline' label")

    def test_config_only_drift_is_not_ready(self):
        self.api.prompts["workshop-candidate"]["config"] = {"variant": "baseline"}
        self.assertNotReady("FAIL  prompt bank-workshop/t-bank/assistant: workshop-candidate")

    def test_text_drift_is_not_ready(self):
        self.api.prompts["workshop-baseline"]["prompt"] = "something else"
        self.assertNotReady("FAIL  prompt bank-workshop/t-bank/assistant: workshop-baseline")

    def test_missing_dataset_is_not_ready(self):
        self.api.dataset = None
        self.assertNotReady("FAIL  dataset bank-workshop/t-bank/regression-v1 exists")

    def test_archived_only_dataset_is_not_ready(self):
        for r in self.api.rows:
            r["status"] = "ARCHIVED"
        self.assertNotReady("0/8 items ACTIVE and matching")

    def test_changed_item_is_not_ready_and_extra_remote_items_are_ignored(self):
        self.api.rows.append({"id": "someone-elses", "status": "ACTIVE"})
        self.assertReady()
        self.api.rows[0]["expectedOutput"] = "changed"
        self.assertNotReady("7/8 items ACTIVE and matching data/seed-dataset.json; not: g01")

    def test_no_recent_turn_is_not_ready(self):
        self.api.roots = []
        self.assertNotReady("FAIL  a 'northwind-assistant' turn in environment 'workshop'")

    def test_experiment_turns_do_not_count_as_a_served_turn(self):
        self.api.roots[0]["environment"] = "sdk-experiment"
        self.assertNotReady("FAIL  a 'northwind-assistant' turn")

    def test_too_few_observations_is_not_ready(self):
        self.api.trace_obs = self.api.trace_obs[:1]
        self.assertNotReady("latest turn has 1 observation(s)")

    def test_signals_must_sit_on_the_root_observation(self):
        self.api.scores[1]["subject"] = {"kind": "trace", "id": "t1", "traceId": "t1"}
        self.assertNotReady("missing pii-in-input")
        self.api.scores[1]["subject"] = {"kind": "observation", "id": "o3", "traceId": "t1"}
        self.assertNotReady("missing pii-in-input")

    def test_scores_spread_over_empty_cursor_pages_still_count(self):
        self.api.page_size, self.api.empty_pages = 1, 2
        self.assertReady()

    def test_repeated_cursor_reaches_the_verdict(self):
        self.api.page_size, self.api.repeat_cursor = 1, True
        self.assertNotReady("Langfuse read failed")

    def test_turn_older_than_a_day_warns(self):
        from tests.fake_langfuse import NOW, iso
        from datetime import timedelta
        self.api.roots[0]["startTime"] = iso(NOW - timedelta(hours=30))
        text = self.assertReady()
        self.assertIn("WARN  latest turn is", text)

    def test_missing_prepared_run_is_not_ready(self):
        self.api.experiments = [r for r in self.api.experiments if r["metadata"]["prompt_label"] != "workshop-candidate"]
        self.assertNotReady("FAIL  a run of workshop-candidate v2")

    def test_run_of_an_older_prompt_version_does_not_count(self):
        self.api.prompts["workshop-baseline"]["version"] = 7
        self.assertNotReady("FAIL  a run of workshop-baseline v7")

    def test_required_read_failures_reach_the_verdict(self):
        for endpoint in ("prompt", "dataset", "dataset-items", "observations", "scores", "experiments"):
            self.api = FakeAPI()
            self.api.fail[endpoint] = 500
            self.assertNotReady("Langfuse read failed: HTTP 500")

    def test_malformed_response_reaches_the_verdict(self):
        self.api.roots[0]["startTime"] = "yesterday"
        self.assertNotReady("unexpected Langfuse response")

    def test_missing_core_assets_are_not_ready(self):
        with patch.object(v, "DATASET_FILE", Path(self.tmp.name) / "missing.json"), \
                patch.object(v.local_items, "__defaults__", (Path(self.tmp.name) / "missing.json",)):
            self.assertNotReady("FAIL  data/seed-dataset.json")
        with patch.object(v.local_prompts, "__defaults__", (Path(self.tmp.name) / "missing.py",)):
            self.assertNotReady("FAIL  prompt text in src/northwind/prompts.py")


if __name__ == "__main__":
    unittest.main()

"""verify_demo.py offline: results, summary and exit code, the .env rule, the manifest parse,
the preflight wrapper, and full runs against a fake Langfuse. No network, Langfuse or models."""
import io
import os
import subprocess
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

NOW = "2099-01-01T00:00:00.000Z"  # always newer than the lookback window start
NAMES = v.workshop_names({"WORKSHOP_ID": "t-bank"})


class FakeLangfuse:
    """Answers the reads verify_demo makes, from canned rows. Tweak class attributes per test."""

    project = "banking-workshop"
    labels = set(v.PROMPT_LABELS)
    dataset_exists = True
    items = [{"id": f"i{n}", "status": "ACTIVE"} for n in range(8)]
    roots = [{"traceId": "t1", "startTime": NOW}]
    scores = list(v.EXPECTED_SCORES)
    experiments: object = [{"id": "run1"}]

    def __init__(self, base="https://lf.example", pk="pk", sk="sk"):
        self.base = base
        self.paths = []

    def get(self, path, **params):
        self.paths.append(path)
        if path == "/api/public/projects":
            return {"data": [{"id": "p1", "name": self.project}]}
        if path.startswith("/api/public/v2/prompts/"):
            assert "%2F" in path  # folder-style names are encoded
            if params["label"] not in self.labels:
                raise v.ApiError(404, "not found")
            return {"version": 2}
        if path.startswith("/api/public/v2/datasets/"):
            if not self.dataset_exists:
                raise v.ApiError(404, "not found")
            return {"id": "d1"}
        if path == "/api/public/dataset-items":
            return {"data": self.items, "meta": {"totalPages": 1}}
        if path == "/api/public/v2/observations":
            assert params["fromStartTime"] and params["toStartTime"]  # every read is time-bounded
            assert params["environment"] == v.ENVIRONMENT and params["name"] == v.ROOT_NAME
            return {"data": self.roots[:1]}
        raise AssertionError(path)

    def cursor(self, path, params, cap=1000):
        self.paths.append(path)
        if path == "/api/public/v2/observations":
            return [{"id": f"o{n}"} for n in range(5)]
        if path == "/api/public/v3/scores":
            return [{"name": n} for n in self.scores]
        if path == "/api/public/experiments":
            if isinstance(self.experiments, Exception):
                raise self.experiments
            return self.experiments
        raise AssertionError(path)


def fake(**attrs):
    return type("Fake", (FakeLangfuse,), attrs)()


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = dict(os.environ)
        del v.RESULTS[:]

    def tearDown(self):
        del v.RESULTS[:]
        os.environ.clear()
        os.environ.update(self.saved)

    def run_checks(self, lf, project="banking-workshop"):
        with redirect_stdout(io.StringIO()) as out:
            state = v.langfuse_checks(lf, project, NAMES)
        return state, out.getvalue(), v.summary()


class HelperTests(Base):
    def test_check_records_pass_fail_and_warn(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertTrue(v.check(True, "a"))
            self.assertFalse(v.check(False, "b"))
            self.assertFalse(v.check(False, "c", required=False, hint="do x"))
        self.assertEqual([s for s, _, _ in v.RESULTS], ["PASS", "FAIL", "WARN"])
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[2].startswith("  WARN  c") and lines[2].endswith("->  do x"))

    def test_summary_ready_only_when_no_required_check_fails(self):
        self.assertEqual(v.summary([("PASS", True, "a"), ("WARN", False, "b")]),
                         ("READY (1/2 checks passed, 1 warning)", 0))
        self.assertEqual(v.summary([("PASS", True, "a"), ("FAIL", True, "b"), ("WARN", False, "c")]),
                         ("NOT READY (1/3 checks passed, 1 required failed, 1 warning)", 1))

    def test_parse_env_handles_comments_quotes_and_export(self):
        text = '# c\n\nexport A=1\nB="two words"  \nC=three # trailing\nD=\'x # y\'\nE=\nnot a line\n'
        self.assertEqual(v.parse_env(text), {"A": "1", "B": "two words", "C": "three", "D": "x # y", "E": ""})

    def test_env_file_overrides_and_drops_shell_langfuse_keys(self):
        environ = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "LANGFUSE_HOST": "https://shell", "OTHER": "kept"}
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_BASE_URL=https://file\n")
            self.assertTrue(v.load_env(env, environ))
            self.assertEqual(environ, {"LANGFUSE_PUBLIC_KEY": "pk-file", "LANGFUSE_BASE_URL": "https://file",
                                       "OTHER": "kept"})
            environ = {"LANGFUSE_PUBLIC_KEY": "pk-shell", "OTHER": "kept"}
            self.assertFalse(v.load_env(Path(tmp) / "missing", environ))
            self.assertEqual(environ, {"OTHER": "kept"})

    def test_workshop_profile_reads_its_own_env(self):
        self.assertEqual(v.env_file("workshop"), v.DEMO_DIR / ".env")

    def test_own_manifest_names_the_rehearsal_project_and_profile(self):
        text = (v.DEMO_DIR / "demo.yaml").read_text()
        self.assertEqual(v.manifest_value(text, "langfuse", "project"), "banking-workshop")
        self.assertEqual(v.manifest_value(text, "", "profile"), "workshop")

    def test_workshop_names_follow_workshop_id(self):
        self.assertEqual(v.workshop_names({}), {"workshop_id": "bank-workshop",
                                                "prompt": "bank-workshop/bank-workshop/assistant",
                                                "dataset": "bank-workshop/bank-workshop/regression-v1"})
        self.assertEqual(NAMES["prompt"], "bank-workshop/t-bank/assistant")

    def test_names_match_the_app(self):
        """The strings verify_demo rebuilds must equal what the app and helpers use."""
        import helpers  # noqa: F401  (scripts/ is on sys.path; loads northwind.config)
        from northwind import agent, config, prompts
        names = v.workshop_names({"WORKSHOP_ID": config.WORKSHOP_ID})
        self.assertEqual(names["prompt"], prompts.PROMPT_NAME)
        self.assertEqual(names["dataset"], helpers.DATASET_NAME)
        self.assertEqual(v.ROOT_NAME, agent.TRACE_NAME)

    def test_expected_dataset_size_reads_the_seed_file(self):
        self.assertEqual(v.expected_dataset_size(), 8)

    def test_parse_time_accepts_api_timestamps(self):
        self.assertEqual(v.parse_time("2026-11-26T10:00:00.123Z").microsecond, 123000)
        self.assertEqual(v.parse_time("2026-11-26T10:00:00.123456789+00:00").microsecond, 123456)

    def test_network_failure_is_an_api_error_not_a_traceback(self):
        def refuse(*a, **k):
            raise urllib.error.URLError("connection refused")
        with patch.object(v.urllib.request, "urlopen", refuse):
            with self.assertRaises(v.ApiError) as e:
                v.Langfuse("http://langfuse.invalid", "pk", "sk").get("/api/public/projects")
        self.assertIsNone(e.exception.status)

    def test_dataset_items_follows_pages(self):
        class Paged:
            def get(self, path, **params):
                return {"data": [{"id": params["page"]}], "meta": {"totalPages": 3}}
        self.assertEqual([i["id"] for i in v.dataset_items(Paged(), "d")], [1, 2, 3])


class PreflightTests(Base):
    def run_preflight(self, returncode):
        done = SimpleNamespace(returncode=returncode, stdout="langfuse: 4.17.0\n", stderr="")
        with patch.object(v.subprocess, "run", return_value=done) as run, redirect_stdout(io.StringIO()):
            ok = v.preflight_check(Path("/x/.env"), python="py")
        return ok, run.call_args

    def test_preflight_wraps_workshop_py_with_the_same_env_file(self):
        ok, call = self.run_preflight(0)
        self.assertTrue(ok)
        self.assertEqual(call.args[0][2:], ["--env-file", "/x/.env", "preflight"])
        self.assertEqual(call.args[0][0], "py")

    def test_failed_preflight_is_a_required_failure(self):
        ok, _ = self.run_preflight(1)
        self.assertFalse(ok)
        self.assertEqual(v.summary()[1], 1)

    def test_preflight_timeout_is_reported(self):
        with patch.object(v.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 90)), \
                redirect_stdout(io.StringIO()):
            self.assertFalse(v.preflight_check(Path("/x/.env")))


class LangfuseCheckTests(Base):
    def test_everything_in_place_is_ready_with_links(self):
        state, out, (line, code) = self.run_checks(fake())
        self.assertEqual(code, 0, out)
        self.assertTrue(line.startswith("READY"), line)
        self.assertEqual((state["trace_id"], state["dataset_id"]), ("t1", "d1"))
        links = dict(v.act_links("https://lf.example", state))
        self.assertEqual(links["Module 02 · latest turn"], "https://lf.example/project/p1/traces/t1")
        self.assertEqual(links["Module 03 · the prompt"],
                         "https://lf.example/project/p1/prompts/bank-workshop%2Ft-bank%2Fassistant")
        self.assertIn("/datasets/d1", links["Module 05 · the dataset"])

    def test_keys_for_another_project_stop_before_any_artifact_read(self):
        lf = fake(project="other")
        _, out, (_, code) = self.run_checks(lf)
        self.assertEqual(code, 1)
        self.assertIn("want 'banking-workshop'", out)
        self.assertEqual(lf.paths, ["/api/public/projects"])

    def test_missing_candidate_label_fails(self):
        _, out, (_, code) = self.run_checks(fake(labels={"workshop-baseline"}))
        self.assertEqual(code, 1)
        self.assertIn("has no 'workshop-candidate' label", out)

    def test_missing_dataset_fails_and_skips_experiments(self):
        lf = fake(dataset_exists=False)
        _, out, (_, code) = self.run_checks(lf)
        self.assertEqual(code, 1)
        self.assertNotIn("/api/public/experiments", lf.paths)

    def test_short_dataset_fails(self):
        _, out, (_, code) = self.run_checks(fake(items=[{"id": "a", "status": "ACTIVE"},
                                                        {"id": "b", "status": "ARCHIVED"}]))
        self.assertEqual(code, 1)
        self.assertIn("1 ACTIVE item(s) (want 8)", out)

    def test_no_recent_turn_fails(self):
        _, out, (_, code) = self.run_checks(fake(roots=[]))
        self.assertEqual(code, 1)

    def test_missing_signal_scores_fail(self):
        _, out, (_, code) = self.run_checks(fake(scores=["guardrail-blocked"]))
        self.assertEqual(code, 1)
        self.assertIn("missing pii-in-input, all-caps-signal", out)

    def test_old_turn_and_missing_experiments_only_warn(self):
        _, out, (line, code) = self.run_checks(fake(roots=[{"traceId": "t1", "startTime": "2000-01-01T00:00:00Z"}],
                                                    experiments=[]))
        self.assertEqual(code, 0, out)
        self.assertIn("2 warnings", line)

    def test_unreadable_experiments_only_warn(self):
        _, out, (_, code) = self.run_checks(fake(experiments=v.ApiError(400, "bad")))
        self.assertEqual(code, 0, out)
        self.assertIn("WARN  experiment runs could not be read", out)


class MainTests(Base):
    def test_main_reads_its_env_file_and_ignores_shell_keys(self):
        os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-shell"
        seen = {}

        def make(base, pk, sk):
            seen.update(base=base, pk=pk)
            return fake()

        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("LANGFUSE_PUBLIC_KEY=pk-file\nLANGFUSE_SECRET_KEY=sk\n"
                           "LANGFUSE_BASE_URL=https://lf.example\nWORKSHOP_ID=t-bank\n")
            with patch.object(v, "Langfuse", make), patch.object(v, "listening", return_value=True), \
                    redirect_stdout(io.StringIO()) as out:
                code = v.main(["--env-file", str(env), "--skip-preflight", "--links"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(seen, {"base": "https://lf.example", "pk": "pk-file"})
        self.assertIn("bank-workshop%2Ft-bank%2Fassistant", out.getvalue())
        self.assertTrue(out.getvalue().rstrip().splitlines()[-1].startswith("READY"))

    def test_main_without_env_is_not_ready(self):
        with redirect_stdout(io.StringIO()) as out:
            code = v.main(["--env-file", "/nonexistent/.env"])
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", out.getvalue())


if __name__ == "__main__":
    unittest.main()

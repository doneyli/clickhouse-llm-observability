"""scripts/verify_demo.py against an offline fake: no network, Langfuse, Docker or model calls."""
import importlib.util
import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fake_langfuse import FakeLangfuse, obs

DEMO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("litellm_gateway_verify", DEMO / "scripts" / "verify_demo.py")
verify = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(verify)

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
GOOD_ENV = "LANGFUSE_PUBLIC_KEY=pk-good\nLANGFUSE_SECRET_KEY=sk-good\nLANGFUSE_BASE_URL=http://lf:3001\n"
HOSTILE_SHELL = {"LANGFUSE_PUBLIC_KEY": "pk-other", "LANGFUSE_SECRET_KEY": "sk-other",
                 "LANGFUSE_BASE_URL": "http://other:3001", "LANGFUSE_HOST": "http://other:3001",
                 "LITELLM_LANGFUSE_PUBLIC_KEY": "pk-other", "LITELLM_LANGFUSE_SECRET_KEY": "sk-other"}


def fresh_trace():
    return [obs("o1", "t1", "2026-11-27T10:00:00.000Z"),
            obs("o2", "t1", "2026-11-27T10:00:01.000Z", root=False),
            obs("o0", "t0", "2026-11-25T10:00:00.000Z", session="litellm-gateway-old")]


class VerifyHarness(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "demo").mkdir()
        shutil.copy(DEMO / "demo.yaml", self.root / "demo" / "demo.yaml")
        for name, value in (("REPO_ROOT", self.root), ("DEMO_DIR", self.root / "demo")):
            p = patch.object(verify, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_verify(self, fake, env_text=GOOD_ENV, shell=None, argv=None):
        if env_text is not None:
            (self.root / ".env").write_text(env_text)
        environ = dict(shell or {})
        out = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=fake.urlopen), redirect_stdout(out):
            code = verify.run(argv or [], environ, NOW)
        return code, out.getvalue()

    def assert_no_legacy_reads(self, fake):
        for url in fake.requests:
            self.assertNotIn("/api/public/traces", url)
            self.assertNotIn("/api/public/sessions", url)


class ReadyTests(VerifyHarness):
    def test_ready_on_v4_reads_observations_v2_with_basic_fields(self):
        fake = FakeLangfuse(observations=fresh_trace())
        code, out = self.run_verify(fake, argv=["--links"])
        self.assertEqual(code, 0, out)
        self.assertIn("READY (", out)
        self.assertNotIn("NOT READY", out)
        self.assertIn("latest run t1 ('litellm-gateway-demo') has 2 observation(s)", out)
        self.assertIn("via /api/public/v2/observations", out)
        self.assertIn("http://lf:3001/project/proj-1/traces/t1", out)
        self.assertIn("http://lf:3001/project/proj-1/sessions/litellm-gateway-abc12345", out)
        self.assertIn("http://localhost:4000/ui/", out)
        self.assert_no_legacy_reads(fake)
        window = [u for u in fake.requests if "/v2/observations" in u and "userId=" in u]
        self.assertTrue(window and all("fields=core%2Cbasic%2Ctrace_context" in u for u in window))

    def test_ready_on_pinned_v3_falls_back_to_v1_observations_only_after_a_404(self):
        fake = FakeLangfuse(mode="v3", observations=fresh_trace())
        code, out = self.run_verify(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("via /api/public/observations (pinned v3 server)", out)
        self.assertIn("has 2 observation(s)", out)
        self.assert_no_legacy_reads(fake)
        self.assertTrue(any("/api/public/v2/observations" in u for u in fake.requests))

    def test_cursor_is_followed_across_empty_pages(self):
        fake = FakeLangfuse(observations=fresh_trace(), page_size=1, empty_pages=2)
        code, out = self.run_verify(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("has 2 observation(s)", out)

    def test_stale_run_only_warns(self):
        fake = FakeLangfuse(observations=[obs("o1", "t1", "2026-11-24T10:00:00.000Z")])
        code, out = self.run_verify(fake)
        self.assertEqual(code, 0, out)
        self.assertIn("WARN  latest run is 74.0 h old", out)
        self.assertIn("1 warning", out)

    def test_dedicated_keys_with_documented_override(self):
        env = ("LITELLM_LANGFUSE_PUBLIC_KEY=pk-good\nLITELLM_LANGFUSE_SECRET_KEY=sk-good\n"
               "LITELLM_LANGFUSE_BASE_URL=http://lf:3001\nDEMO_LANGFUSE_PROJECT=ai-gateway\n"
               "LANGFUSE_PUBLIC_KEY=pk-stack\nLANGFUSE_SECRET_KEY=sk-stack\n")
        fake = FakeLangfuse(projects=[{"id": "p2", "name": "ai-gateway"}], observations=fresh_trace())
        code, out = self.run_verify(fake, env_text=env)
        self.assertEqual(code, 0, out)
        self.assertIn("keys map to project 'ai-gateway' (want 'ai-gateway')", out)


class ConfigFailureTests(VerifyHarness):
    def test_missing_env_with_hostile_shell_fails_before_any_api_use(self):
        fake = FakeLangfuse(observations=fresh_trace(), keys=("pk-other", "sk-other"))
        code, out = self.run_verify(fake, env_text=None, shell=dict(HOSTILE_SHELL))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  env loaded from .env", out)
        self.assertIn("NOT READY", out)
        self.assertEqual(fake.requests, [])

    def test_hostile_shell_keys_are_ignored_when_env_exists(self):
        fake = FakeLangfuse(observations=fresh_trace())
        code, out = self.run_verify(fake, shell=dict(HOSTILE_SHELL))
        self.assertEqual(code, 0, out)
        api = [u for u in fake.requests if "/api/public/" in u]
        self.assertTrue(api and all(u.startswith("http://lf:3001/") for u in api))

    def test_partial_dedicated_keys_fail_before_api_use(self):
        fake = FakeLangfuse()
        code, out = self.run_verify(fake, env_text=GOOD_ENV + "LITELLM_LANGFUSE_PUBLIC_KEY=pk-x\n")
        self.assertEqual(code, 1)
        self.assertIn("only one of LITELLM_LANGFUSE_PUBLIC_KEY", out)
        self.assertEqual(fake.requests, [])

    def test_missing_base_url_fails_before_api_use(self):
        fake = FakeLangfuse()
        code, out = self.run_verify(fake, env_text="LANGFUSE_PUBLIC_KEY=pk-good\nLANGFUSE_SECRET_KEY=sk-good\n")
        self.assertEqual(code, 1)
        self.assertIn("missing base URL", out)
        self.assertEqual(fake.requests, [])

    def test_container_profile_is_identified_positively(self):
        manifest = self.root / "demo" / "demo.yaml"
        manifest.write_text(manifest.read_text().replace("profile: container", "profile: standalone"))
        fake = FakeLangfuse()
        code, out = self.run_verify(fake)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  demo.yaml declares profile: container", out)
        self.assertEqual(fake.requests, [])


class ProjectTests(VerifyHarness):
    def test_empty_project_list_is_not_ready(self):
        code, out = self.run_verify(FakeLangfuse(projects=[], observations=fresh_trace()))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  keys resolve to a project", out)

    def test_wrong_project_is_not_ready(self):
        code, out = self.run_verify(FakeLangfuse(projects=[{"id": "x", "name": "Other"}]))
        self.assertEqual(code, 1)
        self.assertIn("keys map to project 'Other' (want 'LLM Observability Demo')", out)

    def test_dedicated_keys_without_override_are_still_checked(self):
        env = ("LITELLM_LANGFUSE_PUBLIC_KEY=pk-good\nLITELLM_LANGFUSE_SECRET_KEY=sk-good\n"
               "LANGFUSE_BASE_URL=http://lf:3001\n")
        fake = FakeLangfuse(projects=[{"id": "p2", "name": "ai-gateway"}], observations=fresh_trace())
        code, out = self.run_verify(fake, env_text=env)
        self.assertEqual(code, 1)
        self.assertIn("(want 'LLM Observability Demo')", out)

    def test_cloud_mode_is_still_checked(self):
        fake = FakeLangfuse(projects=[{"id": "p2", "name": "someone-else"}])
        code, out = self.run_verify(fake, env_text=GOOD_ENV + "DEPLOY_MODE=cloud\n")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  keys map to project 'someone-else'", out)

    def test_wrong_keys_are_not_ready(self):
        fake = FakeLangfuse(keys=("pk-real", "sk-real"))
        code, out = self.run_verify(fake)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  keys authenticate against http://lf:3001: HTTP 401", out)


class ReadFailureTests(VerifyHarness):
    def test_no_recent_run_is_not_ready(self):
        code, out = self.run_verify(FakeLangfuse(observations=[]))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  a gateway run (user gateway-demo-user)", out)

    def test_other_users_do_not_count(self):
        rows = [obs("o1", "t1", "2026-11-27T10:00:00.000Z", user="sdk-demo-user")]
        code, out = self.run_verify(FakeLangfuse(observations=rows))
        self.assertEqual(code, 1)

    def test_a_non_404_observation_error_does_not_fall_back(self):
        fake = FakeLangfuse(observations=fresh_trace(), fail={"/api/public/v2/observations": 500})
        code, out = self.run_verify(fake)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  observation read failed: HTTP 500", out)
        self.assertFalse(any(u.split("?")[0].endswith("/api/public/observations") for u in fake.requests))

    def test_repeated_cursor_is_an_error_not_a_loop(self):
        fake = FakeLangfuse(observations=fresh_trace(), page_size=1, repeat_cursor=True)
        code, out = self.run_verify(fake)
        self.assertEqual(code, 1)
        self.assertIn("returned the same cursor twice", out)
        self.assertLess(len(fake.requests), 10)

    def test_gateway_down_is_not_ready(self):
        code, out = self.run_verify(FakeLangfuse(observations=fresh_trace(), gateway_up=False))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  litellm-proxy live at http://localhost:4000", out)

    def test_unexpected_crash_still_ends_in_a_verdict(self):
        out = io.StringIO()
        with patch.object(verify, "run", side_effect=RuntimeError("boom")), redirect_stdout(out):
            self.assertEqual(verify.main(), 1)
        self.assertIn("NOT READY", out.getvalue())


class HelperTests(unittest.TestCase):
    def test_empty_verdict_is_not_ready(self):
        self.assertEqual(verify.Verdict().summary()[1], 1)

    def test_summary_counts(self):
        v = verify.Verdict()
        with redirect_stdout(io.StringIO()):
            v.check(True, "a")
            v.check(False, "b", required=False)
        self.assertEqual(v.summary(), ("READY (1/2 checks passed, 1 warning)", 0))
        with redirect_stdout(io.StringIO()):
            v.check(False, "c")
        self.assertEqual(v.summary(), ("NOT READY (1/3 checks passed, 1 required failed, 1 warning)", 1))

    def test_parse_env(self):
        env = verify.parse_env('export A=1\nB="two # not a comment"\nC=3 # comment\n# D=4\n')
        self.assertEqual(env, {"A": "1", "B": "two # not a comment", "C": "3"})

    def test_manifest_values(self):
        text = (DEMO / "demo.yaml").read_text()
        self.assertEqual(verify.manifest_value(text, "", "profile"), "container")
        self.assertEqual(verify.manifest_value(text, "langfuse", "project"), "LLM Observability Demo")

    def test_trace_constants_match_the_client_payload(self):
        cspec = importlib.util.spec_from_file_location("lg_client_for_constants", DEMO / "client.py")
        client = importlib.util.module_from_spec(cspec)
        cspec.loader.exec_module(client)
        meta = client.build_payload("p", "r", "s")["metadata"]
        self.assertEqual(meta["trace_name"], verify.TRACE_NAME)
        self.assertEqual(meta["trace_user_id"], verify.DEMO_USER)


if __name__ == "__main__":
    unittest.main()

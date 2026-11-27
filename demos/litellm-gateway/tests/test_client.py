import importlib.util
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


CLIENT_PATH = Path(__file__).resolve().parents[1] / "client.py"
SPEC = importlib.util.spec_from_file_location("litellm_gateway_client", CLIENT_PATH)
client = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(client)


class StubResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class GatewayClientTests(unittest.TestCase):
    def test_payload_contains_langfuse_context(self):
        payload = client.build_payload("hello", "a" * 32, "session-1")
        metadata = payload["metadata"]

        self.assertEqual(payload["model"], "demo-model")
        self.assertEqual(metadata["request_id"], "a" * 32)
        self.assertEqual(metadata["session_id"], "session-1")
        self.assertEqual(metadata["generation_name"], "litellm-gateway-completion")
        self.assertIn("gateway", metadata["tags"])
        self.assertIn("gateway:litellm", metadata["tags"])

    def test_gateway_call_uses_openai_endpoint_and_bearer_auth(self):
        payload = client.build_payload("hello", "b" * 32, "session-2")
        stub_response = {
            "choices": [{"message": {"content": "Gateway observability."}}]
        }

        with patch.object(client, "urlopen", return_value=StubResponse(stub_response)) as mocked:
            response = client.call_gateway("http://gateway:4000", "secret", payload)

        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://gateway:4000/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(
            response["choices"][0]["message"]["content"],
            "Gateway observability.",
        )

    def test_trace_check_reads_v2_observations_and_uses_basic_auth(self):
        """On a v4 server the check must NOT touch the removed v1 trace endpoints."""
        expected_result = {
            "data": [{
                "traceId": "actual-trace-id",
                "traceName": "litellm-gateway-demo",
                "name": "completion",
                "sessionId": "session-3",
                "isRootObservation": True,
            }]
        }
        with patch.object(client, "request_json", return_value=expected_result) as mocked:
            trace = client.wait_for_trace(
                "http://langfuse:3000", "public", "secret", "session-3", 1, 4
            )

        expected_auth = base64.b64encode(b"public:secret").decode("ascii")
        self.assertEqual(
            trace, {"id": "actual-trace-id", "name": "litellm-gateway-demo", "observations": 1}
        )
        url = mocked.call_args.args[0]
        self.assertTrue(url.startswith("http://langfuse:3000/api/public/v2/observations?"))
        self.assertNotIn("/api/public/traces", url)

        query = parse_qs(urlsplit(url).query)
        # trace_context is what carries traceId/traceName onto each row.
        self.assertEqual(query["fields"], ["core,basic,trace_context"])
        self.assertIn("fromStartTime", query)
        # The filter shape is load-bearing: `string`/`=` has returned 200 with
        # zero rows, which is indistinguishable from "the trace never arrived".
        self.assertEqual(
            json.loads(query["filter"][0]),
            [{"type": "stringOptions", "column": "sessionId",
              "operator": "any of", "value": ["session-3"]}],
        )
        self.assertEqual(
            mocked.call_args.kwargs["headers"]["Authorization"],
            f"Basic {expected_auth}",
        )

    def test_trace_check_falls_back_to_v1_on_a_v3_server(self):
        """The self-hosted stack is still 3.x, where v2 observations 404."""
        expected_result = {
            "data": [{"id": "actual-trace-id", "sessionId": "session-3", "name": "trace",
                      "observations": ["obs-1", "obs-2"]}]
        }
        with patch.object(client, "request_json", return_value=expected_result) as mocked:
            trace = client.wait_for_trace(
                "http://langfuse:3000", "public", "secret", "session-3", 1, 3
            )

        self.assertEqual(trace["id"], "actual-trace-id")
        self.assertEqual(trace["observations"], 2)
        self.assertEqual(
            mocked.call_args.args[0],
            "http://langfuse:3000/api/public/traces?sessionId=session-3&limit=10",
        )

    def test_api_major_detected_from_health(self):
        for version, expected in (("4.43.0", 4), ("3.221.1", 3), ("5.0.0", 5)):
            with self.subTest(version=version):
                with patch.object(client, "request_json", return_value={"version": version}):
                    self.assertEqual(
                        client.detect_api_major("http://langfuse:3000", "auth"), expected
                    )

    def test_api_major_defaults_to_v4_when_health_is_unreadable(self):
        """Guessing v3 would silently call endpoints that are being removed."""
        with patch.object(client, "request_json", side_effect=client.DemoError("HTTP 500")):
            self.assertEqual(client.detect_api_major("http://langfuse:3000", "auth"), 4)
        with patch.object(client, "request_json", return_value={"version": "not-a-version"}):
            self.assertEqual(client.detect_api_major("http://langfuse:3000", "auth"), 4)

    def test_v4_rows_collapse_to_one_trace_without_requiring_the_root_flag(self):
        """The gateway's OTLP export owns the trace shape; no flag may be required."""
        rows = [
            {"traceId": "t1", "traceName": "litellm-gateway-demo",
             "name": "child", "sessionId": "session-3"},
            {"traceId": "t1", "traceName": "litellm-gateway-demo",
             "name": "parent", "sessionId": "session-3"},
        ]
        self.assertEqual(
            client.pick_trace(rows, "session-3", 4),
            {"id": "t1", "name": "litellm-gateway-demo", "observations": 2},
        )
        self.assertIsNone(client.pick_trace([], "session-3", 4))
        self.assertIsNone(
            client.pick_trace([{"traceId": "t1", "sessionId": "other"}], "session-3", 4)
        )

    def test_v3_trace_without_observations_is_not_ready(self):
        """A v3 trace row can land before its observations: a 200 alone is not proof."""
        empty = {"data": [{"id": "t1", "sessionId": "session-3", "name": "trace",
                           "observations": []}]}
        with patch.object(client, "request_json", return_value=empty), \
                patch.object(client.time, "sleep"):
            with self.assertRaises(client.DemoError) as ctx:
                client.wait_for_trace("http://langfuse:3000", "pk", "sk", "session-3", 0.05, 3)
        self.assertIn("no observations yet", str(ctx.exception))

    def test_trace_check_stops_on_auth_errors(self):
        with patch.object(client, "request_json",
                          side_effect=client.DemoError("GET x returned HTTP 401: no")) as mocked:
            with self.assertRaises(client.DemoError):
                client.wait_for_trace("http://langfuse:3000", "pk", "sk", "s", 5, 4)
        self.assertEqual(mocked.call_count, 1)


class ReadinessTests(unittest.TestCase):
    def quiet(self):
        return patch("builtins.print")

    def test_summary_ready_not_ready_and_warnings(self):
        with self.quiet():
            ready = client.Readiness(prior_passed=4)
            ready.check(True, "gateway")
            ready.check(False, "skipped", required=False)
        self.assertEqual(ready.summary(), ("READY (5/6 checks passed, 1 warning)", 0))
        with self.quiet():
            ready.check(False, "trace")
        self.assertEqual(
            ready.summary(),
            ("NOT READY (5/7 checks passed, 1 required failed, 1 warning)", 1),
        )

    def test_check_prints_status_and_hint_only_on_failure(self):
        with patch("builtins.print") as printed:
            ready = client.Readiness()
            ready.check(True, "ok", hint="never shown")
            ready.check(False, "bad", hint="fix it")
        self.assertEqual(printed.call_args_list[0].args[0], "  PASS  ok")
        self.assertEqual(printed.call_args_list[1].args[0], "  FAIL  bad  ->  fix it")

    def test_prior_passed_tolerates_bad_values(self):
        self.assertEqual(client.prior_passed({"GATEWAY_PREFLIGHT_PASSED": "4"}), 4)
        self.assertEqual(client.prior_passed({"GATEWAY_PREFLIGHT_PASSED": "x"}), 0)
        self.assertEqual(client.prior_passed({"GATEWAY_PREFLIGHT_PASSED": "-2"}), 0)
        self.assertEqual(client.prior_passed({}), 0)

    def test_expected_project(self):
        self.assertEqual(client.expected_project({}), client.STACK_PROJECT)
        self.assertEqual(client.expected_project({"DEMO_LANGFUSE_PROJECT": "p"}), "p")
        self.assertIsNone(client.expected_project({"LITELLM_LANGFUSE_PUBLIC_KEY": "pk"}))
        self.assertIsNone(client.expected_project({"DEPLOY_MODE": "cloud"}))
        self.assertEqual(
            client.expected_project({"DEPLOY_MODE": "cloud", "DEMO_LANGFUSE_PROJECT": "p"}), "p"
        )

    def test_stack_project_matches_the_manifest(self):
        manifest = (CLIENT_PATH.parent / "demo.yaml").read_text()
        self.assertIn(f"project: {client.STACK_PROJECT}", manifest)

    def test_act_links(self):
        links = client.act_links("http://lf:3001/", "proj", "t1", "s 1", "http://gw:4000")
        self.assertEqual(links, [
            ("Act 1 · this run's trace", "http://lf:3001/project/proj/traces/t1"),
            ("Act 1 · this run's session", "http://lf:3001/project/proj/sessions/s%201"),
            ("Act 2 · LiteLLM admin UI", "http://gw:4000/ui/"),
        ])


class MainTests(unittest.TestCase):
    """main() end to end against stubbed HTTP: the verdict and the exit code."""

    ENV = {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
           "LANGFUSE_BASE_URL": "http://lf:3001", "GATEWAY_PREFLIGHT_PASSED": "4"}
    ANSWER = {"choices": [{"message": {"content": "Gateway observability."}}]}

    def fake_langfuse(self, project="LLM Observability Demo", version="4.40.0", rows=None):
        rows = [{"traceId": "t1", "traceName": "litellm-gateway-demo", "sessionId": "s1",
                 "isRootObservation": True}] if rows is None else rows

        def fake(url, **kwargs):
            if url.endswith("/v1/chat/completions"):
                return self.ANSWER
            if url.endswith("/api/public/projects"):
                return {"data": [{"id": "proj", "name": project}]}
            if url.endswith("/api/public/health"):
                return {"version": version}
            if "/api/public/v2/observations" in url:
                return {"data": rows}
            raise AssertionError(f"unexpected URL {url}")
        return fake

    def run_main(self, argv, env, fake):
        lines = []
        with patch.object(client, "request_json", side_effect=fake), \
                patch.object(client.time, "sleep"), \
                patch("builtins.print", side_effect=lambda *a, **k: lines.append(" ".join(map(str, a)))):
            code = client.main(argv, env)
        return code, "\n".join(lines)

    def test_ready_with_links(self):
        code, out = self.run_main(["hi", "--session-id", "s1", "--links"], self.ENV,
                                  self.fake_langfuse())
        self.assertEqual(code, 0, out)
        self.assertIn("READY (8/8 checks passed)", out)
        self.assertIn("with 1 observation(s) (server v4, /api/public/v2/observations)", out)
        self.assertIn("http://lf:3001/project/proj/traces/t1", out)

    def test_wrong_project_is_not_ready(self):
        code, out = self.run_main(["hi", "--session-id", "s1"], self.ENV,
                                  self.fake_langfuse(project="Other"))
        self.assertEqual(code, 1)
        self.assertIn("keys map to project 'Other' (want 'LLM Observability Demo')", out)
        self.assertIn("NOT READY (6/7 checks passed, 1 required failed)", out)

    def test_trace_that_never_lands_is_not_ready(self):
        code, out = self.run_main(["hi", "--session-id", "s1", "--trace-timeout", "0.05"],
                                  self.ENV, self.fake_langfuse(rows=[]))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  trace for session s1", out)

    def test_gateway_failure_is_not_ready(self):
        def down(url, **kwargs):
            raise client.DemoError(f"Could not reach {url}: refused")
        code, out = self.run_main(["hi"], self.ENV, down)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  gateway call: Could not reach", out)
        self.assertIn("NOT READY (4/5 checks passed, 1 required failed)", out)

    def test_missing_keys_is_not_ready(self):
        env = {"GATEWAY_PREFLIGHT_PASSED": "4"}
        code, out = self.run_main(["hi"], env, self.fake_langfuse())
        self.assertEqual(code, 1)
        self.assertIn("FAIL  Langfuse keys set", out)

    def test_skipped_trace_check_only_warns(self):
        code, out = self.run_main(["hi", "--skip-trace-check"], self.ENV, self.fake_langfuse())
        self.assertEqual(code, 0)
        self.assertIn("READY (5/6 checks passed, 1 warning)", out)


if __name__ == "__main__":
    unittest.main()

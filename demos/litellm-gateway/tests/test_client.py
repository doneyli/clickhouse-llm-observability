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
        self.assertEqual(trace, {"id": "actual-trace-id", "name": "litellm-gateway-demo"})
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
            "data": [{"id": "actual-trace-id", "sessionId": "session-3", "name": "trace"}]
        }
        with patch.object(client, "request_json", return_value=expected_result) as mocked:
            trace = client.wait_for_trace(
                "http://langfuse:3000", "public", "secret", "session-3", 1, 3
            )

        self.assertEqual(trace["id"], "actual-trace-id")
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
            {"id": "t1", "name": "litellm-gateway-demo"},
        )
        self.assertIsNone(client.pick_trace([], "session-3", 4))
        self.assertIsNone(
            client.pick_trace([{"traceId": "t1", "sessionId": "other"}], "session-3", 4)
        )


if __name__ == "__main__":
    unittest.main()

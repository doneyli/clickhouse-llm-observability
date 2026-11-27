import importlib.util
import base64
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fake_langfuse import FakeLangfuse, obs


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



class TraceCheckTests(unittest.TestCase):
    """client.py's post-run read-back, through the shared reads in scripts/verify_demo.py."""

    SENT = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)

    def wait(self, fake, session="litellm-gateway-abc12345", timeout=1):
        with patch("urllib.request.urlopen", side_effect=fake.urlopen), \
                patch.object(client.time, "sleep"), \
                patch.object(client, "datetime", FixedDatetime):
            return client.wait_for_trace("http://lf:3001", "pk-good", "sk-good", session, timeout, self.SENT)

    def test_v4_reads_observations_v2_for_the_session_with_basic_auth(self):
        fake = FakeLangfuse(observations=[
            obs("o1", "t1", "2026-11-27T10:00:01.000Z"),
            obs("o2", "t1", "2026-11-27T10:00:02.000Z", root=False),
            obs("o9", "t9", "2026-11-27T10:00:03.000Z", session="someone-else"),
        ])
        trace = self.wait(fake)
        self.assertEqual(trace, {"id": "t1", "name": "litellm-gateway-demo",
                                 "observations": 2, "api": "v2"})
        first = urlsplit(fake.requests[0])
        self.assertEqual(first.path, "/api/public/v2/observations")
        query = parse_qs(first.query)
        self.assertEqual(query["fields"], ["core,basic,trace_context"])
        # The filter shape is load-bearing: `string`/`=` has returned 200 with
        # zero rows, which is indistinguishable from "the trace never arrived".
        self.assertEqual(
            json.loads(query["filter"][0]),
            [{"type": "stringOptions", "column": "sessionId",
              "operator": "any of", "value": ["litellm-gateway-abc12345"]}],
        )
        expected_auth = "Basic " + base64.b64encode(b"pk-good:sk-good").decode("ascii")
        self.assertEqual(set(fake.auth_headers), {expected_auth})
        self.assertFalse(any("/api/public/traces" in u for u in fake.requests))

    def test_v3_falls_back_to_v1_observations_on_404_and_never_reads_traces(self):
        fake = FakeLangfuse(mode="v3", observations=[
            obs("o0", "t0", "2026-11-27T09:00:00.000Z"),          # before this request
            obs("o1", "t1", "2026-11-27T10:00:01.000Z"),
        ])
        trace = self.wait(fake)
        self.assertEqual((trace["id"], trace["observations"], trace["api"]), ("t1", 1, "v1"))
        v1 = [u for u in fake.requests if urlsplit(u).path == "/api/public/observations"]
        self.assertTrue(v1)
        self.assertEqual(parse_qs(urlsplit(v1[0]).query)["userId"], ["gateway-demo-user"])
        self.assertFalse(any("/api/public/traces" in u for u in fake.requests))

    def test_session_that_never_lands_raises(self):
        fake = FakeLangfuse(observations=[obs("o9", "t9", "2026-11-27T10:00:03.000Z", session="other")])
        with self.assertRaises(client.DemoError) as ctx:
            self.wait(fake, timeout=0.05)
        self.assertIn("session is not indexed yet", str(ctx.exception))

    def test_auth_errors_stop_polling(self):
        fake = FakeLangfuse(keys=("pk-real", "sk-real"))
        with self.assertRaises(client.DemoError) as ctx:
            self.wait(fake, timeout=5)
        self.assertIn("HTTP 401", str(ctx.exception))
        self.assertEqual(len(fake.requests), 1)

    def test_a_non_404_read_error_does_not_fall_back(self):
        fake = FakeLangfuse(fail={"/api/public/v2/observations": 500})
        with self.assertRaises(client.DemoError) as ctx:
            self.wait(fake, timeout=0.05)
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertFalse(any(urlsplit(u).path == "/api/public/observations" for u in fake.requests))


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 27, 10, 0, 5, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()

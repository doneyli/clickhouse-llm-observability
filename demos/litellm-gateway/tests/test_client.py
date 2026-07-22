import importlib.util
import base64
import json
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_trace_check_uses_session_filter_and_basic_auth(self):
        expected_result = {
            "data": [{"id": "actual-trace-id", "sessionId": "session-3", "name": "trace"}]
        }
        with patch.object(client, "request_json", return_value=expected_result) as mocked:
            trace = client.wait_for_trace(
                "http://langfuse:3000", "public", "secret", "session-3", 1
            )

        expected_auth = base64.b64encode(b"public:secret").decode("ascii")
        self.assertEqual(trace["name"], "trace")
        self.assertEqual(
            mocked.call_args.args[0],
            "http://langfuse:3000/api/public/traces?sessionId=session-3&limit=10",
        )
        self.assertEqual(
            mocked.call_args.kwargs["headers"]["Authorization"],
            f"Basic {expected_auth}",
        )


if __name__ == "__main__":
    unittest.main()

"""Dual-ingest contract: the mirror gets the same spans, scores and media; a dead mirror costs nothing.

Runs with any demo venv (needs langfuse, httpx, python-dotenv):

    demos/real-estate/.venv/bin/python -m unittest discover -s demos/_shared/tests
"""

import base64
import hashlib
import json
import sys
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SpanExportResult  # noqa: E402
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased  # noqa: E402

import langfuse_dual as dual  # noqa: E402

TARGET = dual.Target("http://mirror.invalid", "pk-lf-mirror", "sk-lf-mirror", "proj", "test")


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeHttp:
    """Stands in for httpx.Client: replays scripted responses or raises, and counts calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, text="nope" if outcome >= 400 else "{}")

    def close(self):
        pass


def make_span(provider=None):
    provider = provider or TracerProvider()
    with provider.get_tracer("t").start_as_current_span("s") as span:
        pass
    return span


class BreakerTest(unittest.TestCase):
    def test_failure_opens_until_cooldown_and_logs_each_transition_once(self):
        clock, logs = FakeClock(), []
        breaker = dual.Breaker("local", cooldown_s=30, clock=clock, log=logs.append)
        self.assertTrue(breaker.allow())
        breaker.record_failure("ConnectError")
        breaker.record_failure("ConnectError")
        self.assertFalse(breaker.allow())
        self.assertEqual(len(logs), 1)
        clock.now += 30
        self.assertTrue(breaker.allow())
        breaker.record_success()
        breaker.record_success()
        self.assertTrue(breaker.is_up)
        self.assertEqual(len(logs), 2)

    def test_auth_failure_uses_the_longer_cooldown(self):
        clock = FakeClock()
        breaker = dual.Breaker("local", cooldown_s=30, auth_cooldown_s=300, clock=clock, log=lambda m: None)
        breaker.record_failure("HTTP 401", auth=True)
        clock.now += 31
        self.assertFalse(breaker.allow())
        clock.now += 300
        self.assertTrue(breaker.allow())


class MirrorExporterTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.breaker = dual.Breaker("local", cooldown_s=30, clock=self.clock, log=lambda m: None)
        self.span = make_span()

    def exporter(self, http):
        return dual.MirrorExporter(TARGET, self.breaker, http=http)

    def test_connection_error_opens_the_circuit_and_later_batches_skip_io(self):
        http = FakeHttp(httpx.ConnectError("refused"))
        exporter = self.exporter(http)
        self.assertEqual(exporter.export([self.span]), SpanExportResult.FAILURE)
        self.assertFalse(self.breaker.is_up)
        for _ in range(5):
            self.assertEqual(exporter.export([self.span]), SpanExportResult.FAILURE)
        self.assertEqual(len(http.calls), 1)  # one attempt, then no I/O while open
        self.assertEqual(self.breaker.dropped, 6)

    def test_recovers_after_cooldown(self):
        http = FakeHttp(httpx.ConnectError("refused"), 200)
        exporter = self.exporter(http)
        exporter.export([self.span])
        self.clock.now += 30
        self.assertEqual(exporter.export([self.span]), SpanExportResult.SUCCESS)
        self.assertTrue(self.breaker.is_up)

    def test_server_errors_open_the_circuit_but_a_bad_batch_does_not(self):
        exporter = self.exporter(FakeHttp(413, 200))
        self.assertEqual(exporter.export([self.span]), SpanExportResult.FAILURE)
        self.assertTrue(self.breaker.is_up)
        self.assertEqual(exporter.bad_batches, 1)
        self.assertEqual(self.exporter(FakeHttp(503)).export([self.span]), SpanExportResult.FAILURE)
        self.assertFalse(self.breaker.is_up)

    def test_sends_otlp_protobuf_with_the_mirror_keys_and_v4_headers(self):
        http = FakeHttp(200)
        self.exporter(http).export([self.span])
        url, kwargs = http.calls[0]
        self.assertEqual(url, "http://mirror.invalid/api/public/otel/v1/traces")
        headers = kwargs["headers"]
        self.assertEqual(headers["Authorization"], TARGET.basic_auth())
        self.assertEqual(headers["x-langfuse-public-key"], "pk-lf-mirror")
        self.assertEqual(headers["x-langfuse-ingestion-version"], "4")
        self.assertEqual(headers["Content-Type"], "application/x-protobuf")

    def test_a_hung_mirror_costs_at_most_the_deadline(self):
        import socket

        hung = socket.socket()  # accepts the connection, never answers
        hung.bind(("127.0.0.1", 0))
        hung.listen(8)
        self.addCleanup(hung.close)
        target = dual.Target(f"http://127.0.0.1:{hung.getsockname()[1]}", "pk", "sk")
        exporter = dual.MirrorExporter(target, self.breaker, timeout_s=0.5)
        started = time.monotonic()
        self.assertEqual(exporter.export([self.span]), SpanExportResult.FAILURE)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.breaker.down_reason, "ReadTimeout")

    def test_closed_exporter_never_sends(self):
        http = FakeHttp(200)
        exporter = self.exporter(http)
        exporter.shutdown()
        self.assertEqual(exporter.export([self.span]), SpanExportResult.FAILURE)
        self.assertEqual(http.calls, [])


class ScoreTest(unittest.TestCase):
    def test_body_keeps_the_score_id_and_drops_the_config_id(self):
        body = dual.score_body({"score_id": "s1", "name": "ok", "value": True, "trace_id": "t",
                                "observation_id": "o", "data_type": "BOOLEAN", "config_id": "cfg",
                                "comment": "c"}, environment="production")
        self.assertEqual(body, {"id": "s1", "name": "ok", "value": 1, "traceId": "t",
                                "observationId": "o", "dataType": "BOOLEAN", "comment": "c",
                                "environment": "production"})

    def test_session_score_has_no_trace_id(self):
        body = dual.score_body({"score_id": "s", "name": "n", "value": 0.5, "session_id": "sess"}, None)
        self.assertEqual(body, {"id": "s", "name": "n", "value": 0.5, "sessionId": "sess"})

    def test_sampled_out_trace_is_not_copied(self):
        breaker = dual.Breaker("local", log=lambda m: None)
        http = FakeHttp()
        mirror = dual.ScoreMirror(TARGET, breaker, sampler=TraceIdRatioBased(0.0), http=http)
        mirror.submit({"score_id": "s", "name": "n", "value": 1, "trace_id": "ab" * 16})
        mirror.submit({"score_id": "s2", "name": "n", "value": 1, "session_id": "sess"})
        self.assertTrue(mirror.flush(2))
        self.assertEqual([json.loads(k["content"])["id"] for _, k in http.calls], ["s2"])

    def test_dead_mirror_drains_the_queue_quickly(self):
        clock = FakeClock()
        breaker = dual.Breaker("local", clock=clock, log=lambda m: None)
        http = FakeHttp(httpx.ConnectError("refused"))
        mirror = dual.ScoreMirror(TARGET, breaker, http=http)
        for i in range(50):
            mirror.submit({"score_id": str(i), "name": "n", "value": 1})
        started = time.monotonic()
        self.assertTrue(mirror.flush(2))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(http.calls), 1)

    def test_wrapped_create_score_sends_the_same_id_to_both(self):
        seen = []

        class Client:
            def create_score(self, **kwargs):
                seen.append(kwargs["score_id"])

        client, http = Client(), FakeHttp()
        scores = dual.ScoreMirror(TARGET, dual.Breaker("local", log=lambda m: None), http=http)
        dual._wrap_create_score(client, scores)
        client.create_score(name="n", value=1, trace_id="ab" * 16)
        scores.flush(2)
        self.assertEqual(json.loads(http.calls[0][1]["content"])["id"], seen[0])


class ProfileTest(unittest.TestCase):
    def resolve(self, requested, cloud=True, selfhosted=True):
        return dual.resolve_profile(requested, has_cloud=cloud, has_selfhosted=selfhosted,
                                    knob="X_PROFILE", demo="x")

    def test_defaults(self):
        self.assertEqual(self.resolve(None), "dual")
        self.assertEqual(self.resolve("", selfhosted=False), "cloud")
        self.assertEqual(self.resolve(None, cloud=False), "selfhosted")
        self.assertEqual(self.resolve(None, cloud=False, selfhosted=False), "selfhosted")

    def test_explicit_values_are_validated(self):
        self.assertEqual(self.resolve("Cloud"), "cloud")
        self.assertEqual(self.resolve("selfhosted", selfhosted=False), "selfhosted")  # base .env target
        with self.assertRaises(SystemExit):
            self.resolve("dual", selfhosted=False)
        with self.assertRaises(SystemExit):
            self.resolve("dual", cloud=False)
        with self.assertRaises(SystemExit):
            self.resolve("both")

    def test_other_profiles_need_their_own_overlay(self):
        resolve = lambda name, exists: dual.resolve_profile(  # noqa: E731
            name, has_cloud=True, has_selfhosted=True, knob="X", demo="x",
            overlay_exists=lambda n: exists)
        self.assertEqual(resolve("selfhosted-annuities", True), "selfhosted-annuities")
        with self.assertRaises(SystemExit):
            resolve("selfhosted-annuities", False)


class Collector(BaseHTTPRequestHandler):
    """Fake Langfuse: records OTLP batches, scores and media uploads, answers the project probe.

    Media follows the real server: the id is derived from the sha256, and a second request for
    bytes it already stores gets no upload URL.
    """

    def do_GET(self):
        self._reply(200, {"data": [{"id": "proj"}]})

    def do_POST(self):
        body = self._body()
        self.server.received.append((self.path, self.headers.get("x-langfuse-public-key"), body))
        if self.path != "/api/public/media":
            return self._reply(200, {})
        media_id = json.loads(body)["sha256Hash"].replace("+", "-").replace("/", "_")[:22]
        upload = None if media_id in self.server.stored else \
            f"http://127.0.0.1:{self.server.server_port}/upload/{media_id}"
        self._reply(200, {"mediaId": media_id, "uploadUrl": upload})

    def do_PUT(self):
        body = self._body()
        self.server.received.append((self.path, None, body))
        self.server.stored.add(self.path.rsplit("/", 1)[-1])
        self._reply(200, {})

    def do_PATCH(self):
        self.server.received.append((self.path, None, self._body()))
        self._reply(200, {})

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def _reply(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    server.received = []
    server.stored = set()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
PNG_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()


def media_id(content):
    return base64.b64encode(hashlib.sha256(content).digest()).decode().replace("+", "-").replace("/", "_")[:22]


def paths(server, prefix):
    return [r[0] for r in server.received if r[0].startswith(prefix)]


class MediaTest(unittest.TestCase):
    def setUp(self):
        from langfuse.media import LangfuseMedia

        self.server = serve()
        self.addCleanup(self.server.shutdown)
        self.target = dual.Target(f"http://127.0.0.1:{self.server.server_port}", "pk-lf-mirror",
                                  "sk-lf-mirror", "proj", "test")
        self.photo = LangfuseMedia(base64_data_uri=PNG_URI)

    def test_upload_then_report_and_each_location_once(self):
        media = dual.MediaMirror(self.target, dual.Breaker("local", log=lambda m: None))
        for field in ("input", "input", "output"):  # the duplicate is dropped before any I/O
            media.submit(self.photo, trace_id="ab" * 16, observation_id="cd" * 8, field=field)
        self.assertTrue(media.flush(5))

        requests = [json.loads(r[2]) for r in self.server.received if r[0] == "/api/public/media"]
        self.assertEqual([r["field"] for r in requests], ["input", "output"])
        self.assertEqual(requests[0]["sha256Hash"], self.photo._content_sha256_hash)
        self.assertEqual(requests[0]["contentLength"], len(PNG))
        # the bytes go up once: for "output" the server already has them
        uploads = [r for r in self.server.received if r[0].startswith("/upload/")]
        self.assertEqual([(r[0], r[2]) for r in uploads], [(f"/upload/{media_id(PNG)}", PNG)])
        self.assertEqual(paths(self.server, f"/api/public/media/{media_id(PNG)}"),
                         [f"/api/public/media/{media_id(PNG)}"])
        self.assertEqual(media.failed, 0)

    def test_dead_mirror_drops_media_without_waiting(self):
        clock = FakeClock()
        breaker = dual.Breaker("local", clock=clock, log=lambda m: None)
        http = FakeHttp(httpx.ConnectError("refused"))
        media = dual.MediaMirror(self.target, breaker, http=http)
        for i in range(20):
            media.submit(self.photo, trace_id="ab" * 16, observation_id=f"{i:016x}", field="input")
        started = time.monotonic()
        self.assertTrue(media.flush(2))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(breaker.dropped, 20)

    def test_queue_holds_a_bounded_number_of_bytes(self):
        breaker = dual.Breaker("local", log=lambda m: None)
        breaker.record_failure("test")  # open: nothing is sent, so nothing drains early
        media = dual.MediaMirror(self.target, breaker, max_bytes=len(PNG) * 2, http=FakeHttp())
        for i in range(5):
            media.submit(self.photo, trace_id="ab" * 16, observation_id=f"{i:016x}", field="input")
        self.assertGreaterEqual(breaker.dropped, 3)


class EndToEndTest(unittest.TestCase):
    """A real Langfuse client + provider: the primary gets every span whatever the mirror does."""

    def setUp(self):
        from langfuse import Langfuse

        self.primary = serve()
        self.provider = TracerProvider()
        primary_url = f"http://127.0.0.1:{self.primary.server_port}"
        # The SDK keeps one resource manager per public key and tearDown shuts it down, so
        # every test needs its own key or it inherits a stopped client.
        self.pk = f"pk-lf-primary-{uuid.uuid4().hex[:8]}"
        self.lf = Langfuse(public_key=self.pk, secret_key="sk-lf-primary",
                           base_url=primary_url, tracer_provider=self.provider)

    def tearDown(self):
        self.lf.shutdown()
        self.primary.shutdown()

    def run_turn(self):
        with self.lf.start_as_current_observation(name="turn", as_type="span") as root:
            root.update(input="my card 4111 1111 1111 1111", output="done")
            trace_id = self.lf.get_current_trace_id()
        self.lf.create_score(trace_id=trace_id, name="helpful", value=1)
        return trace_id

    def test_mirror_up_receives_the_same_spans_masked_and_the_same_scores(self):
        mirror_server = serve()
        self.addCleanup(mirror_server.shutdown)
        target = dual.Target(f"http://127.0.0.1:{mirror_server.server_port}", "pk-lf-mirror",
                             "sk-lf-mirror", "proj", "test")

        def mask_cards(*, params):
            from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

            patches = {ident: OtelSpanPatch(set_attributes={
                k: v.replace("4111 1111 1111 1111", "[CARD]")})
                for ident, span in params.spans.items()
                for k, v in span.attributes.items() if isinstance(v, str) and "4111" in v}
            return MaskOtelSpansResult(span_patches=patches) if patches else None

        mirror = dual.attach(self.lf, target, primary_public_key=self.pk,
                             provider=self.provider, mask_otel_spans=mask_cards)
        self.assertIsNotNone(mirror)
        trace_id = self.run_turn()
        self.lf.flush()
        mirror.flush()

        mirrored = [r for r in mirror_server.received if r[0].endswith("/otel/v1/traces")]
        self.assertTrue(mirrored, "mirror received no spans")
        self.assertTrue(all(r[1] == "pk-lf-mirror" for r in mirrored))
        payload = b"".join(r[2] for r in mirrored)
        self.assertIn(bytes.fromhex(trace_id), payload)
        self.assertIn(b"[CARD]", payload)
        self.assertNotIn(b"4111 1111 1111 1111", payload)
        scores = [json.loads(r[2]) for r in mirror_server.received if r[0] == "/api/public/scores"]
        self.assertEqual([(s["name"], s["traceId"]) for s in scores], [("helpful", trace_id)])

    def test_media_reaches_the_mirror_under_the_same_reference(self):
        from langfuse.media import LangfuseMedia

        mirror_server = serve()
        self.addCleanup(mirror_server.shutdown)
        target = dual.Target(f"http://127.0.0.1:{mirror_server.server_port}", "pk-lf-mirror",
                             "sk-lf-mirror", "proj", "test")
        mirror = dual.attach(self.lf, target, primary_public_key=self.pk, provider=self.provider)
        with self.lf.start_as_current_observation(name="photo-audit", as_type="span") as root:
            root.update(input={"photo": LangfuseMedia(content_bytes=PNG, content_type="image/png"),
                               "inline": PNG_URI}, output="ok")
        # a third-party span (no Langfuse SDK involved) carrying a base64 image in gen_ai.prompt
        with self.provider.get_tracer("some.instrumentor").start_as_current_span("llm") as span:
            span.set_attribute("gen_ai.prompt", json.dumps([{"type": "image_url", "url": PNG_URI}]))
        self.lf.flush()
        mirror.flush(5)

        reference = f"id={media_id(PNG)}".encode()
        for server in (self.primary, mirror_server):
            spans = b"".join(r[2] for r in server.received if r[0].endswith("/otel/v1/traces"))
            self.assertIn(reference, spans)
            self.assertNotIn(PNG_URI.split(",")[1].encode(), spans)  # no inline base64 left
        self.assertEqual([r[2] for r in mirror_server.received if r[0].startswith("/upload/")], [PNG])

    def test_dead_mirror_costs_the_primary_nothing(self):
        target = dual.Target(f"http://127.0.0.1:{free_port()}", "pk-lf-mirror", "sk-lf-mirror",
                             "proj", "test")
        mirror = dual.attach(self.lf, target, primary_public_key=self.pk,
                             provider=self.provider)
        self.assertIsNotNone(mirror)
        self.assertFalse(mirror.is_up)  # the startup probe already knows

        started = time.monotonic()
        trace_id = self.run_turn()
        self.lf.flush()
        mirror.flush()
        self.provider.force_flush()
        self.assertLess(time.monotonic() - started, 3)

        primary_spans = b"".join(r[2] for r in self.primary.received
                                 if r[0].endswith("/otel/v1/traces"))
        self.assertIn(bytes.fromhex(trace_id), primary_spans)


if __name__ == "__main__":
    unittest.main()

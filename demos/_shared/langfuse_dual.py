"""
Dual ingest: mirror every Langfuse span and score to a second Langfuse, best-effort.

The demo's Langfuse client stays bound to ONE primary target (Langfuse Cloud in dual
mode). This module adds a copy for a second target (the local self-hosted instance):

    TracerProvider
      ├─ LangfuseSpanProcessor   the client's own              → primary
      └─ LangfuseSpanProcessor   mirror: same filter + mask    → MirrorExporter → mirror

Why not a second Langfuse client: with two clients in one process, get_client() without
a public key returns a DISABLED client, so CallbackHandler / @observe tracing stops with
no error. Why the SDK's own processor rather than a plain BatchSpanProcessor: it applies
the same should_export_span filter and the same mask_otel_spans PII hook, so the mirror
gets exactly the spans the primary gets, redacted the same way, with the same ids. It is
built with the PRIMARY public key, which it only uses for its project-scope filter (so it
accepts the one client's spans); the mirror's own keys live in MirrorExporter's headers.

The mirror must never cost the primary anything:
  * MirrorExporter sends ONE POST per batch with a hard deadline (no retry loop), on the
    mirror processor's own worker thread. The primary's export never waits on it.
  * A circuit breaker drops batches without I/O for `cooldown_s` after a failure, then
    tries again. Each up/down transition is logged once.
  * Score copies and media uploads go through bounded background queues behind the same
    breaker.
  * Mirror.flush() is bounded, so a dead mirror adds at most ~timeout_s to process exit.

Shared by demos/real-estate, demos/bank-assistant and demos/insurance-claims (including
the claims doc-intake service's own venv): keep it free of LangChain/LangGraph imports.
See docs/LANGFUSE_DUAL_INGEST.md.
"""

from __future__ import annotations

import atexit
import base64
import json
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

PROFILES = ("cloud", "selfhosted", "dual")
PROVISION_HINT = "python scripts/langfuse_local.py provision"


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass(frozen=True)
class Target:
    """One Langfuse project: where its data goes and how to authenticate."""

    base_url: str
    public_key: str
    secret_key: str
    project_id: str = ""
    label: str = "self-hosted"

    @classmethod
    def from_env_file(cls, path: Path, label: str = "self-hosted") -> Optional["Target"]:
        """Read a target from an .env file (e.g. .env.selfhosted). None if absent or incomplete."""
        path = Path(path)
        if not path.exists():
            return None
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(path).items() if v}
        url = values.get("LANGFUSE_BASE_URL") or values.get("LANGFUSE_HOST")
        if not (url and values.get("LANGFUSE_PUBLIC_KEY") and values.get("LANGFUSE_SECRET_KEY")):
            return None
        return cls(url.rstrip("/"), values["LANGFUSE_PUBLIC_KEY"], values["LANGFUSE_SECRET_KEY"],
                   values.get("LANGFUSE_PROJECT_ID", ""), label)

    def basic_auth(self) -> str:
        return "Basic " + base64.b64encode(f"{self.public_key}:{self.secret_key}".encode()).decode()

    def trace_url(self, trace_id: str) -> str:
        return f"{self.base_url}/project/{self.project_id}/traces/{trace_id}"


def resolve_profile(requested: Optional[str], *, has_cloud: bool, has_selfhosted: bool,
                    knob: str, demo: str,
                    overlay_exists: Callable[[str], bool] = lambda name: False) -> str:
    """`cloud` | `selfhosted` | `dual`. Unset → dual when both targets are configured.

    Creating .env.selfhosted (scripts/langfuse_local.py provision) is the opt-in to dual;
    CI runners never have one, so they stay Cloud-only. With neither file the default stays
    `selfhosted`, as before: the demo's own .env then holds the target. Any other name is
    accepted as a single-target profile when `overlay_exists(name)` (claims'
    `selfhosted-annuities`).
    """
    profile = (requested or "").strip().lower()
    if not profile:
        if has_cloud and has_selfhosted:
            return "dual"
        return "cloud" if has_cloud else "selfhosted"
    if profile not in PROFILES:
        if overlay_exists(profile):
            return profile
        raise SystemExit(f"{knob}={requested}: expected one of {', '.join(PROFILES)} "
                         f"(or a profile with its own .env.{profile})")
    if profile == "dual" and not has_selfhosted:
        raise SystemExit(f"{knob}=dual but .env.selfhosted is missing — run: {PROVISION_HINT} {demo}")
    if profile == "dual" and not has_cloud:
        raise SystemExit(f"{knob}=dual but the Cloud target (.env.cloud) is missing")
    return profile


class Breaker:
    """Up/down state of one mirror target. Closed = send; open = drop without I/O until retry."""

    def __init__(self, label: str, *, cooldown_s: float = 30.0, auth_cooldown_s: float = 300.0,
                 clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = _log):
        self.label = label
        self.cooldown_s = cooldown_s
        self.auth_cooldown_s = auth_cooldown_s
        self.down_reason: Optional[str] = None
        self.dropped = 0
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._retry_at = 0.0

    @property
    def is_up(self) -> bool:
        return self.down_reason is None

    def allow(self) -> bool:
        with self._lock:
            return self._clock() >= self._retry_at

    def record_success(self) -> None:
        with self._lock:
            if self.down_reason is None:
                return
            self.down_reason, self._retry_at = None, 0.0
        self._log(f"✓ {self.label} Langfuse is reachable again — mirroring resumed")

    def record_failure(self, reason: str, *, auth: bool = False) -> None:
        with self._lock:
            was_up = self.down_reason is None
            self.down_reason = reason
            self._retry_at = self._clock() + (self.auth_cooldown_s if auth else self.cooldown_s)
        if was_up:
            then = (f"check the keys or re-run: {PROVISION_HINT} <demo>" if auth
                    else f"retrying every {self.cooldown_s:.0f}s")
            self._log(f"! {self.label} Langfuse unavailable ({reason}) — primary ingest "
                      f"unaffected; {then}")

    def record_drop(self, count: int = 1) -> None:
        with self._lock:
            self.dropped += count


def _classify(status_code: int) -> Optional[str]:
    """Breaker verdict for an HTTP status: None = target healthy, 'auth' or 'down' otherwise."""
    if status_code in (401, 403):
        return "auth"
    if status_code == 429 or status_code >= 500:
        return "down"
    return None


class MirrorExporter(SpanExporter):
    """OTLP/HTTP export to the mirror: one POST per batch with a hard deadline, behind the breaker.

    Deliberately not OTLPSpanExporter: its retry loop would hold a dead mirror's batch for
    the whole export timeout and log every attempt. One attempt plus the breaker keeps a
    stopped instance to a single warning line.
    """

    def __init__(self, target: Target, breaker: Breaker, *, timeout_s: float = 3.0, http=None):
        import httpx
        from langfuse import __version__ as sdk_version

        self._endpoint = f"{target.base_url}/api/public/otel/v1/traces"
        self._headers = {
            "Authorization": target.basic_auth(),
            "Content-Type": "application/x-protobuf",
            # The server infers v4 (real-time evaluator) eligibility from the SDK headers;
            # the explicit ingestion version covers servers that only read that one.
            "x-langfuse-sdk-name": "python",
            "x-langfuse-sdk-version": sdk_version,
            "x-langfuse-public-key": target.public_key,
            "x-langfuse-ingestion-version": "4",
        }
        self._http = http or httpx.Client(timeout=timeout_s)
        self._breaker = breaker
        self._closed = False
        self.bad_batches = 0

    def export(self, spans: Sequence[Any]):
        from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans

        if self._closed or not self._breaker.allow():
            self._breaker.record_drop(len(spans))
            return SpanExportResult.FAILURE
        try:
            body = encode_spans(spans).SerializePartialToString()
            response = self._http.post(self._endpoint, content=body, headers=self._headers)
        except Exception as error:  # noqa: BLE001 — refused, timeout, DNS: the mirror is down
            self._breaker.record_failure(type(error).__name__)
            self._breaker.record_drop(len(spans))
            return SpanExportResult.FAILURE
        verdict = _classify(response.status_code)
        if verdict is not None:
            self._breaker.record_failure(f"HTTP {response.status_code}", auth=verdict == "auth")
            self._breaker.record_drop(len(spans))
            return SpanExportResult.FAILURE
        if response.status_code >= 400:
            # A rejected batch (e.g. 413) is that batch's problem, not the target's.
            self.bad_batches += 1
            self._breaker.record_drop(len(spans))
            return SpanExportResult.FAILURE
        self._breaker.record_success()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self._closed = True
        self._http.close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def score_body(kwargs: dict, environment: Optional[str]) -> dict:
    """`create_score(**kwargs)` → body for the mirror's POST /api/public/scores (same score id)."""
    value = kwargs.get("value")
    if isinstance(value, bool):  # the public API wants 1/0 for BOOLEAN scores
        value = 1 if value else 0
    body: dict = {"id": kwargs["score_id"], "name": kwargs["name"], "value": value}
    for source, field in (("trace_id", "traceId"), ("observation_id", "observationId"),
                          ("session_id", "sessionId"), ("dataset_run_id", "datasetRunId"),
                          ("data_type", "dataType"), ("comment", "comment"),
                          ("metadata", "metadata")):
        if kwargs.get(source) is not None:
            body[field] = kwargs[source]
    if kwargs.get("environment") or environment:
        body["environment"] = kwargs.get("environment") or environment
    # config_id is NOT copied: score configs are per project, so the primary's id means
    # nothing on the mirror (and an unknown configId is rejected).
    return body


_STOP = object()


class _Worker:
    """A bounded queue drained by one lazily started daemon thread; `flush` waits a bounded time."""

    thread_name = "langfuse-mirror"

    def __init__(self, maxsize: int):
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize)
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _enqueue(self, item: Any) -> bool:
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            return False
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self.thread_name, daemon=True)
                self._thread.start()
        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                self._handle(item)
            except Exception as error:  # noqa: BLE001 — one bad item must not stop the copies
                _log(f"! {self.thread_name}: {type(error).__name__}: {error}")
            finally:
                self._queue.task_done()

    def _handle(self, item: Any) -> None:
        raise NotImplementedError

    def flush(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.02)
        return not self._queue.unfinished_tasks


class ScoreMirror(_Worker):
    """Copies scores to the mirror's public scores API from one background thread."""

    thread_name = "langfuse-score-mirror"

    def __init__(self, target: Target, breaker: Breaker, *, environment: Optional[str] = None,
                 sampler=None, timeout_s: float = 3.0, maxsize: int = 10_000, http=None):
        import httpx

        super().__init__(maxsize)
        self._url = f"{target.base_url}/api/public/scores"
        self._headers = {"Authorization": target.basic_auth(), "Content-Type": "application/json"}
        self._http = http or httpx.Client(timeout=timeout_s)
        self._breaker = breaker
        self._environment = environment
        self._sampler = sampler
        self.rejected = 0

    def _sampled(self, trace_id: Optional[str]) -> bool:
        """Mirror the SDK: a score on a sampled-out trace is dropped, session/run scores never are."""
        if not trace_id or self._sampler is None:
            return True
        try:
            from opentelemetry.sdk.trace.sampling import Decision

            decision = self._sampler.should_sample(None, int(trace_id, 16), "score").decision
            return decision == Decision.RECORD_AND_SAMPLE
        except Exception:  # noqa: BLE001
            return True

    def submit(self, kwargs: dict) -> None:
        if not self._sampled(kwargs.get("trace_id")):
            return
        if not self._enqueue(score_body(kwargs, self._environment)):
            self._breaker.record_drop()

    def _handle(self, body: dict) -> None:
        if not self._breaker.allow():
            self._breaker.record_drop()
            return
        try:
            response = self._http.post(self._url, content=json.dumps(body, default=str),
                                       headers=self._headers)
        except Exception as error:  # noqa: BLE001
            self._breaker.record_failure(type(error).__name__)
            self._breaker.record_drop()
            return
        verdict = _classify(response.status_code)
        if verdict is not None:
            self._breaker.record_failure(f"HTTP {response.status_code}", auth=verdict == "auth")
            self._breaker.record_drop()
        elif response.status_code >= 400:
            self.rejected += 1
            if self.rejected == 1:  # once, so a schema mismatch is visible without spamming
                _log(f"! mirror rejected score '{body.get('name')}': HTTP {response.status_code} "
                     f"{response.text[:200]}")
        else:
            self._breaker.record_success()


class MediaMirror(_Worker):
    """Uploads each image/audio/file the primary uploads, so the copied spans' media resolve too.

    The SDK swaps media in inputs and outputs for `@@@langfuseMedia:...|id=<id>|...@@@`
    references before the span is exported, so the mirror receives references to media it
    never got. The id is derived from the bytes (sha256, base64url, first 22 chars) and the
    server recomputes and checks it, so the same bytes get the same id on every instance:
    the references in the copied spans need no rewriting, only this upload. Same flow as the
    SDK: request an upload URL, PUT the bytes to object storage, report the result.
    """

    thread_name = "langfuse-media-mirror"

    def __init__(self, target: Target, breaker: Breaker, *, timeout_s: float = 3.0,
                 upload_timeout_s: float = 30.0, max_bytes: int = 256 * 2**20,
                 maxsize: int = 1_000, http=None):
        import httpx

        super().__init__(maxsize)
        self._url = f"{target.base_url}/api/public/media"
        self._headers = {"Authorization": target.basic_auth(), "Content-Type": "application/json"}
        self._http = http or httpx.Client(timeout=timeout_s)
        self._breaker = breaker
        self._upload_timeout_s = upload_timeout_s
        self._max_bytes = max_bytes
        self._queued_bytes = 0
        self._seen: "dict[tuple, None]" = {}  # insertion-ordered, bounded: recent uploads only
        self._logged: set = set()
        self.failed = 0

    def submit(self, media, *, trace_id: Optional[str], observation_id: Optional[str],
               field: str) -> None:
        content = getattr(media, "_content_bytes", None)
        media_id = getattr(media, "_media_id", None)
        if not (content and media_id and media._content_type and media._content_sha256_hash):
            return
        key = (media_id, trace_id, observation_id, field)
        with self._lock:
            # The same item can arrive twice: from the primary's upload and from the mirror's own
            # export-stage processing of a third-party span. One upload per (media, location).
            if key in self._seen:
                return
            self._seen[key] = None
            if len(self._seen) > 10_000:
                self._seen.pop(next(iter(self._seen)))
            if self._queued_bytes + len(content) > self._max_bytes:
                self._breaker.record_drop()
                return
            self._queued_bytes += len(content)
        job = {"media_id": media_id, "content": content, "content_type": media._content_type,
               "sha256": media._content_sha256_hash, "trace_id": trace_id,
               "observation_id": observation_id, "field": field}
        if not self._enqueue(job):
            with self._lock:
                self._queued_bytes -= len(content)
            self._breaker.record_drop()

    def _handle(self, job: dict) -> None:
        try:
            self._upload(job)
        finally:
            with self._lock:
                self._queued_bytes -= len(job["content"])

    def _log_once(self, key: str, message: str) -> None:
        if key not in self._logged:
            self._logged.add(key)
            _log(message)

    def _upload(self, job: dict) -> None:
        if not self._breaker.allow():
            self._breaker.record_drop()
            return
        request = {"traceId": job["trace_id"], "observationId": job["observation_id"],
                   "contentType": job["content_type"], "contentLength": len(job["content"]),
                   "sha256Hash": job["sha256"], "field": job["field"]}
        try:
            response = self._http.post(self._url, content=json.dumps(request), headers=self._headers)
        except Exception as error:  # noqa: BLE001
            self._breaker.record_failure(type(error).__name__)
            self._breaker.record_drop()
            return
        verdict = _classify(response.status_code)
        if verdict is not None:
            self._breaker.record_failure(f"HTTP {response.status_code}", auth=verdict == "auth")
            self._breaker.record_drop()
            return
        if response.status_code >= 400:
            self.failed += 1
            self._log_once("request", f"! mirror refused a media upload: HTTP {response.status_code} "
                                      f"{response.text[:200]}")
            return
        self._breaker.record_success()
        answer = response.json()
        upload_url = answer.get("uploadUrl")
        if not upload_url:
            return  # the mirror already has these bytes
        if answer.get("mediaId") != job["media_id"]:
            self.failed += 1
            self._log_once("id", f"! mirror media id {answer.get('mediaId')} != {job['media_id']}: "
                                 "upload skipped")
            return
        headers = {"Content-Type": job["content_type"]}
        if "storage.googleapis.com" not in upload_url:  # same rule as the SDK
            headers.update({"x-ms-blob-type": "BlockBlob", "x-amz-checksum-sha256": job["sha256"]})
        started = time.monotonic()
        try:
            put = self._http.put(upload_url, content=job["content"], headers=headers,
                                 timeout=self._upload_timeout_s)
        except Exception as error:  # noqa: BLE001 — object storage, not the web server
            self.failed += 1
            host = upload_url.split("/")[2] if "//" in upload_url else upload_url
            self._log_once("storage", f"! mirror media storage unreachable at {host} "
                                      f"({type(error).__name__}): images/audio stay missing there")
            return
        if put.status_code >= 400:
            self.failed += 1
            self._log_once("put", f"! mirror media upload failed: HTTP {put.status_code}")
        try:
            self._http.patch(f"{self._url}/{job['media_id']}", headers=self._headers, content=json.dumps({
                "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                "uploadHttpStatus": put.status_code,
                "uploadHttpError": put.text[:1000] if put.status_code >= 400 else None,
                "uploadTimeMs": int((time.monotonic() - started) * 1000)}))
        except Exception:  # noqa: BLE001 — the bytes are there; the status is cosmetic
            pass


class Mirror:
    """Handle on an attached mirror: status, trace links and a bounded flush."""

    def __init__(self, target: Target, breaker: Breaker, processor, exporter: MirrorExporter,
                 scores: ScoreMirror, timeout_s: float, media: Optional[MediaMirror] = None):
        self.target = target
        self.breaker = breaker
        self.processor = processor
        self.exporter = exporter
        self.scores = scores
        self.media = media
        self.timeout_s = timeout_s

    @property
    def is_up(self) -> bool:
        return self.breaker.is_up

    def trace_url(self, trace_id: str) -> str:
        return self.target.trace_url(trace_id)

    def flush(self, timeout_s: Optional[float] = None) -> None:
        """Export what the mirror has queued, waiting at most `timeout_s` in total."""
        budget = self.timeout_s if timeout_s is None else timeout_s
        deadline = time.monotonic() + budget
        self.processor.force_flush(int(budget * 1000))
        self.scores.flush(max(deadline - time.monotonic(), 0.0))
        if self.media is not None:
            self.media.flush(max(deadline - time.monotonic(), 0.0))

    def close(self) -> None:
        """atexit: bounded flush, then stop sending so a later provider shutdown cannot block."""
        try:
            self.flush()
        finally:
            self.exporter.shutdown()


def probe(target: Target, timeout_s: float = 1.5) -> Optional[str]:
    """None if `target` is reachable AND accepts its keys; otherwise the reason it is not."""
    import httpx

    try:
        response = httpx.get(f"{target.base_url}/api/public/projects",
                             headers={"Authorization": target.basic_auth()}, timeout=timeout_s)
    except Exception as error:  # noqa: BLE001
        return f"unreachable at {target.base_url}: {type(error).__name__}"
    if response.status_code in (401, 403):
        return f"keys rejected by {target.base_url} (HTTP {response.status_code})"
    if response.status_code >= 400:
        return f"{target.base_url} answered HTTP {response.status_code}"
    return None


def attach(client, target: Target, *, primary_public_key: str, provider=None,
           should_export_span: Optional[Callable[[Any], bool]] = None,
           mask_otel_spans: Optional[Callable[..., Any]] = None,
           environment: Optional[str] = None, timeout_s: float = 3.0,
           cooldown_s: float = 30.0) -> Optional[Mirror]:
    """Mirror `client`'s spans and scores to `target`. Never raises; returns None if impossible.

    Call right after constructing the Langfuse client, with the SAME should_export_span and
    mask_otel_spans it was given. `provider` defaults to the global tracer provider (what
    the SDK installs when the app passes none).
    """
    try:
        from langfuse._client.span_processor import LangfuseSpanProcessor
        from opentelemetry import trace as otel_trace

        provider = provider or otel_trace.get_tracer_provider()
        if not hasattr(provider, "add_span_processor"):
            _log(f"! cannot mirror to {target.base_url}: tracer provider "
                 f"{type(provider).__name__} is not the SDK provider")
            return None

        breaker = Breaker(target.label, cooldown_s=cooldown_s)
        reason = probe(target)
        if reason:
            breaker.record_failure(reason, auth="keys rejected" in reason)

        exporter = MirrorExporter(target, breaker, timeout_s=timeout_s)
        media = MediaMirror(target, breaker, timeout_s=timeout_s)
        processor = LangfuseSpanProcessor(
            public_key=primary_public_key,  # project-scope filter only: accept the one client's spans
            secret_key=target.secret_key,
            base_url=target.base_url,
            span_exporter=exporter,
            media_manager=_mirror_media_manager(client, media),
            should_export_span=should_export_span,
            mask_otel_spans=mask_otel_spans,
        )
        provider.add_span_processor(processor)
        scores = ScoreMirror(target, breaker, environment=environment,
                             sampler=getattr(provider, "sampler", None), timeout_s=timeout_s)
        mirror = Mirror(target, breaker, processor, exporter, scores, timeout_s, media)
        _wrap_create_score(client, scores)
        _wrap_media_uploads(client, media)
        atexit.register(mirror.close)
        if breaker.is_up:
            _log(f"✓ mirroring traces, scores and media to {target.label} Langfuse at "
                 f"{target.base_url}")
        return mirror
    except Exception as error:  # noqa: BLE001 — the mirror is optional; the primary must start
        _log(f"! {target.label} mirror disabled: {type(error).__name__}: {error}")
        return None


def _wrap_create_score(client, scores: ScoreMirror) -> None:
    """Send each score to the primary as before, then queue a copy with the SAME score id.

    Patched on the instance, so every `client.create_score` call is covered, including the
    evaluator scores `dataset.run_experiment(...)` writes through `self.create_score`.
    (Clients returned by get_client() are separate wrappers and are not covered.)
    """
    original = client.create_score

    def create_score(**kwargs):
        if not kwargs.get("score_id"):
            kwargs["score_id"] = uuid.uuid4().hex
        result = original(**kwargs)
        try:
            scores.submit(kwargs)
        except Exception:  # noqa: BLE001 — never let the copy break the caller
            pass
        return result

    client.create_score = create_score


def _wrap_media_uploads(client, uploads: MediaMirror) -> None:
    """Queue a mirror upload for every media item the primary's media manager uploads.

    The SDK calls `_process_media` for media in span inputs/outputs/metadata (sampled spans
    only) and for export-stage media in third-party spans. Patched on the instance, which
    shadows the class method.
    """
    manager = getattr(getattr(client, "_resources", None), "_media_manager", None)
    if manager is None:
        return
    original = manager._process_media

    def _process_media(*, media: Any, trace_id: str, observation_id: Optional[str], field: str):
        original(media=media, trace_id=trace_id, observation_id=observation_id, field=field)
        try:
            uploads.submit(media, trace_id=trace_id, observation_id=observation_id, field=field)
        except Exception:  # noqa: BLE001 — never let the copy break the caller
            pass

    manager._process_media = _process_media


def _mirror_media_manager(client, uploads: MediaMirror):
    """Media manager for the mirror's own export stage: swaps base64 media in third-party spans
    for the same references the primary writes, and queues the bytes for the mirror only."""
    from langfuse._task_manager.media_manager import MediaManager

    primary = getattr(getattr(client, "_resources", None), "_media_manager", None)

    class _MirrorMediaManager(MediaManager):
        # Only _find_and_process_media is used here, and it needs nothing but these two.
        def __init__(self) -> None:  # no API client: the bytes go through `uploads`
            self._enabled = getattr(primary, "_enabled", True)

        def _process_media(self, *, media: Any, trace_id: str, observation_id: Optional[str],
                           field: str) -> None:
            uploads.submit(media, trace_id=trace_id, observation_id=observation_id, field=field)

    return _MirrorMediaManager()

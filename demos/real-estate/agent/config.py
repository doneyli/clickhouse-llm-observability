"""
Shared configuration for the Real Estate Property Concierge demo.

Key-isolation is the #1 landmine: this demo targets a *dedicated* Langfuse
project ("real-estate" by default; override with LANGFUSE_PROJECT_NAME, e.g.
for a Langfuse Cloud project named differently). If the surrounding shell has
other LANGFUSE_* keys exported, every trace/dataset/score would silently land
in the wrong project.

To prevent that we:
  1. Load this folder's .env explicitly.
  2. HARD-SET os.environ (override, never setdefault) from those values.
  3. Instantiate Langfuse() with the keys explicitly.
  4. verify_project() confirms the keys resolve to the expected project name.
"""

import os
import sys
import base64
import urllib.request
import urllib.error
import urllib.parse
import json
from pathlib import Path
from typing import Optional

import threading

from dotenv import load_dotenv, dotenv_values

# --- Load this folder's .env (overriding any inherited shell values) ---
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)

LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

# HARD override so any get_client()/SDK path uses the real-estate project keys.
os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST

# Override with LANGFUSE_PROJECT_NAME when targeting e.g. a Langfuse Cloud
# project that isn't named "real-estate".
EXPECTED_PROJECT = os.environ.get("LANGFUSE_PROJECT_NAME", "real-estate")

AGENT_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-sonnet-4-6")
# The OpenAI model used for the Claude-vs-GPT comparison (agent side).
OPENAI_AGENT_MODEL = os.environ.get("OPENAI_AGENT_MODEL", "gpt-4o")
# Accept either spelling of the OpenAI key.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_AI_API_KEY")

# Tags applied to every trace so the demo's traffic is easy to filter in the UI.
BASE_TAGS = ["real-estate", "property-concierge"]

# --- Optional trace mirror (e.g. self-hosted primary + Langfuse Cloud) -------
# When all three are set, every span — live, portal AND experiment — is ALSO
# exported to the mirror project with the same trace ids. Scores are duplicated
# only on the live-traffic/portal paths (via record_score below); experiment
# evaluation scores, prompts, datasets, dataset runs and managed evaluators
# exist only on the primary, so experiment traces appear on the mirror without
# scores or run linkage.
# Read STRICTLY from this folder's .env (never the shell environment): the
# primary keys' shell-override landmine applies here too, and a stale
# shell-exported LANGFUSE_MIRROR_* must not silently enable mirroring.
_env_file = dotenv_values(_ENV_PATH)
MIRROR_PUBLIC_KEY = _env_file.get("LANGFUSE_MIRROR_PUBLIC_KEY")
MIRROR_SECRET_KEY = _env_file.get("LANGFUSE_MIRROR_SECRET_KEY")
MIRROR_HOST = (_env_file.get("LANGFUSE_MIRROR_HOST") or "").rstrip("/") or None
MIRROR_ENABLED = bool(MIRROR_PUBLIC_KEY and MIRROR_SECRET_KEY and MIRROR_HOST)

_langfuse = None
_anthropic = None
_mirror_attached = False
_mirror_processor = None
# The portal serves sync FastAPI handlers from a thread pool; without a lock,
# two cold-start requests could each attach a mirror processor (spans then
# mirrored twice, one processor orphaned from flush/atexit).
_langfuse_lock = threading.Lock()


def _attach_mirror() -> None:
    """Fan spans out to the mirror Langfuse via a second OTLP exporter.

    The SDK's own LangfuseSpanProcessor filters spans by public key (so a
    second Langfuse *client* would reject the primary's spans); a plain
    BatchSpanProcessor on the same tracer provider exports everything —
    identical spans, identical trace ids, on both backends.
    """
    global _mirror_attached
    if _mirror_attached or not MIRROR_ENABLED:
        return
    import base64 as _b64

    from opentelemetry import trace as _otel_trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = _otel_trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        print(f"WARN: cannot attach Langfuse mirror ({MIRROR_HOST}): "
              f"tracer provider {type(provider).__name__} is not the SDK provider.",
              file=sys.stderr)
        return
    auth = _b64.b64encode(f"{MIRROR_PUBLIC_KEY}:{MIRROR_SECRET_KEY}".encode()).decode()
    global _mirror_processor
    _mirror_processor = BatchSpanProcessor(OTLPSpanExporter(
        endpoint=f"{MIRROR_HOST}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}",
                 "x-langfuse-public-key": MIRROR_PUBLIC_KEY,
                 # Observation-level (new-model) evaluators only execute in
                 # real time on v4-ingested data; without this header the
                 # mirror's traces render fine but judges never fire.
                 "x-langfuse-ingestion-version": "4"},
    ))
    provider.add_span_processor(_mirror_processor)
    # The Langfuse client's flush()/atexit only cover ITS OWN processor —
    # without these two hooks, short-lived scripts exit before the mirror
    # batch exports and the mirrored trace is silently lost.
    import atexit
    atexit.register(_mirror_processor.shutdown)
    _mirror_attached = True
    print(f"✓ Mirroring traces to {MIRROR_HOST}")


def flush_langfuse(lf=None) -> None:
    """Flush the primary client AND the mirror processor (if attached).

    The mirror flush is capped at 3s so an unreachable mirror adds at most a
    short delay to a turn/feedback request instead of hanging it.
    """
    (lf or get_langfuse()).flush()
    if _mirror_processor is not None:
        _mirror_processor.force_flush(3_000)


def get_langfuse():
    """Return a singleton Langfuse client bound to the real-estate project keys."""
    global _langfuse
    with _langfuse_lock:
        if _langfuse is None:
            from langfuse import Langfuse

            _langfuse = Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                host=LANGFUSE_HOST,
            )
            _attach_mirror()
    return _langfuse


def record_score(lf, **kwargs) -> None:
    """create_score on the primary AND (best-effort) on the mirror.

    Trace/observation ids are identical on both backends (same OTel spans),
    so the same payload lands on the mirror via its public scores API.
    """
    lf.create_score(**kwargs)
    if not MIRROR_ENABLED:
        return
    value = kwargs.get("value")
    if isinstance(value, bool):  # public API wants 1/0 for BOOLEAN scores
        value = 1 if value else 0
    body = {
        "traceId": kwargs.get("trace_id"),
        "name": kwargs.get("name"),
        "value": value,
    }
    if kwargs.get("observation_id"):
        body["observationId"] = kwargs["observation_id"]
    if kwargs.get("data_type"):
        body["dataType"] = kwargs["data_type"]
    if kwargs.get("comment"):
        body["comment"] = kwargs["comment"]
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{MIRROR_HOST}/api/public/scores", data=data, method="POST",
            headers={"Authorization": "Basic " + base64.b64encode(
                f"{MIRROR_PUBLIC_KEY}:{MIRROR_SECRET_KEY}".encode()).decode(),
                "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # mirror is best-effort; never break the demo
        print(f"WARN: mirror score '{kwargs.get('name')}' failed: {e}", file=sys.stderr)


def get_anthropic():
    """Return a singleton Anthropic client."""
    global _anthropic
    if _anthropic is None:
        import anthropic

        _anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


_openai = None


def get_openai():
    """Return a singleton OpenAI client (used when the agent runs on a GPT model)."""
    global _openai
    if _openai is None:
        if not OPENAI_API_KEY:
            raise RuntimeError(
                "OPENAI_API_KEY / OPEN_AI_API_KEY not set — needed to run the agent "
                "on an OpenAI model. Add it to demos/real-estate/.env."
            )
        import openai

        _openai = openai.OpenAI(api_key=OPENAI_API_KEY)
    return _openai


def _basic_auth() -> str:
    return base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()


def langfuse_api(method: str, path: str, body=None, timeout: int = 20):
    """Call the Langfuse REST API with the project's keys.

    Returns (status_code, parsed_json). HTTP error responses are returned as
    (code, {"error": ...}); connection failures raise urllib.error.URLError.
    Shared by every entrypoint so auth/timeout/error handling live in one place.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{LANGFUSE_HOST}{path}", data=data, method=method,
        headers={"Authorization": f"Basic {_basic_auth()}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read(300).decode(errors="replace")}


def _version_hint(path: str, status: int) -> str:
    """Explain a 404 on a v2 read endpoint as the server-version issue it usually is.

    The **Observations API v2** is unsupported on self-hosted OSS v3, so pointing
    this demo at the repo's local stack (LANGFUSE_HOST defaults to
    http://localhost:3001, which runs a v3 server) makes these reads 404 with no
    hint as to why. Scores API v3 is fine on v3 (≥ 3.63.0); it is specifically
    `/v2/` that needs a v4 server or Langfuse Cloud.
    """
    if status != 404 or "/v2/" not in path:
        return ""
    try:
        _, health = langfuse_api("GET", "/api/public/health", timeout=5)
        version = health.get("version", "unknown")
    except Exception:
        version = "unreachable"
    return (f"\n  HINT: {path} requires a Langfuse v4 server or Langfuse Cloud. "
            f"{LANGFUSE_HOST} reports version {version}. "
            f"This demo is designed against Langfuse Cloud — check LANGFUSE_HOST in "
            f"demos/real-estate/.env.")


def _paginate(path: str, params: dict, timeout: int = 20) -> list:
    """Collect every page of a cursor-paginated v2/v3 list endpoint.

    The v1 list endpoints were page-based; v2/v3 are cursor-based and signal the
    final page by OMITTING `meta.cursor` (an empty `meta` is a complete result,
    not an error). Callers get one flat list.
    """
    out: list = []
    cursor = None
    while True:
        q = dict(params)
        if cursor:
            q["cursor"] = cursor
        status, data = langfuse_api(
            "GET", f"{path}?{urllib.parse.urlencode(q)}", timeout=timeout)
        if status != 200:
            raise RuntimeError(
                f"GET {path} -> {status}: {data.get('error')}{_version_hint(path, status)}")
        out.extend(data.get("data") or [])
        cursor = (data.get("meta") or {}).get("cursor")
        if not cursor:
            return out


def list_observations(trace_id: str, *, fields: str = "core,basic",
                      limit: int = 100) -> list:
    """Observations of a trace, via `GET /api/public/v2/observations`.

    Replaces the deprecated `GET /api/public/traces/{id}` + `.observations`.
    Note `input`/`output` are returned as RAW JSON STRINGS (the v2 endpoint
    rejects `parseIoAsJson` outright), so parse them client-side — see
    `observation_io`. Ask for the `io` field group to get them at all.
    """
    return _paginate("/api/public/v2/observations",
                     {"traceId": trace_id, "limit": limit, "fields": fields})


def root_observation(observations: list) -> Optional[dict]:
    """The logical root of a trace, whose input/output ARE the trace's.

    Match on the `isRootObservation` flag, never on `parentObservationId is
    None`: the SDK can mark an observation as the app root while it still has a
    non-null parent, and the observation-level evaluators key off the same flag.
    Requires the `basic` field group.
    """
    return next((o for o in observations if o.get("isRootObservation")), None)


def observation_io(observation: dict, key: str):
    """Parse a v2 observation's `input`/`output` (raw string) into JSON if it is JSON."""
    raw = (observation or {}).get(key)
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw  # plain text output (e.g. the concierge's answer) — not JSON


def root_observations_by_tag(tag: str, *, limit: int = 50,
                             fields: str = "core,basic") -> list:
    """Root observations of traces carrying `tag`, newest first.

    Replaces the deprecated `GET /api/public/traces?tags=...`. v2 is
    observation-scoped, so constrain to `isRootObservation` to get exactly one
    row per trace rather than one per observation. Deliberately a SINGLE page:
    callers want the newest N, and following the cursor here would walk the
    project's entire history.
    """
    filter_conditions = json.dumps([
        {"type": "arrayOptions", "column": "traceTags",
         "operator": "any of", "value": [tag]},
        {"type": "boolean", "column": "isRootObservation",
         "operator": "=", "value": True},
    ])
    path = "/api/public/v2/observations"
    status, data = langfuse_api("GET", path + "?" +
                               urllib.parse.urlencode({"filter": filter_conditions,
                                                       "limit": limit,
                                                       "fields": fields}))
    if status != 200:
        raise RuntimeError(f"v2/observations (tag={tag}) -> {status}: "
                           f"{data.get('error')}{_version_hint(path, status)}")
    return data.get("data") or []


def list_scores(trace_id: str, *, fields: str = "subject", limit: int = 100) -> list:
    """Scores of a trace, via `GET /api/public/v3/scores`.

    Replaces the removed `GET /api/public/scores` and the deprecated
    `GET /api/public/traces/{id}` + `.scores`. v3 moved the target of a score
    into a `subject` object, so the flat `observationId` field is GONE — use
    `score_observation_id()` rather than `score["observationId"]`, which now
    silently reads as None on every score.
    """
    return _paginate("/api/public/v3/scores",
                     {"traceId": trace_id, "limit": limit, "fields": fields})


def score_observation_id(score: dict) -> Optional[str]:
    """The observation a score is attached to, or None for a trace-level score.

    v3 shape: `subject = {"kind": "observation"|"trace", "id": ..., "traceId": ...}`.
    Requires the `subject` field group.
    """
    subject = score.get("subject") or {}
    return subject.get("id") if subject.get("kind") == "observation" else None


def verify_project(quiet: bool = False) -> str:
    """
    Confirm the configured keys resolve to EXPECTED_PROJECT.

    Returns the project name. Exits the process if the keys are wrong so we
    never silently pollute another project (the key-shadowing landmine).
    """
    try:
        _, data = langfuse_api("GET", "/api/public/projects", timeout=10)
    except urllib.error.URLError as e:
        print(f"ERROR: cannot reach Langfuse at {LANGFUSE_HOST}: {e}", file=sys.stderr)
        sys.exit(1)

    projects = data.get("data", [])
    names = [p.get("name") for p in projects]
    if EXPECTED_PROJECT not in names:
        print(
            f"ERROR: configured keys resolve to project(s) {names}, "
            f"expected '{EXPECTED_PROJECT}'. Refusing to run so we don't "
            f"pollute the wrong project.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not quiet:
        print(f"✓ Langfuse project verified: {EXPECTED_PROJECT} @ {LANGFUSE_HOST}")
    return EXPECTED_PROJECT

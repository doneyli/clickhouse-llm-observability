"""
Shared configuration for the Real Estate Property Concierge demo.

Key-isolation is the #1 landmine: this demo targets a *dedicated* Langfuse
project ("real-estate" by default; override with LANGFUSE_PROJECT_NAME, e.g.
for a Langfuse Cloud project named differently). If the surrounding shell has
other LANGFUSE_* keys exported, every trace/dataset/score would silently land
in the wrong project.

To prevent that we:
  1. Load this folder's .env explicitly.
  2. HARD-SET os.environ (override, never setdefault) from those values —
     including LANGFUSE_BASE_URL, which SDK v4 reads and which outranks a
     `host=` constructor argument.
  3. Instantiate Langfuse() with the keys explicitly, passing `base_url=`
     (highest precedence of all — no env var can override it).
  4. verify_project() confirms the keys resolve to the expected project name.

Targets: REAL_ESTATE_PROFILE = cloud | selfhosted | dual (see PROFILE below and
docs/LANGFUSE_DUAL_INGEST.md). In dual mode the .env target stays the primary and the
shared local self-hosted instance gets a best-effort copy of every span and score.

Requires Langfuse Python SDK v4 (`langfuse>=4.10,<5.0` — see requirements.txt for
why the floor is 4.10 rather than the repo-wide 4.7).
"""

import os
import sys
import base64
import urllib.request
import urllib.error
import urllib.parse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import threading

from dotenv import load_dotenv, dotenv_values

# --- Load this folder's .env (overriding any inherited shell values) ---
_DEMO_DIR = Path(__file__).resolve().parent.parent
_ENV_PATH = _DEMO_DIR / ".env"
_SELFHOSTED_PATH = _DEMO_DIR / ".env.selfhosted"
# Read BEFORE .env is loaded with override=True, so a one-off shell value wins, as in the bank
# and claims demos: `REAL_ESTATE_PROFILE=cloud ./.venv/bin/python scripts/run_live_traffic.py`.
_PROFILE_FROM_SHELL = os.environ.get("REAL_ESTATE_PROFILE")
load_dotenv(_ENV_PATH, override=True)

# Cloud + self-hosted dual ingest, shared with the other standalone demos (demos/_shared).
sys.path.insert(0, str(_DEMO_DIR.parent / "_shared"))
import langfuse_dual  # noqa: E402

# Target profile. `cloud`: the target in .env (Langfuse Cloud by default). `selfhosted`:
# .env.selfhosted — the shared local instance, written by
# `python scripts/langfuse_local.py provision real-estate` — replaces .env's Langfuse settings.
# `dual`: .env's target stays the primary and every span and score is mirrored, best-effort,
# to the local instance. Unset → dual when .env.selfhosted exists.
PROFILE = langfuse_dual.resolve_profile(
    _PROFILE_FROM_SHELL or os.environ.get("REAL_ESTATE_PROFILE"),
    has_cloud=True, has_selfhosted=_SELFHOSTED_PATH.exists(),
    knob="REAL_ESTATE_PROFILE", demo="real-estate")
if PROFILE == "selfhosted" and _SELFHOSTED_PATH.exists():
    load_dotenv(_SELFHOSTED_PATH, override=True)

# PII redaction policy. Imported after load_dotenv so LANGFUSE_MASK_PII can be
# set in .env; deliberately NOT listed in .env, so `LANGFUSE_MASK_PII=false
# <cmd>` works from the shell for the masked/unmasked comparison.
from agent import masking  # noqa: E402  (must follow load_dotenv)

LANGFUSE_PUBLIC_KEY = os.environ["LANGFUSE_PUBLIC_KEY"]
LANGFUSE_SECRET_KEY = os.environ["LANGFUSE_SECRET_KEY"]
# Accept either spelling from .env: v4 standardizes on LANGFUSE_BASE_URL, but this
# demo (and the repo's other .env files) historically use LANGFUSE_HOST.
LANGFUSE_HOST = (os.environ.get("LANGFUSE_BASE_URL")
                 or os.environ.get("LANGFUSE_HOST")
                 or "http://localhost:3001")

# HARD override so any get_client()/SDK path uses the real-estate project keys.
os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
os.environ["LANGFUSE_HOST"] = LANGFUSE_HOST
# SDK v4 reads LANGFUSE_BASE_URL, and it OUTRANKS a `host=` constructor arg. Pin it
# to the same value so a stale inherited LANGFUSE_BASE_URL can never silently
# redirect this demo's traces to another backend (only `base_url=` beats it, which
# is what get_langfuse() passes).
os.environ["LANGFUSE_BASE_URL"] = LANGFUSE_HOST

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

# --- The dual-mode mirror -------------------------------------------------------
# In dual mode every span — live, portal AND experiment — and every score written through
# the client (record_score, experiment evaluators) is ALSO sent to the local self-hosted
# instance, with the same trace, observation and score ids, the same span filter and the
# same PII redaction. Prompts, datasets, dataset-run links and managed evaluators stay
# per instance, so experiment traces appear on the mirror without run linkage.
# The mirror is read STRICTLY from files (never the shell): the primary keys'
# shell-override landmine applies here too.
if PROFILE == "dual":
    MIRROR = langfuse_dual.Target.from_env_file(_SELFHOSTED_PATH, label="self-hosted")
else:
    # Deprecated alias: the three LANGFUSE_MIRROR_* variables in .env (any second Langfuse).
    _env_file = dotenv_values(_ENV_PATH)
    _legacy = [_env_file.get(f"LANGFUSE_MIRROR_{k}") for k in ("HOST", "PUBLIC_KEY", "SECRET_KEY")]
    MIRROR = (langfuse_dual.Target(_legacy[0].rstrip("/"), _legacy[1], _legacy[2], label="mirror")
              if all(_legacy) else None)
MIRROR_ENABLED = MIRROR is not None

_langfuse = None
_anthropic = None
_mirror: Optional["langfuse_dual.Mirror"] = None
# The portal serves sync FastAPI handlers from a thread pool; without a lock,
# two cold-start requests could each attach a mirror processor (spans then
# mirrored twice, one processor orphaned from flush/atexit).
_langfuse_lock = threading.Lock()


def flush_langfuse(lf=None) -> None:
    """Flush the primary client AND the mirror (if attached).

    The mirror flush is bounded (a few seconds at most), so an unreachable mirror adds
    only a short delay to a turn/feedback request instead of hanging it.
    """
    (lf or get_langfuse()).flush()
    if _mirror is not None:
        _mirror.flush()


def get_langfuse():
    """Return a singleton Langfuse client bound to the real-estate project keys."""
    global _langfuse, _mirror
    with _langfuse_lock:
        if _langfuse is None:
            from langfuse import Langfuse

            # Redact PII in span attributes at EXPORT stage, inside this
            # process. `mask_otel_spans` (SDK >= 4.9.0) sees the final raw OTel
            # attributes of every span this client exports — including spans
            # from third-party instrumentation, which the legacy `mask=` hook
            # never sees. See agent/masking.py for the policy and its limits.
            mask = masking.mask_otel_spans if masking.enabled() else None

            # `base_url=` (not `host=`): in SDK v4 it has the highest precedence of
            # all and cannot be overridden by a LANGFUSE_BASE_URL env var, so the
            # explicit per-project value always wins.
            _langfuse = Langfuse(
                public_key=LANGFUSE_PUBLIC_KEY,
                secret_key=LANGFUSE_SECRET_KEY,
                base_url=LANGFUSE_HOST,
                **({"mask_otel_spans": mask} if mask else {}),
            )
            if MIRROR is not None:
                # The mirror runs the SAME mask hook, so redaction and mirroring now
                # combine (the old plain-OTLP mirror could not, and refused to start).
                _mirror = langfuse_dual.attach(
                    _langfuse, MIRROR, primary_public_key=LANGFUSE_PUBLIC_KEY,
                    mask_otel_spans=mask)
    return _langfuse


def record_score(lf, **kwargs) -> None:
    """create_score on the primary — and, in dual mode, the same score on the mirror.

    The mirror copy needs nothing here: langfuse_dual wraps the client's create_score,
    so it is sent with the same score id from a background queue.

    A score references EXACTLY ONE subject: a trace, an observation, a session,
    or a dataset run. `session_id=` is the only way to score a whole
    conversation — no Langfuse-managed evaluator can target a session, because
    the server cannot know when a conversation has ended. Session scores are
    therefore always written from here.
    """
    lf.create_score(**kwargs)


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


def sessions_with_turns(*, trace_name: Optional[str] = None,
                        lookback_days: int = 90, limit: int = 1000,
                        fields: str = "core,basic,io") -> "dict[str, list]":
    """Sessions and their turns in one call, newest session first.

    Replaces the deprecated `GET /api/public/sessions` (removed from Langfuse
    Cloud on 2026-11-16). v4 has no session entity: a session is just the rows
    sharing a `sessionId`, so one `v2/observations` read answers both "which
    sessions exist" and "what turns are in them" — where the old pair of calls
    needed a session list *then* a lookup of each one's turns.

    Pass `trace_name` to keep the pool to real turns: a demo project also
    collects sessions from the verification scripts, and filtering server-side
    beats fetching them and discarding them. Results are sorted by `startTime`
    descending, so the returned mapping is in newest-session-first order.

    `lookback_days` bounds the query as the Observations v2 docs advise — but it
    is also a silent cutoff: a caller that wants the longest conversation rather
    than the most recent one should widen it past the age of the project's
    seeded data, not accept the default.
    """
    conditions = [{"type": "boolean", "column": "isRootObservation",
                   "operator": "=", "value": True}]
    if trace_name:
        conditions.append({"type": "string", "column": "traceName",
                           "operator": "=", "value": trace_name})
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = _paginate("/api/public/v2/observations",
                     {"filter": json.dumps(conditions), "limit": limit,
                      "fields": fields,
                      "fromStartTime": since.strftime("%Y-%m-%dT%H:%M:%SZ")})
    by_session: "dict[str, list]" = {}
    for observation in rows:
        session_id = observation.get("sessionId")
        if session_id:
            by_session.setdefault(session_id, []).append(observation)
    return by_session


def root_observations_by_sessions(session_ids: Sequence[str], *,
                                  fields: str = "core,basic") -> list:
    """Root observations — one row per TURN — for a batch of sessions.

    Filtering on `sessionId` needs the **stringOptions / "any of"** filter type.
    `{"type": "string", "column": "sessionId", "operator": "="}` is accepted with
    a 200 and returns ZERO rows: a silent empty result rather than an error, so a
    per-session "=" lookup reads as "that conversation has no turns". Batching
    every id into one "any of" call is both correct and a single round trip.

    Ask for the `io` field group as well to get each turn's input/output (raw
    JSON strings — parse with `observation_io`).
    """
    if not session_ids:
        return []
    filter_conditions = json.dumps([
        {"type": "stringOptions", "column": "sessionId",
         "operator": "any of", "value": list(session_ids)},
        {"type": "boolean", "column": "isRootObservation",
         "operator": "=", "value": True},
    ])
    return _paginate("/api/public/v2/observations",
                     {"filter": filter_conditions, "limit": 100, "fields": fields})


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


# --- Experiments (formerly "dataset runs") ------------------------------------
#
# `GET /datasets/{name}/runs/{runName}` and its siblings are deprecated and are
# removed from Langfuse Cloud on 2026-11-16. The replacement is a two-step read:
# resolve the dataset ID, find the experiment by name, then list its items.
# Unlike the old run response, items carry their scores inline (`fields=scores`),
# so the per-item "fetch the trace to collect its scores" round trip is gone.

# Both experiment endpoints REQUIRE `fromStartTime`. This demo's experiments are
# all recent, but a generous window costs nothing and keeps a long-lived project
# readable; override for a project with a longer history.
EXPERIMENT_LOOKBACK_DAYS = int(os.environ.get("LANGFUSE_EXPERIMENT_LOOKBACK_DAYS", "400"))


def _experiment_window() -> str:
    """`fromStartTime` far enough back to include every experiment we may look up."""
    since = datetime.now(timezone.utc) - timedelta(days=EXPERIMENT_LOOKBACK_DAYS)
    return since.strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_dataset_id(dataset_name: str) -> Optional[str]:
    """A dataset's ID, via `GET /api/public/v2/datasets/{name}`.

    Experiments are queried by dataset **ID**, never by name, so every
    experiment read starts here. Returns None if the dataset does not exist.
    """
    path = f"/api/public/v2/datasets/{urllib.parse.quote(dataset_name, safe='')}"
    status, data = langfuse_api("GET", path)
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"GET {path} -> {status}: {data.get('error')}")
    return data.get("id")


def find_experiment(dataset_name: str, run_name: str, *,
                    fields: str = "core") -> Optional[dict]:
    """One experiment by dataset name + run name, via `GET /api/public/experiments`.

    Replaces `GET /api/public/datasets/{name}/runs/{runName}`. Ask for the
    `scores` field group to get the run-level aggregate scores
    (`subject.kind == "experiment"`). Returns None when either the dataset or
    the named experiment is absent — the deprecated endpoint 404'd in both cases.
    """
    dataset_id = resolve_dataset_id(dataset_name)
    if dataset_id is None:
        return None
    rows = _paginate("/api/public/experiments",
                     {"datasetId": dataset_id, "name": run_name,
                      "fromStartTime": _experiment_window(),
                      "limit": 100, "fields": fields})
    # `name` is a filter, not an exact-match lookup, so confirm the match.
    return next((r for r in rows if r.get("name") == run_name), None)


def list_experiment_items(experiment_id: str, *,
                          fields: str = "scores") -> list:
    """An experiment's items, via `GET /api/public/experiment-items`.

    Replaces the `datasetRunItems` array that the deprecated run endpoint
    embedded. Each row carries `traceId` plus — with `fields=scores` — every
    score on that item, so scores no longer need a per-item trace fetch. Add
    `io` to `fields` for the item input/output/expected output.
    """
    return _paginate("/api/public/experiment-items",
                     {"experimentId": experiment_id,
                      "fromStartTime": _experiment_window(),
                      "limit": 100, "fields": fields})


def delete_experiment_traces(dataset_name: str, run_name: str) -> int:
    """Delete the traces behind one experiment. Returns the number deleted.

    Stand-in for the deprecated `DELETE /api/public/datasets/{name}/runs/{run}`,
    which re-runs called so that a pinned `run_name` produced a clean snapshot
    instead of appending to the previous run. v4 has no experiment-delete API,
    so this deletes the underlying traces via `DELETE /api/public/traces` —
    **and with them their observations and scores**, which the deprecated
    endpoint left alone. Confirm that wider deletion is what you want.

    The re-run guarantee does survive, verified against Cloud: because an
    experiment is derived from its traces, deleting them drops the experiment
    from `GET /api/public/experiments` altogether, and a re-run under the same
    name comes back with only its own items.
    """
    experiment = find_experiment(dataset_name, run_name)
    if experiment is None:
        return 0
    trace_ids = sorted({item["traceId"] for item in
                        list_experiment_items(experiment["id"], fields="core")
                        if item.get("traceId")})
    deleted = 0
    for start in range(0, len(trace_ids), 1000):  # API caps a batch at 1,000
        chunk = trace_ids[start:start + 1000]
        status, data = langfuse_api("DELETE", "/api/public/traces",
                                    body={"traceIds": chunk})
        if status not in (200, 202, 204):
            raise RuntimeError(f"DELETE /api/public/traces -> {status}: "
                               f"{data.get('error')}")
        deleted += len(chunk)
    return deleted


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
        # State the redaction posture out loud. Silent masking is how a demo
        # ends up claiming a guarantee nobody watched it switch on — and silent
        # *un*masking is worse.
        if masking.enabled():
            print("✓ PII redaction ON — emails, phones, IBANs, national ids and "
                  "card numbers are scrubbed before export (LANGFUSE_MASK_PII=false "
                  "to disable)")
        else:
            print("! PII redaction OFF — raw payloads will be exported "
                  "(LANGFUSE_MASK_PII)")
    return EXPECTED_PROJECT

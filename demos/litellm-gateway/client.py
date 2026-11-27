#!/usr/bin/env python3
"""Send one OpenAI-compatible request through LiteLLM and verify its trace.

This is the second half of `run_demo.sh`, which is also the demo's readiness
check: one PASS / FAIL / WARN line per check, then READY or NOT READY. Exit 0
when no required check failed, else 1. `--links` also prints the link each act
opens. Standard library only.

The trace check reads Observations API v2 on a v4 server (Langfuse Cloud). The
repo's self-hosted server is v3, which has no Observations v2, so there it reads
the v3 trace list — the only session-filtered read a v3 server offers.
"""

import argparse
import base64
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_PROMPT = "Explain in one sentence why observability belongs at an AI gateway."


class DemoError(RuntimeError):
    """A user-actionable demo failure."""


def build_payload(prompt: str, request_id: str, session_id: str) -> Dict[str, Any]:
    """Build a request that LiteLLM can enrich and export to Langfuse."""
    return {
        "model": "demo-model",
        "messages": [
            {
                "role": "system",
                "content": "Be concise and answer in one sentence.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 120,
        "user": "gateway-demo-user",
        "metadata": {
            "generation_name": "litellm-gateway-completion",
            "trace_name": "litellm-gateway-demo",
            # LiteLLM owns the OpenTelemetry trace ID. Keep a separate request ID
            # for correlation and use this unique session ID for API verification.
            "request_id": request_id,
            "session_id": session_id,
            # LiteLLM's langfuse_otel callback maps "trace_user_id" (not
            # "user_id") to the Langfuse trace user; the wrong key is silently
            # dropped and never reaches the trace.
            "trace_user_id": "gateway-demo-user",
            # path:proxy pairs with sdk_client.py's path:sdk so both instrumentation
            # models can be filtered apart in one Langfuse trace list.
            "tags": ["litellm", "gateway", "gateway:litellm", "path:proxy", "demo"],
        },
    }


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30,
) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DemoError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise DemoError(f"Could not reach {url}: {exc.reason}") from exc
    # A read timeout raises socket.timeout, which is NOT a URLError subclass, so
    # it would otherwise escape as an unhandled traceback and abort the run. Wrap
    # it as a DemoError so wait_for_trace's retry loop treats one slow poll as
    # retryable — Langfuse queries can stall briefly after a ClickHouse restart.
    except (TimeoutError, socket.timeout) as exc:
        raise DemoError(f"{method} {url} timed out after {timeout:g}s") from exc
    except json.JSONDecodeError as exc:
        raise DemoError(f"{method} {url} returned invalid JSON") from exc


def call_gateway(
    gateway_url: str,
    gateway_key: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return request_json(
        f"{gateway_url.rstrip('/')}/v1/chat/completions",
        method="POST",
        body=payload,
        headers={"Authorization": f"Bearer {gateway_key}"},
        timeout=90,
    )


def basic_auth(public_key: str, secret_key: str) -> str:
    return base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")


def detect_api_major(langfuse_url: str, auth: str, timeout: float = 5) -> int:
    """Major version of the target Langfuse server, from `GET /api/public/health`.

    The verification read below has no endpoint that works on both majors: a v3
    server has no Observations v2 API, and Langfuse Cloud removes the v1 trace
    endpoints on 2026-11-16. This demo is documented against Cloud but defaults
    to the repo's self-hosted stack, so it has to serve both and pick per server
    — the same gate `demos/grocery-assistant` uses.

    An unreadable or unparseable version is treated as v4: the endpoint with a
    future is the better guess, and the v4 read fails loudly rather than
    silently returning nothing.
    """
    try:
        health = request_json(
            f"{langfuse_url.rstrip('/')}/api/public/health",
            headers={"Authorization": f"Basic {auth}"},
            timeout=timeout,
        )
        return int(str(health.get("version", "")).split(".")[0])
    except (DemoError, ValueError, TypeError):
        return 4


def session_read_url(langfuse_url: str, session_id: str, api_major: int) -> str:
    """Where to look for the session's trace, for this server major.

    v4 answers "what happened in this session" with observation ROWS rather than
    a trace object — v4 has no trace entity — so this asks for `trace_context`
    to get `traceId`/`traceName` on each row and normalises them in
    `wait_for_trace`.

    The `sessionId` filter uses **stringOptions / "any of"** (value = a list).
    The docs show `{"type": "string", "operator": "="}`, and as of Cloud 4.43
    that works too — but it returned a 200 with ZERO rows on 4.27, which reads
    exactly like "the trace never arrived". "any of" is verified on both, and a
    silent empty result is the one failure this demo must not have.
    """
    base = langfuse_url.rstrip("/")
    if api_major <= 3:
        return f"{base}/api/public/traces?sessionId={quote(session_id, safe='')}&limit=10"
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    query = urlencode({
        "filter": json.dumps([{"type": "stringOptions", "column": "sessionId",
                               "operator": "any of", "value": [session_id]}]),
        "fields": "core,basic,trace_context",
        "fromStartTime": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 100,
    })
    return f"{base}/api/public/v2/observations?{query}"


def pick_trace(rows: list, session_id: str, api_major: int) -> Optional[Dict[str, Any]]:
    """The session's trace as `{"id", "name", "observations"}`, or None if it has not landed.

    v3 rows are already traces, and a trace row can exist before its observations
    land, so `observations` counts the ids the row lists rather than trusting the
    row itself. v4 rows are observations, several per trace, so this collapses
    them: prefer the row flagged `isRootObservation` (whose name is the trace
    name), but fall back to any row rather than requiring the flag — the
    gateway's OTLP export owns this trace's shape, and demanding a flag it may not
    set would turn a delivered trace into a verification failure.
    """
    if api_major <= 3:
        row = next((r for r in rows if r.get("sessionId") == session_id), None)
        if row is None:
            return None
        return {"id": row.get("id"), "name": row.get("name"),
                "observations": len(row.get("observations") or [])}
    matching = [r for r in rows if r.get("sessionId") == session_id]
    if not matching:
        return None
    row = next((r for r in matching if r.get("isRootObservation")), matching[-1])
    return {"id": row.get("traceId"), "name": row.get("traceName") or row.get("name"),
            "observations": len(matching)}


def wait_for_trace(
    langfuse_url: str,
    public_key: str,
    secret_key: str,
    session_id: str,
    timeout: float,
    api_major: int = 4,
) -> Dict[str, Any]:
    auth = basic_auth(public_key, secret_key)
    read_url = session_read_url(langfuse_url, session_id, api_major)
    deadline = time.monotonic() + timeout
    last_error = "trace has not arrived yet"

    while time.monotonic() < deadline:
        try:
            result = request_json(
                read_url,
                headers={"Authorization": f"Basic {auth}"},
                timeout=min(5, max(1, timeout)),
            )
            trace = pick_trace(result.get("data", []), session_id, api_major)
            # An HTTP 200 with the trace row is not proof: the row can land before
            # its observations do, so keep polling until there is at least one.
            if trace and trace["observations"] > 0:
                return trace
            last_error = (
                "trace exists but has no observations yet" if trace
                else "session is not indexed yet"
            )
        except DemoError as exc:
            last_error = str(exc)
            if "HTTP 401" in last_error or "HTTP 403" in last_error:
                raise
        # Back off between polls on BOTH paths — a 200 with the trace not yet
        # indexed (the common case, ingestion is async) must not spin in a tight
        # loop hammering the API for the whole timeout window.
        time.sleep(1)

    raise DemoError(
        f"Langfuse did not return the {session_id} session within {timeout:g}s. "
        f"Last check: {last_error}"
    )


# Project the stack-wide keys belong to. Keep in sync with `langfuse.project` in
# demo.yaml and LANGFUSE_INIT_PROJECT_NAME in the root docker-compose.yaml.
STACK_PROJECT = "LLM Observability Demo"


class Readiness:
    """PASS / FAIL / WARN lines and the closing READY or NOT READY verdict.

    `run_demo.sh` prints its own preflight lines first and passes how many passed
    in `GATEWAY_PREFLIGHT_PASSED`, so the closing count covers the whole run.
    A failed optional check is a WARN and does not make the demo NOT READY.
    """

    def __init__(self, prior_passed: int = 0) -> None:
        self.results = [("PASS", True, "preflight")] * max(0, prior_passed)

    def check(self, ok: bool, what: str, *, required: bool = True, hint: str = "") -> bool:
        status = "PASS" if ok else ("FAIL" if required else "WARN")
        self.results.append((status, required, what))
        print(f"  {status}  {what}" + (f"  ->  {hint}" if hint and not ok else ""))
        return ok

    def summary(self) -> Tuple[str, int]:
        passed = sum(1 for s, _, _ in self.results if s == "PASS")
        failed = sum(1 for s, _, _ in self.results if s == "FAIL")
        warned = sum(1 for s, _, _ in self.results if s == "WARN")
        detail = f"{passed}/{len(self.results)} checks passed"
        if failed:
            detail += f", {failed} required failed"
        if warned:
            detail += f", {warned} warning{'s' if warned != 1 else ''}"
        return (f"NOT READY ({detail})", 1) if failed else (f"READY ({detail})", 0)


def prior_passed(environ: Dict[str, str]) -> int:
    try:
        return max(0, int(environ.get("GATEWAY_PREFLIGHT_PASSED", "0") or 0))
    except ValueError:
        return 0


def expected_project(environ: Dict[str, str]) -> Optional[str]:
    """The project name the keys must map to, or None when any project is fine.

    `DEMO_LANGFUSE_PROJECT` pins it explicitly. Otherwise only the self-hosted
    stack-wide keys have a known project: dedicated `LITELLM_LANGFUSE_*` keys and
    cloud mode point at a project the presenter chose, so the check only reports it.
    """
    if environ.get("DEMO_LANGFUSE_PROJECT"):
        return environ["DEMO_LANGFUSE_PROJECT"]
    if environ.get("LITELLM_LANGFUSE_PUBLIC_KEY") or environ.get("DEPLOY_MODE") == "cloud":
        return None
    return STACK_PROJECT


def project_of(langfuse_url: str, auth: str) -> Dict[str, Any]:
    """The project these keys belong to (`GET /api/public/projects`, on v3 and v4)."""
    result = request_json(
        f"{langfuse_url.rstrip('/')}/api/public/projects",
        headers={"Authorization": f"Basic {auth}"},
        timeout=10,
    )
    data = result.get("data") or []
    return data[0] if data else {}


def act_links(
    langfuse_url: str, project_id: str, trace_id: Optional[str], session_id: str, gateway_url: str
) -> List[Tuple[str, str]]:
    """One (label, url) per act of DEMO_SCRIPT.md, in act order."""
    p = f"{langfuse_url.rstrip('/')}/project/{project_id}"
    links = []
    if trace_id:
        links.append(("Act 1 · this run's trace", f"{p}/traces/{trace_id}"))
    links.append(("Act 1 · this run's session", f"{p}/sessions/{quote(session_id, safe='')}"))
    links.append(("Act 2 · LiteLLM admin UI", f"{gateway_url.rstrip('/')}/ui/"))
    return links


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--session-id",
        default=f"litellm-gateway-{uuid.uuid4().hex[:8]}",
        help="Langfuse session id (generated by default)",
    )
    parser.add_argument(
        "--trace-timeout",
        type=float,
        default=30,
        help="Seconds to wait for asynchronous Langfuse ingestion",
    )
    parser.add_argument(
        "--skip-trace-check",
        action="store_true",
        help="Return after the gateway response without polling Langfuse",
    )
    parser.add_argument(
        "--links",
        action="store_true",
        help="Also print the link each act of DEMO_SCRIPT.md opens",
    )
    return parser.parse_args(argv)


def run_checks(args: argparse.Namespace, ready: Readiness, environ: Dict[str, str]) -> None:
    """The gateway call, then the Langfuse read-back. Each step records its own check."""
    gateway_url = environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    gateway_key = environ.get("LITELLM_MASTER_KEY", "sk-litellm-demo")
    request_id = uuid.uuid4().hex
    payload = build_payload(args.prompt, request_id, args.session_id)

    print("Gateway call")
    try:
        response = call_gateway(gateway_url, gateway_key, payload)
        answer = response["choices"][0]["message"]["content"]
    except DemoError as exc:
        ready.check(False, f"gateway call: {exc}",
                    hint="docker compose --profile demo logs --tail 50 litellm-proxy")
        return
    except (KeyError, IndexError, TypeError):
        ready.check(False, f"gateway returned an unexpected response: {response}")
        return
    ready.check(True, f"gateway answered on {gateway_url} (model alias {payload['model']})")
    print(f"Model response: {answer}")
    print(f"Request ID:    {request_id}")
    print(f"Session ID:    {args.session_id}")

    print("Langfuse")
    if args.skip_trace_check:
        ready.check(False, "trace check skipped (--skip-trace-check)", required=False)
        return

    public_key = environ.get("LITELLM_LANGFUSE_PUBLIC_KEY") or environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = environ.get("LITELLM_LANGFUSE_SECRET_KEY") or environ.get("LANGFUSE_SECRET_KEY")
    langfuse_url = environ.get(
        "LITELLM_LANGFUSE_BASE_URL",
        environ.get("LANGFUSE_BASE_URL", environ.get("LANGFUSE_HOST", "http://localhost:3001")),
    )
    if not ready.check(bool(public_key and secret_key),
                       "Langfuse keys set (LITELLM_LANGFUSE_* or LANGFUSE_*)",
                       hint="run ./setup.sh, or set them in the root .env"):
        return
    auth = basic_auth(public_key, secret_key)

    want = expected_project(environ)
    try:
        project = project_of(langfuse_url, auth)
    except DemoError as exc:
        ready.check(False, f"keys authenticate against {langfuse_url}: {exc}",
                    hint="401: wrong key pair, or the base URL points at another host or region")
        return
    name = project.get("name")
    if not ready.check(want is None or name == want,
                       f"keys map to project '{name}'" + (f" (want '{want}')" if want else ""),
                       hint="use that project's keys in the root .env, or set DEMO_LANGFUSE_PROJECT"):
        return

    api_major = detect_api_major(langfuse_url, auth)
    endpoint = "/api/public/traces" if api_major <= 3 else "/api/public/v2/observations"
    try:
        trace = wait_for_trace(
            langfuse_url, public_key, secret_key, args.session_id, args.trace_timeout, api_major
        )
    except DemoError as exc:
        ready.check(False, f"trace for session {args.session_id}: {exc}",
                    hint="the proxy may export to another project or host; "
                         "docker compose --profile demo logs --tail 50 litellm-proxy")
        return
    ready.check(True, (
        f"session {args.session_id} landed as trace '{trace.get('name') or 'unnamed'}' "
        f"with {trace['observations']} observation(s) (server v{api_major}, {endpoint})"
    ))
    print(f"Trace ID:      {trace.get('id')}")
    print(f"Langfuse UI:   {langfuse_url.rstrip('/')} (filter by session {args.session_id})")

    if args.links and project.get("id"):
        print("\nLinks")
        for label, url in act_links(
            langfuse_url, project["id"], trace.get("id"), args.session_id, gateway_url
        ):
            print(f"  {label:<28} {url}")


def main(argv: Optional[List[str]] = None, environ: Optional[Dict[str, str]] = None) -> int:
    args = parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    ready = Readiness(prior_passed(env))
    run_checks(args, ready, env)
    line, code = ready.summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())

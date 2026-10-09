#!/usr/bin/env python3
"""Send one OpenAI-compatible request through LiteLLM and verify its trace."""

import argparse
import base64
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
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
    """The session's trace as `{"id", "name"}`, or None if it has not landed.

    v3 rows are already traces. v4 rows are observations, several per trace, so
    this collapses them: prefer the row flagged `isRootObservation` (whose name
    is the trace name), but fall back to any row rather than requiring the flag
    — the gateway's OTLP export owns this trace's shape, and demanding a flag it
    may not set would turn a delivered trace into a verification failure.
    """
    if api_major <= 3:
        return next((r for r in rows if r.get("sessionId") == session_id), None)
    matching = [r for r in rows if r.get("sessionId") == session_id]
    if not matching:
        return None
    row = next((r for r in matching if r.get("isRootObservation")), matching[-1])
    return {"id": row.get("traceId"), "name": row.get("traceName") or row.get("name")}


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
            if trace:
                return trace
            last_error = "session is not indexed yet"
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


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    gateway_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
    gateway_key = os.getenv("LITELLM_MASTER_KEY", "sk-litellm-demo")
    request_id = uuid.uuid4().hex
    payload = build_payload(args.prompt, request_id, args.session_id)

    response = call_gateway(gateway_url, gateway_key, payload)
    try:
        answer = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise DemoError(f"Gateway returned an unexpected response: {response}") from exc

    print(f"Model response: {answer}")
    print(f"Request ID:    {request_id}")
    print(f"Session ID:    {args.session_id}")

    if args.skip_trace_check:
        print("Trace check:   skipped")
        return 0

    public_key = os.getenv("LITELLM_LANGFUSE_PUBLIC_KEY") or os.getenv(
        "LANGFUSE_PUBLIC_KEY"
    )
    secret_key = os.getenv("LITELLM_LANGFUSE_SECRET_KEY") or os.getenv(
        "LANGFUSE_SECRET_KEY"
    )
    langfuse_url = os.getenv(
        "LITELLM_LANGFUSE_BASE_URL",
        os.getenv("LANGFUSE_BASE_URL", os.getenv("LANGFUSE_HOST", "http://localhost:3001")),
    )
    if not public_key or not secret_key:
        raise DemoError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required for trace verification"
        )

    api_major = detect_api_major(langfuse_url, basic_auth(public_key, secret_key))
    trace = wait_for_trace(
        langfuse_url,
        public_key,
        secret_key,
        args.session_id,
        args.trace_timeout,
        api_major,
    )
    trace_name = trace.get("name") or "unnamed trace"
    print(f"Trace check:   captured by Langfuse ({trace_name})")
    print(f"Trace ID:      {trace.get('id')}")
    print(f"Langfuse UI:   {langfuse_url.rstrip('/')} (filter by session {args.session_id})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DemoError as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        sys.exit(1)

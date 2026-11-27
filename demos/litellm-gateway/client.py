#!/usr/bin/env python3
"""Send one OpenAI-compatible request through LiteLLM and verify its trace."""

import argparse
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# The Langfuse reads live in scripts/verify_demo.py, the read-only readiness
# check, so the post-run check and the readiness check cannot drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import verify_demo as reads  # noqa: E402


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
        "user": reads.DEMO_USER,
        "metadata": {
            "generation_name": "litellm-gateway-completion",
            "trace_name": reads.TRACE_NAME,
            # LiteLLM owns the OpenTelemetry trace ID. Keep a separate request ID
            # for correlation and use this unique session ID for API verification.
            "request_id": request_id,
            "session_id": session_id,
            # LiteLLM's langfuse_otel callback maps "trace_user_id" (not
            # "user_id") to the Langfuse trace user; the wrong key is silently
            # dropped and never reaches the trace.
            "trace_user_id": reads.DEMO_USER,
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


def session_filter(session_id: str) -> Dict[str, str]:
    """Observations v2 filter for one session.

    The `sessionId` filter uses **stringOptions / "any of"** (value = a list).
    The docs show `{"type": "string", "operator": "="}`, and as of Cloud 4.43
    that works too — but it returned a 200 with ZERO rows on 4.27, which reads
    exactly like "the trace never arrived". "any of" is verified on both, and a
    silent empty result is the one failure this demo must not have.
    """
    return {"filter": json.dumps([{"type": "stringOptions", "column": "sessionId",
                                   "operator": "any of", "value": [session_id]}])}


def find_session_trace(
    lf: "reads.Langfuse", session_id: str, since: datetime, until: datetime
) -> Optional[Dict[str, Any]]:
    """This run's trace as `{"id", "name", "observations", "api"}`, or None if not landed.

    Reads Observations v2 (the same reads as `scripts/verify_demo.py`). On the
    pinned self-hosted v3 server, where v2 answers 404, it falls back to the v1
    observations list, which has no session field: there it takes the newest
    trace of this demo's user that started after the request was sent.
    `observations` counts the trace's rows, because a trace can be visible
    before its observations land.
    """
    api, rows = reads.observation_rows(
        lf, since, until, session_filter(session_id), {"userId": reads.DEMO_USER}
    )
    if api == "v2":
        rows = [r for r in rows if r.get("sessionId") == session_id]
    trace = reads.latest_trace(rows)
    if trace is None:
        return None
    obs = reads.trace_observations(lf, api, trace["id"], since, until)
    return {"id": trace["id"], "name": trace["name"], "api": api,
            "observations": len({o.get("id") for o in obs if o.get("id")})}


def wait_for_trace(
    langfuse_url: str,
    public_key: str,
    secret_key: str,
    session_id: str,
    timeout: float,
    sent_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    lf = reads.Langfuse(langfuse_url, public_key, secret_key, timeout=min(5, max(1, timeout)))
    since = (sent_at or datetime.now(timezone.utc)) - timedelta(seconds=30)
    deadline = time.monotonic() + timeout
    last_error = "trace has not arrived yet"

    while time.monotonic() < deadline:
        try:
            trace = find_session_trace(
                lf, session_id, since, datetime.now(timezone.utc) + timedelta(minutes=5)
            )
            if trace and trace["observations"] > 0:
                return trace
            last_error = (
                "trace exists but has no observations yet" if trace
                else "session is not indexed yet"
            )
        except reads.ApiError as exc:
            last_error = str(exc)
            if exc.status in (401, 403):
                raise DemoError(f"Langfuse rejected the keys: {exc}") from exc
        # Back off between polls — a 200 with the trace not yet indexed (the
        # common case, ingestion is async) must not spin in a tight loop
        # hammering the API for the whole timeout window.
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

    sent_at = datetime.now(timezone.utc)
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

    trace = wait_for_trace(
        langfuse_url,
        public_key,
        secret_key,
        args.session_id,
        args.trace_timeout,
        sent_at,
    )
    trace_name = trace.get("name") or "unnamed trace"
    print(f"Trace check:   captured by Langfuse ({trace_name}, "
          f"{trace['observations']} observation(s), {trace['api']} observations API)")
    print(f"Trace ID:      {trace.get('id')}")
    print(f"Langfuse UI:   {langfuse_url.rstrip('/')} (filter by session {args.session_id})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DemoError as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        sys.exit(1)

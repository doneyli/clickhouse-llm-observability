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
from typing import Any, Dict, Optional
from urllib.parse import quote
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


def wait_for_trace(
    langfuse_url: str,
    public_key: str,
    secret_key: str,
    session_id: str,
    timeout: float,
) -> Dict[str, Any]:
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    trace_url = (
        f"{langfuse_url.rstrip('/')}/api/public/traces?"
        f"sessionId={quote(session_id, safe='')}&limit=10"
    )
    deadline = time.monotonic() + timeout
    last_error = "trace has not arrived yet"

    while time.monotonic() < deadline:
        try:
            result = request_json(
                trace_url,
                headers={"Authorization": f"Basic {auth}"},
                timeout=min(5, max(1, timeout)),
            )
            for trace in result.get("data", []):
                if trace.get("sessionId") == session_id:
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

    trace = wait_for_trace(
        langfuse_url,
        public_key,
        secret_key,
        args.session_id,
        args.trace_timeout,
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

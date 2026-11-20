"""
Settings, the Langfuse client, and W3C trace propagation for every process in this demo.

Each process (the orchestrator and each A2A agent service) calls `get_langfuse(<service>)`
once at startup. All of them export to the SAME Langfuse project: one trace can only span
processes that write to one project.

Settings come from the demo's own `.env`. `LANGFUSE_*` is never read from the shell: a key
exported for another project would send traces there while queries read this one, and a
404 would look like a bug. (CI is the exception, where secrets arrive as env vars.)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

# a2a-sdk's own CLIENT/SERVER spans stay off (SPEC §6). Langfuse's default export filter
# drops their scope, and if one is the current span when we inject headers, the remote
# agent's parent is a span that never reaches Langfuse. Must be set before `import a2a`.
os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

DEMO_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = DEMO_DIR / "data"
_FILE = {k: v for k, v in dotenv_values(DEMO_DIR / ".env").items() if v is not None}
_TRUST_SHELL_KEYS = os.environ.get("CI") == "true" or os.environ.get("HANDOFF_TRUST_SHELL_KEYS") == "1"


def setting(name: str, default: Optional[str] = None) -> Optional[str]:
    """Process env first, then the demo `.env`; LANGFUSE_* only from the `.env` (or CI)."""
    if name.startswith("LANGFUSE_") and not _TRUST_SHELL_KEYS:
        return _FILE.get(name, default)
    return os.environ.get(name, _FILE.get(name, default))


LANGFUSE_BASE_URL = setting("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
ENVIRONMENT = setting("HANDOFF_ENVIRONMENT", "production")
RELEASE = setting("HANDOFF_RELEASE", "1.0.0")
CAPTURE_DIR = setting("HANDOFF_CAPTURE_DIR")  # set → spans go to JSONL files, not Langfuse

HOST = setting("HANDOFF_HOST", "127.0.0.1")
PORTS = {"billing": int(setting("BILLING_PORT", "9310")),
         "tech-support": int(setting("TECH_SUPPORT_PORT", "9311"))}


def agent_url(agent: str) -> str:
    return setting(f"{agent.upper().replace('-', '_')}_URL", f"http://{HOST}:{PORTS[agent]}")


_langfuse = None
SERVICE: Optional[str] = None


def get_langfuse(service: str):
    """The one Langfuse client for this process, created on first call."""
    global _langfuse, SERVICE
    if _langfuse is not None:
        return _langfuse
    from langfuse import Langfuse
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    trace.set_tracer_provider(provider)
    kwargs: dict = {}
    if CAPTURE_DIR:
        from handoff.capture import JsonlSpanExporter

        kwargs["span_exporter"] = JsonlSpanExporter(Path(CAPTURE_DIR) / f"{service}.jsonl", service)
        public_key, secret_key, base_url = "pk-lf-capture", "sk-lf-capture", "http://127.0.0.1:9"
    else:
        public_key, secret_key = setting("LANGFUSE_PUBLIC_KEY"), setting("LANGFUSE_SECRET_KEY")
        base_url = LANGFUSE_BASE_URL
        if not (public_key and secret_key):
            raise RuntimeError(f"LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY missing from {DEMO_DIR / '.env'}")
    _langfuse = Langfuse(public_key=public_key, secret_key=secret_key, base_url=base_url,
                         environment=ENVIRONMENT, release=RELEASE, tracer_provider=provider, **kwargs)
    SERVICE = service
    return _langfuse


def flush() -> None:
    if _langfuse is not None:
        _langfuse.flush()


# --- W3C propagation ---------------------------------------------------------------------

CARRIER_KEYS = ("traceparent", "tracestate", "baggage")


def _propagator(baggage: bool):
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    if not baggage:
        return TraceContextTextMapPropagator()
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.composite import CompositePropagator

    return CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def inject_trace_context(*, baggage: bool = True) -> dict:
    """Headers for ONE outbound A2A call, from the current OTel context."""
    carrier: dict = {}
    _propagator(baggage).inject(carrier)
    return carrier


def extract_trace_context(headers: dict):
    """OTel context from inbound headers (case-insensitive), or None if none arrived."""
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    carrier = {k: lowered[k] for k in CARRIER_KEYS if k in lowered}
    if not carrier:
        return None, ()
    return _propagator(True).extract(carrier), tuple(sorted(carrier))


def current_environment() -> str:
    """The effective environment of the current span (e.g. sdk-experiment inside experiments)."""
    from langfuse._client.attributes import LangfuseOtelSpanAttributes
    from opentelemetry import trace

    attrs = getattr(trace.get_current_span(), "attributes", None) or {}
    return str(attrs.get(LangfuseOtelSpanAttributes.ENVIRONMENT) or ENVIRONMENT)

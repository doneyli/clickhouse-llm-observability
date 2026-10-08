"""
Runtime configuration and the single OpenTelemetry pipeline (OBS-01, OBS-06, ENT-03).

One TracerProvider, two exporters:

    ┌────────────── app process ──────────────┐
    │  LangGraph / LangChain / MCP / our spans │
    │                 │                        │
    │         TracerProvider (W3C ids)         │
    │        ┌────────┴─────────┐              │
    │  LangfuseSpanProcessor   BatchSpanProcessor
    │  (AI spans, PII-masked)  (ALL spans, payloads stripped)
    └────────┼──────────────────┼──────────────┘
             ▼                  ▼
         Langfuse         APM (Dynatrace / Jaeger)

Because both exporters hang off the same provider, a trace has ONE trace id in
both tools — the correlation key an SRE pastes from a Dynatrace incident into
Langfuse, or vice versa. The APM copy carries timings, status and structure but
no prompts or completions, so customer text never reaches the APM.

Configuration is read from this demo's .env only. Shell-exported LANGFUSE_*
variables are ignored on purpose: a key for another project silently sends
traces elsewhere while your queries read this one.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from dotenv import dotenv_values

DEMO_DIR = Path(__file__).resolve().parent.parent
_ENV = {k: v for k, v in dotenv_values(DEMO_DIR / ".env").items() if v is not None}

# Target profile. `cloud` (default when .env.cloud exists) overlays .env.cloud —
# Langfuse Cloud keys — on top of .env; `selfhosted` uses the local EE stack.
PROFILE = (os.environ.get("NORTHWIND_PROFILE") or _ENV.get("NORTHWIND_PROFILE")
           or ("cloud" if (DEMO_DIR / ".env.cloud").exists() else "selfhosted"))
if PROFILE != "selfhosted":
    _overlay = DEMO_DIR / f".env.{PROFILE}"
    if not _overlay.exists():
        raise SystemExit(f"NORTHWIND_PROFILE={PROFILE} but {_overlay.name} is missing")
    _ENV.update({k: v for k, v in dotenv_values(_overlay).items() if v})
    if "LANGFUSE_BASE_URL" not in _ENV and _ENV.get("LANGFUSE_HOST"):
        _ENV["LANGFUSE_BASE_URL"] = _ENV["LANGFUSE_HOST"]


def _main_checkout_env() -> dict:
    """Model API keys may live in the repo-root .env of the main checkout."""
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=DEMO_DIR,
                                capture_output=True, text=True, check=True).stdout.strip()
        root = (DEMO_DIR / common).resolve().parent
        return {k: v for k, v in dotenv_values(root / ".env").items() if v}
    except Exception:  # noqa: BLE001
        return {}


_ROOT_ENV = _main_checkout_env()


def env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Demo .env first, then the process env for non-Langfuse keys."""
    if _ENV.get(key):
        return _ENV[key]
    if not key.startswith("LANGFUSE_") and os.environ.get(key):
        return os.environ[key]
    return default


# Pin the Langfuse settings into the process env so every SDK entry point
# (including the LangChain CallbackHandler) reads THIS project's keys.
for _k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL",
           "LANGFUSE_TRACING_ENVIRONMENT", "LANGFUSE_SAMPLE_RATE", "LANGFUSE_MASK_PII"):
    if _ENV.get(_k):
        os.environ[_k] = _ENV[_k]
for _k in ("LANGFUSE_HOST",):
    os.environ.pop(_k, None)

LANGFUSE_BASE_URL = env("LANGFUSE_BASE_URL", "http://localhost:3100").rstrip("/")
LANGFUSE_PUBLIC_KEY = env("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = env("LANGFUSE_SECRET_KEY")
PROJECT_ID = env("LANGFUSE_PROJECT_ID", "retail-assistant")
# NORTHWIND_ENVIRONMENT / NORTHWIND_SAMPLE_RATE (process env) override the .env
# defaults for one run — e.g. a staging batch, or a 25%-sampled batch.
ENVIRONMENT = os.environ.get("NORTHWIND_ENVIRONMENT") or env("LANGFUSE_TRACING_ENVIRONMENT", "production")
RELEASE = env("NORTHWIND_RELEASE", "assistant-1.4.0")
SAMPLE_RATE = float(os.environ.get("NORTHWIND_SAMPLE_RATE") or env("LANGFUSE_SAMPLE_RATE", "1.0"))
os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = ENVIRONMENT
os.environ["LANGFUSE_SAMPLE_RATE"] = str(SAMPLE_RATE)

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY") or _ROOT_ENV.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = (env("OPENAI_API_KEY") or env("OPEN_AI_API_KEY")
                  or _ROOT_ENV.get("OPENAI_API_KEY") or _ROOT_ENV.get("OPEN_AI_API_KEY"))
if ANTHROPIC_API_KEY:
    os.environ.setdefault("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
if OPENAI_API_KEY:
    os.environ.setdefault("OPENAI_API_KEY", OPENAI_API_KEY)

AGENT_MODEL = env("AGENT_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = env("JUDGE_MODEL", "claude-sonnet-4-6")
MCP_URL = os.environ.get("NORTHWIND_MCP_URL") or env("MCP_URL", "http://localhost:8765/mcp")

APM_OTLP_ENDPOINT = env("APM_OTLP_ENDPOINT", "http://localhost:14318/v1/traces")
APM_OTLP_HEADERS = env("APM_OTLP_HEADERS", "")
APM_UI_TRACE_URL = env("APM_UI_TRACE_URL", "http://localhost:16686/trace/{trace_id}")

# Attributes that carry prompts, completions, tool payloads. Stripped from the
# APM copy: the APM gets operational telemetry, Langfuse gets the AI payloads.
_PAYLOAD_PREFIXES = (
    "langfuse.observation.input", "langfuse.observation.output",
    "langfuse.trace.input", "langfuse.trace.output",
    "langfuse.observation.metadata", "langfuse.trace.metadata",
    "gen_ai.prompt", "gen_ai.completion", "gen_ai.input", "gen_ai.output",
    "input.value", "output.value", "llm.input_messages", "llm.output_messages",
)


def _payload_free_exporter(inner):
    """Wrap an exporter so spans lose their payload attributes on the way out."""
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter

    class PayloadStrippingExporter(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]):
            clean = []
            for s in spans:
                attrs = {k: v for k, v in (s.attributes or {}).items()
                         if not k.startswith(_PAYLOAD_PREFIXES)}
                attrs["langfuse.trace_url"] = trace_url(format(s.context.trace_id, "032x"))
                clean.append(ReadableSpan(
                    name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                    attributes=attrs, events=s.events, links=s.links, kind=s.kind,
                    status=s.status, start_time=s.start_time, end_time=s.end_time,
                    instrumentation_scope=s.instrumentation_scope))
            return inner.export(clean)

        def shutdown(self):
            return inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000):
            return inner.force_flush(timeout_millis)

    return PayloadStrippingExporter()


_provider = None
_langfuse = None


def tracer_provider(service_name: str = "northwind-assistant"):
    """The process-wide provider. APM processor attached first, Langfuse second."""
    global _provider
    if _provider is not None:
        return _provider
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _provider = TracerProvider(resource=Resource.create({
        "service.name": service_name,
        "service.version": RELEASE,
        "deployment.environment": ENVIRONMENT,
    }))
    if APM_OTLP_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        headers = dict(h.split("=", 1) for h in APM_OTLP_HEADERS.split(",") if "=" in h)
        _provider.add_span_processor(BatchSpanProcessor(
            _payload_free_exporter(OTLPSpanExporter(endpoint=APM_OTLP_ENDPOINT, headers=headers)),
            schedule_delay_millis=1000))
    trace.set_tracer_provider(_provider)
    return _provider


def get_langfuse(service_name: str = "northwind-assistant"):
    """Langfuse client bound to the shared provider, with masking + sampling."""
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    from langfuse import Langfuse

    from northwind import masking

    provider = tracer_provider(service_name)
    _langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY,
        secret_key=LANGFUSE_SECRET_KEY,
        base_url=LANGFUSE_BASE_URL,
        environment=ENVIRONMENT,
        release=RELEASE,
        sample_rate=SAMPLE_RATE,
        tracer_provider=provider,
        **({"mask_otel_spans": masking.mask_otel_spans} if masking.enabled() else {}),
    )
    return _langfuse


def flush() -> None:
    """Flush BOTH exporters. Langfuse's flush alone misses the APM processor."""
    if _langfuse is not None:
        _langfuse.flush()
    if _provider is not None:
        _provider.force_flush()


_project_id: Optional[str] = None


def project_id() -> str:
    """The project id behind these keys (a cuid on Cloud), resolved once."""
    global _project_id
    if _project_id is None:
        try:
            _project_id = api("GET", "/api/public/projects")["data"][0]["id"]
        except Exception:  # noqa: BLE001
            _project_id = PROJECT_ID
    return _project_id


def trace_url(trace_id: str) -> str:
    return f"{LANGFUSE_BASE_URL}/project/{project_id()}/traces/{trace_id}"


def apm_url(trace_id: str) -> str:
    return APM_UI_TRACE_URL.format(trace_id=trace_id)


def basic_auth() -> str:
    return "Basic " + base64.b64encode(
        f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()


def api(method: str, path: str, body=None, timeout: int = 30, params: Optional[dict] = None):
    """Minimal Langfuse public-API client (returns parsed JSON; raises on HTTP error)."""
    import httpx

    r = httpx.request(method, f"{LANGFUSE_BASE_URL}{path}", json=body, params=params,
                      headers={"Authorization": basic_auth()}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} → {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else None

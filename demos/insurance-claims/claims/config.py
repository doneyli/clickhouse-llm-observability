"""
Runtime configuration and the single OpenTelemetry pipeline, shared by every
process of the demo: the LangGraph claims app, the policy-admin MCP server and the
Strands doc-intake service (which imports this module from its own venv — keep it
free of LangGraph/LangChain imports).

One TracerProvider per process, two exporters:

    LangGraph / Strands / MCP / our spans
                    │
         TracerProvider (W3C ids)
        ┌───────────┴────────────┐
  LangfuseSpanProcessor     BatchSpanProcessor
  (AI spans, PII-masked)    (ALL spans, payloads stripped)
        ▼                        ▼
     Langfuse                APM (Jaeger locally = Dynatrace stand-in)

Both exporters hang off the same provider, so a trace has ONE trace id in both
tools. The APM copy carries timings, status and structure but no prompts,
completions or claim data.

Configuration comes from this demo's .env (+ .env.<profile>) only. Shell-exported
LANGFUSE_* variables are ignored on purpose: a key for another project silently
sends traces elsewhere while your queries read this one.
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from typing import Optional, Sequence

from dotenv import dotenv_values

DEMO_DIR = Path(__file__).resolve().parent.parent
_ENV = {k: v for k, v in dotenv_values(DEMO_DIR / ".env").items() if v is not None} \
    if (DEMO_DIR / ".env").exists() else {}

# Target profile: `cloud` (default when .env.cloud exists) or `selfhosted` (.env.selfhosted,
# the local Enterprise instance). The overlay holds that target's Langfuse URL and keys.
PROFILE = (os.environ.get("CLAIMS_PROFILE") or _ENV.get("CLAIMS_PROFILE")
           or ("cloud" if (DEMO_DIR / ".env.cloud").exists() else "selfhosted"))
_overlay = DEMO_DIR / f".env.{PROFILE}"
if _overlay.exists():
    _ENV.update({k: v for k, v in dotenv_values(_overlay).items() if v})


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
    else:
        os.environ.pop(_k, None)
os.environ.pop("LANGFUSE_HOST", None)

LANGFUSE_BASE_URL = (env("LANGFUSE_BASE_URL") or "http://localhost:3100").rstrip("/")
LANGFUSE_PUBLIC_KEY = env("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = env("LANGFUSE_SECRET_KEY")
PROJECT_ID = env("LANGFUSE_PROJECT_ID", "")
# CLAIMS_ENVIRONMENT / CLAIMS_SAMPLE_RATE (process env) override the .env defaults for one run.
ENVIRONMENT = os.environ.get("CLAIMS_ENVIRONMENT") or env("LANGFUSE_TRACING_ENVIRONMENT", "production")
# Release knob for the story arcs: 1.4.0 reproduces the retriever bug (F2 in SPEC.md).
RELEASE = os.environ.get("CLAIMS_RELEASE") or env("CLAIMS_RELEASE", "claims-1.5.0")
SAMPLE_RATE = float(os.environ.get("CLAIMS_SAMPLE_RATE") or env("LANGFUSE_SAMPLE_RATE", "1.0"))
os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = ENVIRONMENT
os.environ["LANGFUSE_SAMPLE_RATE"] = str(SAMPLE_RATE)

ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY") or _ROOT_ENV.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = (env("OPENAI_API_KEY") or env("OPEN_AI_API_KEY")
                  or _ROOT_ENV.get("OPENAI_API_KEY") or _ROOT_ENV.get("OPEN_AI_API_KEY"))
if ANTHROPIC_API_KEY:
    os.environ.setdefault("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY)
if OPENAI_API_KEY:
    os.environ.setdefault("OPENAI_API_KEY", OPENAI_API_KEY)

# Production runs the current model; experiments compare an upgrade before switching.
AGENT_MODEL = env("AGENT_MODEL", "claude-sonnet-5-5")
INTAKE_MODEL = env("INTAKE_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = env("JUDGE_MODEL", "claude-sonnet-4-6")

MCP_PORT = int(os.environ.get("CLAIMS_MCP_PORT") or env("CLAIMS_MCP_PORT", "8767"))
MCP_URL = os.environ.get("CLAIMS_MCP_URL") or env("CLAIMS_MCP_URL", f"http://localhost:{MCP_PORT}/mcp")
DOC_INTAKE_PORT = int(os.environ.get("DOC_INTAKE_PORT") or env("DOC_INTAKE_PORT", "9210"))
DOC_INTAKE_URL = os.environ.get("DOC_INTAKE_URL") or env("DOC_INTAKE_URL", f"http://localhost:{DOC_INTAKE_PORT}")
PORTAL_PORT = int(os.environ.get("CLAIMS_PORTAL_PORT") or env("CLAIMS_PORTAL_PORT", "8095"))

APM_OTLP_ENDPOINT = env("APM_OTLP_ENDPOINT", "http://localhost:14318/v1/traces")
APM_OTLP_HEADERS = env("APM_OTLP_HEADERS", "")
APM_UI_TRACE_URL = env("APM_UI_TRACE_URL", "http://localhost:16686/trace/{trace_id}")

# Attributes that carry prompts, completions, tool payloads. Stripped from the APM copy.
_PAYLOAD_PREFIXES = (
    "langfuse.observation.input", "langfuse.observation.output",
    "langfuse.trace.input", "langfuse.trace.output",
    "langfuse.observation.metadata", "langfuse.trace.metadata",
    "gen_ai.prompt", "gen_ai.completion", "gen_ai.input", "gen_ai.output",
    "gen_ai.system_instructions", "gen_ai.tool.call",
    "input.value", "output.value", "llm.input_messages", "llm.output_messages",
)


def _payload_free_exporter(inner):
    """Wrap an exporter so spans lose their payload attributes (and events) on the way out."""
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter

    class PayloadStrippingExporter(SpanExporter):
        def export(self, spans: Sequence[ReadableSpan]):
            clean = []
            for s in spans:
                attrs = {k: v for k, v in (s.attributes or {}).items()
                         if not k.startswith(_PAYLOAD_PREFIXES)}
                attrs["langfuse.trace_url"] = trace_url(format(s.context.trace_id, "032x"))
                # Strands puts prompts and completions in span EVENTS; drop those too.
                events = [e for e in (s.events or ()) if not e.name.startswith("gen_ai.")]
                clean.append(ReadableSpan(
                    name=s.name, context=s.context, parent=s.parent, resource=s.resource,
                    attributes=attrs, events=events, links=s.links, kind=s.kind,
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
SKIPPED_SCOPES = ("com.anthropic.sdk",)


def apm_enabled() -> bool:
    return bool(APM_OTLP_ENDPOINT) and APM_OTLP_ENDPOINT.lower() not in ("none", "off", "disabled")


def tracer_provider(service_name: str = "claims-orchestrator"):
    """The process-wide provider. APM processor first, Langfuse second (get_langfuse).

    Sampling lives on the provider: ParentBased, so a downstream service (MCP server,
    doc-intake) follows the caller's decision via the traceparent flags.
    """
    global _provider
    if _provider is not None:
        return _provider
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    sampler = ParentBased(TraceIdRatioBased(SAMPLE_RATE)) if SAMPLE_RATE < 1 else None
    _provider = TracerProvider(sampler=sampler, resource=Resource.create({
        "service.name": service_name,
        "service.version": RELEASE,
        "deployment.environment": ENVIRONMENT,
    }))
    if apm_enabled():
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        headers = dict(h.split("=", 1) for h in APM_OTLP_HEADERS.split(",") if "=" in h)
        _provider.add_span_processor(BatchSpanProcessor(
            _payload_free_exporter(OTLPSpanExporter(endpoint=APM_OTLP_ENDPOINT, headers=headers)),
            schedule_delay_millis=1000))
    trace.set_tracer_provider(_provider)
    return _provider


def get_langfuse(service_name: str = "claims-orchestrator"):
    """Langfuse client bound to the shared provider, with masking + sampling."""
    global _langfuse
    if _langfuse is not None:
        return _langfuse
    from langfuse import Langfuse

    from claims import masking

    from langfuse.span_filter import is_default_export_span

    provider = tracer_provider(service_name)
    _langfuse = Langfuse(
        # Recent Anthropic SDKs emit their own GenAI spans (scope com.anthropic.sdk.python). The LangChain
        # callback already records each call as a generation (with the prompt link), so exporting both would
        # count every LLM call — and its cost — twice. The APM copy keeps them.
        should_export_span=lambda span: is_default_export_span(span) and not (
            span.instrumentation_scope is not None
            and span.instrumentation_scope.name.startswith(SKIPPED_SCOPES)),
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


# Cross-process context = W3C `traceparent` AND W3C `baggage`. In Langfuse v4 every
# observation carries its own trace attributes (session, user, trace name, version,
# environment), so they must cross the process boundary with the span context. Baggage
# also carries the SDK's "this trace already has a root" claim; without it the receiving
# process marks its first span as a second root of the trace.
CARRIER_KEYS = ("traceparent", "tracestate", "baggage")


def _propagator():
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    return CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def inject_trace_context() -> dict:
    """Carrier for a call into one of OUR services (MCP `_meta`, A2A HTTP headers)."""
    carrier: dict = {}
    _propagator().inject(carrier)
    return carrier


def extract_trace_context(carrier: dict):
    """OTel context from a carrier written by inject_trace_context(). Keys are case-insensitive."""
    lowered = {k.lower(): v for k, v in carrier.items()}
    return _propagator().extract({k: lowered[k] for k in CARRIER_KEYS if k in lowered})


_project_id: Optional[str] = None


def project_id() -> str:
    """The project id behind these keys (a cuid on Cloud), resolved once."""
    global _project_id
    if _project_id is None:
        try:
            _project_id = api("GET", "/api/public/projects")["data"][0]["id"]
        except Exception:  # noqa: BLE001
            _project_id = PROJECT_ID or "unknown"
    return _project_id


def trace_url(trace_id: str) -> str:
    return f"{LANGFUSE_BASE_URL}/project/{project_id()}/traces/{trace_id}"


def session_url(session_id: str) -> str:
    return f"{LANGFUSE_BASE_URL}/project/{project_id()}/sessions/{session_id}"


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

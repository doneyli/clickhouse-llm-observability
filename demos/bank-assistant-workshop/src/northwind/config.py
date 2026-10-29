"""Workshop-only configuration. No parent checkout keys, APM, or target profiles."""
from contextlib import contextmanager
from pathlib import Path
import os
import re
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = Path(os.getenv("WORKSHOP_ENV_FILE", str(ROOT / ".env"))).resolve()
if ENV_FILE.exists():
    values = dotenv_values(ENV_FILE)
    # The workshop file owns its configuration. Missing Langfuse values cannot
    # inherit another project's shell credentials.
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST"):
        os.environ.pop(key, None)
    for key, value in values.items():
        if value is not None:
            os.environ[key] = value

WORKSHOP_ID = os.getenv("WORKSHOP_ID", "bank-workshop")
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", WORKSHOP_ID):
    raise SystemExit("WORKSHOP_ID must be 1-40 lowercase letters, digits or hyphens.")
LANGFUSE_BASE_URL = os.getenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com").rstrip("/")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
PROJECT_ID = os.getenv("LANGFUSE_PROJECT_ID", "")
RELEASE = "bank-workshop-1.0"
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4.1-mini")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", AGENT_MODEL)
MCP_PORT = int(os.getenv("NORTHWIND_MCP_PORT", "8765"))
MCP_URL = f"http://127.0.0.1:{MCP_PORT}/mcp"
PORT = int(os.getenv("WORKSHOP_PORT", "8090"))
_langfuse = None


class UntracedObservation:
    id = None
    trace_id = None
    def update(self, **kwargs):
        pass


class UntracedClient:
    @contextmanager
    def start_as_current_observation(self, **kwargs):
        yield UntracedObservation()
    def get_prompt(self, *args, **kwargs):
        return LocalPrompt(kwargs["fallback"])
    def create_score(self, **kwargs):
        pass
    def flush(self):
        pass


class LocalPrompt:
    is_fallback = True
    version = 0
    def __init__(self, text):
        self.text = text
    def compile(self, **variables):
        text = self.text
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text


def get_langfuse(service_name="bank-workshop"):
    from northwind import tracing, masking
    global _langfuse
    if not tracing.ENABLED:
        return UntracedClient()
    if _langfuse is None:
        if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
            raise RuntimeError("Set workshop Langfuse keys in .env before enabling tracing.")
        from langfuse import Langfuse
        _langfuse = Langfuse(public_key=LANGFUSE_PUBLIC_KEY, secret_key=LANGFUSE_SECRET_KEY,
                             base_url=LANGFUSE_BASE_URL, environment="workshop", release=RELEASE,
                             mask_otel_spans=masking.mask_otel_spans)
    return _langfuse


def require_langfuse():
    from northwind import tracing
    if not tracing.ENABLED:
        raise RuntimeError("Complete lesson 02 tracing before using Cloud helper commands.")
    return get_langfuse()


def flush():
    if _langfuse is not None:
        _langfuse.flush()


def attributes(**kwargs):
    from northwind.tracing import attributes as scope
    return scope(**kwargs)


def _propagator():
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    return CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def inject_trace_context():
    from northwind import tracing
    carrier = {}
    if tracing.ENABLED:
        _propagator().inject(carrier)
    return carrier


def extract_trace_context(carrier):
    return _propagator().extract({k: v for k, v in carrier.items()
                                 if k in ("traceparent", "tracestate", "baggage")})


def trace_url(trace_id):
    if trace_id and PROJECT_ID:
        return f"{LANGFUSE_BASE_URL}/project/{PROJECT_ID}/traces/{trace_id}"
    return LANGFUSE_BASE_URL if trace_id else None


def apm_url(trace_id):
    return None

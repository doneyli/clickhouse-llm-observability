"""
OpenTelemetry Instrumentation for Trace Evaluator.

Emits evaluation spans that link back to original LLM traces,
allowing clear visibility into which model generated a response
and which model evaluated it.

IMPORTANT: This module must be imported BEFORE running evaluations!
"""

import os
from opentelemetry import trace
from opentelemetry.sdk import trace as trace_sdk
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import Link, SpanContext, TraceFlags


# Global tracer instance
_tracer = None
_tracer_provider = None


def setup_instrumentation():
    """Initialize OpenTelemetry for the trace evaluator."""
    global _tracer, _tracer_provider

    service_name = os.getenv("OTEL_SERVICE_NAME", "trace-evaluator")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://clickstack:4318/v1/traces")
    api_key = os.getenv("CLICKSTACK_API_KEY", "")
    debug = os.getenv("DEBUG", "false").lower() == "true"

    # Create resource with service metadata
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })

    _tracer_provider = trace_sdk.TracerProvider(resource=resource)

    # OTLP exporter for ClickStack
    if api_key:
        otlp_exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers={"authorization": api_key}
        )
        _tracer_provider.add_span_processor(
            BatchSpanProcessor(otlp_exporter, max_export_batch_size=512)
        )
        print(f"OTEL export enabled: {otlp_endpoint}")
    else:
        print("Warning: CLICKSTACK_API_KEY not set - evaluation spans will not be exported")

    # Console exporter for debugging
    if debug:
        _tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        print("Console span export enabled (DEBUG mode)")

    trace.set_tracer_provider(_tracer_provider)
    _tracer = trace.get_tracer("trace-evaluator", "1.0.0")

    # Try to instrument LangChain for judge LLM calls
    try:
        from opentelemetry.instrumentation.langchain import LangchainInstrumentor
        LangchainInstrumentor().instrument(tracer_provider=_tracer_provider)
        print(f"OpenLLMetry instrumentation enabled for: {service_name}")
    except ImportError:
        print("LangChain instrumentation not available")
    except Exception as e:
        print(f"Warning: Could not instrument LangChain: {e}")

    return _tracer_provider


def get_tracer():
    """Get the configured tracer instance."""
    global _tracer
    if _tracer is None:
        setup_instrumentation()
    return _tracer


def create_span_link(trace_id_hex: str, span_id_hex: str = None) -> Link:
    """
    Create an OTEL span link to an existing trace.

    This allows evaluation spans to link back to the original LLM trace,
    creating a clear relationship: "this evaluation is for that LLM call".

    Args:
        trace_id_hex: The original trace ID as a hex string (32 chars)
        span_id_hex: Optional span ID as a hex string (16 chars)

    Returns:
        Link object that can be passed to start_as_current_span, or None if invalid
    """
    try:
        # Convert hex strings to integers
        # Trace IDs are 128-bit (32 hex chars), Span IDs are 64-bit (16 hex chars)
        trace_id_int = int(trace_id_hex.replace("-", ""), 16)
        span_id_int = int(span_id_hex.replace("-", ""), 16) if span_id_hex else 0
    except (ValueError, AttributeError):
        # Invalid trace/span ID format
        return None

    span_context = SpanContext(
        trace_id=trace_id_int,
        span_id=span_id_int,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )

    return Link(span_context)


def shutdown():
    """Flush and shutdown the tracer provider."""
    global _tracer_provider
    if _tracer_provider:
        _tracer_provider.shutdown()

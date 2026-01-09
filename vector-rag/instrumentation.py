"""
OpenTelemetry + OpenLLMetry Instrumentation

IMPORTANT: This module must be imported BEFORE LangChain!
"""

import os
from opentelemetry import trace
from opentelemetry.sdk import trace as trace_sdk
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor


def setup_instrumentation():
    """Initialize OpenTelemetry with OpenLLMetry for LangChain."""

    service_name = os.getenv("OTEL_SERVICE_NAME", "vector-rag-demo")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    api_key = os.getenv("CLICKSTACK_API_KEY", "")
    debug = os.getenv("DEBUG", "false").lower() == "true"

    # Create resource with service metadata
    resource = Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    })

    tracer_provider = trace_sdk.TracerProvider(resource=resource)

    # OTLP exporter for ClickStack
    if api_key:
        otlp_exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers={"authorization": api_key}
        )
        tracer_provider.add_span_processor(
            BatchSpanProcessor(otlp_exporter, max_export_batch_size=512)
        )
        print(f"OTLP export enabled: {otlp_endpoint}")
    else:
        print("CLICKSTACK_API_KEY not set - traces will not be exported")

    # Console exporter for debugging
    if debug:
        tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        print("Console span export enabled (DEBUG mode)")

    trace.set_tracer_provider(tracer_provider)

    # Enable OpenLLMetry auto-instrumentation for LangChain
    LangchainInstrumentor().instrument(tracer_provider=tracer_provider)

    print(f"OpenLLMetry instrumentation enabled for: {service_name}")
    return tracer_provider


def get_tracer(name: str = "vector-rag"):
    return trace.get_tracer(name)

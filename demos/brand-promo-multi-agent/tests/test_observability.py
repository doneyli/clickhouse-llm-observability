"""Exercise real CrewAI event instrumentation without model or network calls."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_concurrent_callbacks_export_each_llm_call_once():
    # Instrumentors, the event bus, and the OTel provider are process singletons.
    # A subprocess keeps their real lifecycle isolated from the mocked factories
    # in test_config and starts with the same cold state as run_experiment.py.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
            from concurrent.futures import ThreadPoolExecutor
            from threading import Barrier, BrokenBarrierError
            from unittest.mock import MagicMock, patch

            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
            from openinference.instrumentation.crewai import CrewAIInstrumentor
            from openinference.instrumentation.crewai._event_listener import OpenInferenceEventListener
            from crewai.events import crewai_event_bus
            from crewai.events.types.llm_events import (
                LLMCallCompletedEvent, LLMCallStartedEvent, LLMCallType,
            )

            from src.observability import make_observability_callbacks

            exporter = InMemorySpanExporter()
            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            original = CrewAIInstrumentor._instrument
            starting = Barrier(3)
            instrumenting = Barrier(3)

            def delayed_instrument(self, **kwargs):
                # Hold the first caller inside the real OTel instrumentor so
                # concurrent workers can expose duplicate event registration.
                try:
                    instrumenting.wait(timeout=1)
                except BrokenBarrierError:
                    pass
                return original(self, **kwargs)

            def build_callbacks(_):
                starting.wait(timeout=5)
                return make_observability_callbacks(agent_name="test-agent")

            with (
                patch("langfuse.get_client"),
                patch("src.observability.CallbackHandler", MagicMock()),
                patch.object(CrewAIInstrumentor, "_instrument", delayed_instrument),
                ThreadPoolExecutor(max_workers=3) as workers,
            ):
                list(workers.map(build_callbacks, range(3)))

            with provider.get_tracer("test").start_as_current_span("experiment-item") as parent:
                crewai_event_bus.emit(None, LLMCallStartedEvent(
                    call_id="test-call", model="test-model", messages="hello",
                ))
                crewai_event_bus.emit(None, LLMCallCompletedEvent(
                    call_id="test-call", model="test-model", response="world",
                    call_type=LLMCallType.LLM_CALL,
                ))
                assert crewai_event_bus.flush(timeout=5)

            generations = [s for s in exporter.get_finished_spans()
                           if s.attributes.get("openinference.span.kind") == "LLM"]
            assert len(generations) == 1, f"Expected one generation, got {len(generations)}"
            generation = generations[0]
            assert generation.parent.span_id == parent.get_span_context().span_id
            assert generation.context.trace_id == parent.get_span_context().trace_id
            assert generation.attributes["llm.model_name"] == "test-model"
            assert generation.attributes["input.value"] == "hello"
            assert generation.attributes["output.value"] == "world"
            CrewAIInstrumentor().uninstrument()
            provider.shutdown()
        """),
        ],
        env={**os.environ, "CREWAI_DISABLE_TELEMETRY": "true", "CREWAI_DISABLE_TRACKING": "true"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

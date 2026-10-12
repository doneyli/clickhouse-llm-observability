"""Real SDK export and callback hierarchy, captured in memory with synthetic data."""
import asyncio
import json
from unittest.mock import patch
import unittest
import uuid
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langfuse import Langfuse
from langfuse.api import Prompt_Text
from langfuse.model import TextPromptClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from northwind import agent, config, masking, prompts
from tests.test_workshop import fake_transport, FakeSession


class ScriptedModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "workshop-scripted"
    @property
    def _identifying_params(self):
        return {"model_name": "workshop-scripted"}
    def bind_tools(self, tools):
        return self
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content="The wire fee is USD 35, cutoff 3:00 PM [KB-202].")
        else:
            message = AIMessage(content="", tool_calls=[{"name":"search_knowledge_base",
                "args":{"query":"international wire fee cutoff"}, "id":"scripted-tool"}])
        message.usage_metadata = {"input_tokens":10,"output_tokens":5,"total_tokens":15}
        return ChatResult(generations=[ChatGeneration(message=message)])


class TraceExport(unittest.TestCase):
    def test_real_callbacks_export_one_nested_trace_without_network(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        key = "pk-lf-offline-" + uuid.uuid4().hex
        client = Langfuse(public_key=key, secret_key="sk-lf-synthetic-test-only",
                         base_url="http://127.0.0.1:9", tracer_provider=provider,
                         span_exporter=exporter, mask_otel_spans=masking.mask_otel_spans)
        prompt = TextPromptClient(Prompt_Text(name=prompts.PROMPT_NAME, version=7,
            prompt="Use banking policy tools.", config={}, labels=["workshop-candidate"], tags=[]))
        try:
            with patch.dict("os.environ", {"LANGFUSE_PUBLIC_KEY":key, "NORTHWIND_PROMPT_LABEL":"workshop-candidate"}), \
                 patch.object(config,"get_langfuse",return_value=client), \
                 patch.object(prompts.prompt_source,"resolve",return_value=prompt), \
                 patch.object(agent,"make_llm",return_value=ScriptedModel()), \
                 patch.object(agent,"streamablehttp_client",fake_transport), \
                 patch.object(agent,"ClientSession",FakeSession), \
                 patch.object(agent.monitoring,"record"):
                result = asyncio.run(agent.run_turn("What is the wire fee? My card number is 4111 1111 1111 1111.",
                                                    session_id="offline-session"))
            client.flush()
            spans = exporter.get_finished_spans()
            self.assertIsNone(result["error"])
            roots = [span for span in spans if span.parent is None]
            self.assertEqual(len(roots),1)
            self.assertEqual(roots[0].name,"northwind-assistant")
            self.assertEqual(len({span.context.trace_id for span in spans}),1)
            types = {span.attributes.get("langfuse.observation.type") for span in spans}
            self.assertTrue({"agent","generation","retriever","guardrail"} <= types,types)
            attributes = " ".join(str(dict(span.attributes)) for span in spans)
            self.assertNotIn("4111 1111 1111 1111",attributes)
            self.assertIn("REDACTED_CARD",attributes)
            self.assertIn("offline-session",attributes)
            retrieval = next(span for span in spans if span.name == "kb-retrieval")
            documents = json.loads(retrieval.attributes["langfuse.observation.output"])["documents"]
            self.assertTrue(all(doc["text"] for doc in documents))
            guardrail = next(span for span in spans if span.name == "output-guardrail"
                             and span.attributes.get("langfuse.observation.type") == "guardrail")
            transformed = json.loads(guardrail.attributes["langfuse.observation.output"])
            self.assertTrue(transformed["pii_warning_added"])
            self.assertEqual(transformed["answer"], result["answer"])
            self.assertEqual(guardrail.attributes["langfuse.observation.metadata.pii_warning_added"], "true")
            self.assertEqual(roots[0].attributes.get("langfuse.trace.metadata.prompt_label"), "workshop-candidate")
            generations = [span for span in spans if span.attributes.get("langfuse.observation.type") == "generation"]
            self.assertTrue(generations)
            for generation in generations:
                self.assertEqual(generation.attributes.get("langfuse.observation.prompt.name"), prompts.PROMPT_NAME)
                self.assertEqual(generation.attributes.get("langfuse.observation.prompt.version"), 7)
        finally:
            client.shutdown()


if __name__ == "__main__":
    unittest.main()

"""
Doc-intake service — a Strands agent behind A2A, owned (in the story) by the
document-processing team. Run from its own venv:

    doc_intake/.venv/bin/python -m doc_intake.service        (from demos/insurance-claims)

The LangGraph claims orchestrator calls it with A2A `message/send`: one text part
(the task) plus one file part per claim document. The agent reads the documents with
a vision model and returns the extracted fields as JSON.

Cross-process, cross-framework tracing:
  * the caller sends W3C `traceparent` + `baggage` as HTTP headers;
  * this executor extracts them before anything runs, so every span here — our
    `doc-intake` wrapper and Strands' own invoke_agent / cycle / chat spans — is a
    child in the caller's trace, with the caller's session, user, version and
    environment (carried by the baggage);
  * `propagate_attributes(prompt=...)` links Strands' generations to the Langfuse
    prompt version that was served;
  * the documents go on the wrapper's input as Langfuse media, so they render in the
    trace and a multi-modal judge can read them.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import FilePart, Part, TextPart
from a2a.utils import new_task
from langfuse import propagate_attributes
from langfuse.media import LangfuseMedia
from opentelemetry import context as otel_context
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands.multiagent.a2a import A2AServer
from strands.multiagent.a2a.executor import StrandsA2AExecutor

from claims import config, prompts

log = logging.getLogger("doc-intake")
langfuse = config.get_langfuse(service_name="doc-intake-service")
NAME = "doc-intake"


def _agent(system_prompt: str, model_id: str, max_tokens: int = 2048) -> Agent:
    return Agent(
        name=NAME,
        description="Extracts fields from death-claim documents (certificates, statements, beneficiary forms).",
        model=AnthropicModel(model_id=model_id, max_tokens=max_tokens),
        system_prompt=system_prompt,
        callback_handler=None,
        trace_attributes={"team": "document-processing", "framework": "strands"},
    )


def parse_json(text: str):
    """The agent's JSON answer, tolerating a ```json fence."""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


class TracedIntakeExecutor(StrandsA2AExecutor):
    """Run the agent once per request, inside the caller's trace."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        headers = {}
        if context.call_context is not None:
            headers = context.call_context.state.get("headers") or {}
        carrier = {k: v for k, v in headers.items() if k.lower() in config.CARRIER_KEYS}
        token = otel_context.attach(config.extract_trace_context(carrier)) if carrier else None

        task = context.current_task or new_task(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        meta = dict(context.message.metadata or {}) if context.message else {}
        try:
            parts = context.message.parts if context.message else []
            documents, task_text = [], []
            for p in parts:
                root = p.root
                if isinstance(root, FilePart) and getattr(root.file, "bytes", None):
                    documents.append({"name": root.file.name, "mime_type": root.file.mime_type,
                                      "content": LangfuseMedia(content_bytes=base64.b64decode(root.file.bytes),
                                                               content_type=root.file.mime_type)})
                elif isinstance(root, TextPart):
                    task_text.append(root.text)
            system_prompt, lf_prompt, cfg = prompts.get(langfuse, NAME, meta.get("prompt_label"))
            model_id = cfg.get("model") or config.INTAKE_MODEL
            with propagate_attributes(prompt=lf_prompt), langfuse.start_as_current_observation(
                    as_type="agent", name=NAME,
                    input={"task": "\n".join(task_text), "documents": documents},
                    metadata={"role": "doc_intake", "framework": "strands", "protocol": "a2a",
                              "a2a.context_id": task.context_id, "a2a.task_id": task.id,
                              "claim_id": str(meta.get("claim_id", "")), "model": model_id,
                              "prompt_version": str(getattr(lf_prompt, "version", "fallback")),
                              "linked_by": "traceparent + baggage headers" if "baggage" in {
                                  k.lower() for k in carrier} else ("traceparent header" if carrier else "none")},
            ) as obs:
                blocks = self._convert_a2a_parts_to_content_blocks(parts)
                result = await _agent(system_prompt, model_id, int(cfg.get("max_tokens", 2048))).invoke_async(blocks)
                text = str(result)
                parsed = parse_json(text)
                obs.update(output=parsed if parsed is not None else text,
                           level="DEFAULT" if parsed is not None else "WARNING",
                           status_message=None if parsed is not None else "agent did not return JSON")
            await updater.add_artifact([Part(root=TextPart(text=text))], name="extraction")
            await updater.complete()
        except Exception as exc:  # noqa: BLE001 — surface as a failed A2A task, never hang the caller
            log.exception("doc-intake failed")
            await updater.failed(message=updater.new_agent_message(
                parts=[Part(root=TextPart(text=f"doc-intake failed: {type(exc).__name__}: {exc}"[:500]))]))
        finally:
            config.flush()
            if token is not None:
                otel_context.detach(token)


def build_server() -> A2AServer:
    def factory(context_id: str) -> Agent:
        return _agent(prompts.DOC_INTAKE_V1, config.INTAKE_MODEL)

    server = A2AServer(agent_factory=factory, host="127.0.0.1", port=config.DOC_INTAKE_PORT,
                       version=config.RELEASE)
    server.request_handler = DefaultRequestHandler(
        agent_executor=TracedIntakeExecutor(agent_factory=factory), task_store=InMemoryTaskStore())
    return server


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_server().to_starlette_app(), host="127.0.0.1", port=config.DOC_INTAKE_PORT,
                log_level="warning")

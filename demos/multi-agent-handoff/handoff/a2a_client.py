"""
Calling another agent over A2A 1.0 with the official a2a-sdk client.

One `send()` = one `SendMessage`. Trace headers go in `ClientCallContext.service_parameters`,
which the JSON-RPC transport sends as HTTP headers on that call only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from handoff import config  # noqa: F401  (sets OTEL_INSTRUMENTATION_A2A_SDK_ENABLED before a2a)

import httpx
from a2a.client import ClientCallContext, ClientConfig, create_client
from a2a.helpers.proto_helpers import new_data_part, new_text_part
from a2a.types import Message, Role, SendMessageRequest, TaskState
from google.protobuf import json_format

STATES = {TaskState.TASK_STATE_COMPLETED: "completed", TaskState.TASK_STATE_INPUT_REQUIRED: "input-required",
          TaskState.TASK_STATE_FAILED: "failed", TaskState.TASK_STATE_REJECTED: "rejected",
          TaskState.TASK_STATE_CANCELED: "canceled", TaskState.TASK_STATE_WORKING: "working",
          TaskState.TASK_STATE_SUBMITTED: "submitted", TaskState.TASK_STATE_AUTH_REQUIRED: "auth-required"}


class A2AError(RuntimeError):
    pass


@dataclass
class A2AResult:
    state: str  # completed | input-required | failed | ...
    text: str
    data: dict = field(default_factory=dict)
    task_id: str = ""
    context_id: str = ""
    agent_version: str = ""


def _parts(parts) -> tuple[list[str], dict]:
    texts, data = [], {}
    for p in parts:
        if p.HasField("text"):
            texts.append(p.text)
        elif p.HasField("data"):
            value = json_format.MessageToDict(p.data)
            if isinstance(value, dict):
                data.update(value)
    return texts, data


class A2AClientPool:
    """One a2a-sdk client per agent URL; the agent card is resolved once."""

    def __init__(self, timeout: Optional[float] = None):
        # HANDOFF_A2A_TIMEOUT_S: how long a handoff waits for the remote agent (fault F5 lowers it)
        self._timeout = timeout or float(config.setting("HANDOFF_A2A_TIMEOUT_S", "120"))
        self._clients: dict[str, Any] = {}
        self._cards: dict[str, Any] = {}
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self, url: str):
        if url not in self._clients:
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=self._timeout)
            client = await create_client(url, client_config=ClientConfig(streaming=False, httpx_client=self._http))
            self._clients[url] = client
        return self._clients[url]

    async def agent_card(self, url: str):
        if url not in self._cards:
            client = await self._client(url)
            self._cards[url] = await client.get_card() if hasattr(client, "get_card") else None
        return self._cards[url]

    async def send(self, url: str, *, text: str, data: Optional[dict] = None, task_id: str = "",
                   context_id: str = "", headers: Optional[dict] = None,
                   metadata: Optional[dict] = None) -> A2AResult:
        try:  # an unreachable agent fails at card resolution: that is a failed handoff, not a crash
            client = await self._client(url)
        except Exception as exc:  # noqa: BLE001
            raise A2AError(f"{type(exc).__name__}: {exc}") from exc
        parts = [new_text_part(text)] + ([new_data_part(data)] if data else [])
        msg = Message(role=Role.ROLE_USER, message_id=str(uuid.uuid4()), parts=parts)
        if task_id:
            msg.task_id = task_id
        if context_id:
            msg.context_id = context_id
        if metadata:
            msg.metadata.update(metadata)
        ctx = ClientCallContext(service_parameters=dict(headers or {}), timeout=self._timeout)
        result: Optional[A2AResult] = None
        try:
            async for resp in client.send_message(SendMessageRequest(message=msg), context=ctx):
                if resp.HasField("task"):
                    t = resp.task
                    texts, data_out = [], {}
                    for a in t.artifacts:
                        tx, d = _parts(a.parts)
                        texts += tx
                        data_out.update(d)
                    if t.status.HasField("message"):
                        tx, d = _parts(t.status.message.parts)
                        texts += tx
                        data_out.update(d)
                    result = A2AResult(state=STATES.get(t.status.state, str(t.status.state)),
                                       text="\n".join(x for x in texts if x), data=data_out,
                                       task_id=t.id, context_id=t.context_id)
                elif resp.HasField("message"):
                    tx, d = _parts(resp.message.parts)
                    result = A2AResult(state="completed", text="\n".join(tx), data=d,
                                       task_id=resp.message.task_id, context_id=resp.message.context_id)
        except Exception as exc:  # noqa: BLE001 — a dead or slow agent is a failed handoff, not a crash
            raise A2AError(f"{type(exc).__name__}: {exc}"[:400]) from exc
        if result is None:
            raise A2AError("no response from agent")
        card = self._cards.get(url)
        result.agent_version = getattr(card, "version", "") if card is not None else ""
        return result

    async def aclose(self) -> None:
        for c in self._clients.values():
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass
        if self._http is not None:
            await self._http.aclose()

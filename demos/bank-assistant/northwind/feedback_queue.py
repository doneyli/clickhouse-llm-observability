"""Thumbs-down → review queue, at the moment the customer clicks (EVA-06).

Langfuse has no built-in "score filter → annotation queue" automation, so the
app does it with the public API: when it records `user-feedback = 0` it also
adds that trace to a dedicated queue that the product owner works through.
That is one extra call in the feedback handler (portal/server.py).

scripts/fill_annotation_queue.py stays the batch path: a worst-first SME backlog
that also ranks judge and policy scores. The two are independent: a thumbs-down
answer can be in both queues, because the questions differ. Here the product
owner asks what to do about it; there an SME labels it so the judges can be
calibrated.

Queue items are TRACES because `user-feedback` is a trace score (the customer
rated the whole turn). A trace id is all the app knows when the button is
clicked, so no read is needed and the item can be added before the trace has
finished ingesting.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from typing import Callable, Optional

QUEUE_NAME = "Negative feedback — thumbs down"
QUEUE_DESCRIPTION = ("Every customer thumbs-down, added by the app the moment it is clicked. "
                     "Triage each one: what went wrong (sme-failure-mode) and what to do about it "
                     "(pm-action).")

# The decision the product owner records. `add-to-dataset` is the bridge to
# offline evaluation: those traces become golden-set items.
PM_ACTION_CONFIG = {
    "name": "pm-action", "dataType": "CATEGORICAL",
    "description": "Product-owner triage of a thumbs-down answer: what to do about it",
    "categories": [{"label": c, "value": i} for i, c in enumerate(
        ["fix-agent", "fix-knowledge-base", "add-to-dataset", "expected-behaviour", "no-action"])],
}
FAILURE_MODE_CONFIG = "sme-failure-mode"
FAILURE_MODE_SCORE_CONFIG = {
    "name": FAILURE_MODE_CONFIG, "dataType": "CATEGORICAL", "description": "Open-coded failure category",
    "categories": [{"label": c, "value": i} for i, c in enumerate(
        ["none", "wrong-number", "missing-citation", "unsafe-advice", "upsell", "tool-misuse",
         "should-have-escalated"])],
}
QUEUE_SCORE_CONFIGS = (FAILURE_MODE_SCORE_CONFIG, PM_ACTION_CONFIG)

Api = Callable[..., Optional[dict]]  # config.api(method, path, body=None, params=...)


def _page_all(api: Api, path: str) -> list:
    """Queues, queue items and score configs use page/totalPages pagination."""
    out, page = [], 1
    while True:
        response = api("GET", path, params={"limit": 100, "page": page}) or {}
        out += response.get("data") or []
        if page >= ((response.get("meta") or {}).get("totalPages") or 1):
            return out
        page += 1


class FeedbackQueue:
    """Resolves (or creates) the queue once, then adds each thumbs-down trace once."""

    def __init__(self, api: Api, *, retries: int = 2, retry_delay: float = 2.0):
        self.api = api
        self.retries = retries
        self.retry_delay = retry_delay
        self.queue_id: Optional[str] = None
        self._queued: set = set()
        self._pending: dict[str, Future] = {}
        self._lock = threading.Lock()

    def ensure(self) -> str:
        """The queue id. Creates both score configs and the queue when missing. Idempotent."""
        with self._lock:
            if self.queue_id:
                return self.queue_id
            queue = next((q for q in _page_all(self.api, "/api/public/annotation-queues")
                          if q.get("name") == QUEUE_NAME), None)
            if queue is None:
                configs = {c["name"]: c["id"] for c in _page_all(self.api, "/api/public/score-configs")}
                for definition in QUEUE_SCORE_CONFIGS:
                    if definition["name"] not in configs:
                        configs[definition["name"]] = self.api(
                            "POST", "/api/public/score-configs", definition)["id"]
                ids = [configs[c["name"]] for c in QUEUE_SCORE_CONFIGS]
                queue = self.api("POST", "/api/public/annotation-queues", {
                    "name": QUEUE_NAME, "description": QUEUE_DESCRIPTION, "scoreConfigIds": ids})
            # Known items, so a second click on the same answer (or a restart) adds nothing.
            self._queued = {i["objectId"] for i in
                            _page_all(self.api, f"/api/public/annotation-queues/{queue['id']}/items")
                            if i.get("objectType") == "TRACE"}
            self.queue_id = queue["id"]
            return self.queue_id

    def add(self, trace_id: str) -> dict:
        """Add one trace. Returns {"queue_id", "added"}; added is False if it was already queued."""
        queue_id = self.ensure()
        with self._lock:
            if trace_id in self._queued:
                return {"queue_id": queue_id, "added": False}
            pending = self._pending.get(trace_id)
            owner = pending is None
            if owner:
                pending = self._pending[trace_id] = Future()
        if not owner:
            # A repeat click shares the actual outcome, including a failed POST.
            pending.result()
            return {"queue_id": queue_id, "added": False}
        body = {"objectId": trace_id, "objectType": "TRACE", "status": "PENDING"}
        path = f"/api/public/annotation-queues/{queue_id}/items"
        try:
            for attempt in range(self.retries + 1):
                try:
                    self.api("POST", path, body)
                    break
                except Exception:  # retry, then propagate the final failure
                    if attempt == self.retries:
                        raise
                    time.sleep(self.retry_delay * (attempt + 1))
        except Exception as exc:
            with self._lock:
                del self._pending[trace_id]  # let a later click try again
            pending.set_exception(exc)
            raise
        with self._lock:
            self._queued.add(trace_id)
            del self._pending[trace_id]
        pending.set_result(None)
        return {"queue_id": queue_id, "added": True}

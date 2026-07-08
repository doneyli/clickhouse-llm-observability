"""Langfuse adapter for the experiment runner.

Wraps the existing `dataset.run_experiment(...)` SDK call + REST score
persistence + annotation queue POST. This is a thin extraction of the
logic that previously lived inline in scripts/run_experiment.py — the
runner now dispatches via `get_backend("langfuse")`.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from typing import Any


class LangfuseBackend:
    """Langfuse-side experiment plumbing (dataset load + run + score POST)."""

    def __init__(self) -> None:
        from src.config import load_env

        env = load_env()
        self._host = env.langfuse_host or os.getenv(
            "LANGFUSE_HOST", "http://localhost:3001"
        )
        if not env.langfuse_public_key or not env.langfuse_secret_key:
            raise RuntimeError(
                "LangfuseBackend requires LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY"
            )
        self._auth = base64.b64encode(
            f"{env.langfuse_public_key}:{env.langfuse_secret_key}".encode()
        ).decode()

        from langfuse import get_client

        self._client = get_client()

    def load_dataset(self, name: str) -> Any:
        dataset = self._client.get_dataset(name)
        # SDK/server version mismatch workaround: langfuse v4 SDK sends
        # `datasetVersion` field; langfuse server <4 rejects it. The SDK
        # honors `Ellipsis` as an OMIT sentinel.
        if hasattr(dataset, "version"):
            dataset.version = ...  # type: ignore[assignment]
        return dataset

    def run_experiment(
        self,
        *,
        dataset: Any,
        run_name: str,
        task: Any,
        evaluators: list[Any],
        run_evaluators: list[Any],
        metadata: dict[str, Any],
        max_concurrency: int,
    ) -> Any:
        return dataset.run_experiment(
            name=dataset.name,
            run_name=run_name,
            description=f"PromoPlanner experiment: {run_name}",
            task=task,
            evaluators=evaluators,
            run_evaluators=run_evaluators,
            max_concurrency=max_concurrency,
            metadata=metadata,
        )

    def persist_run_scores(
        self,
        item_results: list[Any],
        run_evaluations: list[Any],
    ) -> None:
        first_trace_id = next(
            (ir.trace_id for ir in item_results if hasattr(ir, "trace_id") and ir.trace_id),
            None,
        )
        if not first_trace_id:
            return

        headers = {
            "Authorization": f"Basic {self._auth}",
            "Content-Type": "application/json",
        }
        for ev in run_evaluations:
            if ev.value is None:
                continue
            try:
                body = json.dumps(
                    {
                        "traceId": first_trace_id,
                        "name": ev.name,
                        "value": ev.value,
                        "comment": ev.comment or "",
                        "dataType": "NUMERIC",
                    }
                ).encode()
                req = urllib.request.Request(
                    f"{self._host}/api/public/scores",
                    data=body,
                    headers=headers,
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10)
            except Exception as e:
                print(
                    f"  Warning: failed to persist run score '{ev.name}': {e}",
                    file=sys.stderr,
                )

    def queue_failed_items(
        self,
        item_results: list[Any],
        queue_name: str,
    ) -> None:
        headers = {
            "Authorization": f"Basic {self._auth}",
            "Content-Type": "application/json",
        }
        try:
            req = urllib.request.Request(
                f"{self._host}/api/public/annotation-queues?limit=100",
                headers=headers,
            )
            resp = urllib.request.urlopen(req, timeout=10)
            queues = json.loads(resp.read()).get("data", [])
            queue_id = next((q["id"] for q in queues if q["name"] == queue_name), None)
            if not queue_id:
                print(
                    f"  Warning: annotation queue '{queue_name}' not found. "
                    "Run seed_annotation_queue.py first.",
                    file=sys.stderr,
                )
                return
        except Exception as e:
            print(f"  Warning: could not list annotation queues: {e}", file=sys.stderr)
            return

        queued = 0
        for ir in item_results:
            if not hasattr(ir, "trace_id") or not ir.trace_id:
                continue
            should_queue = any(
                (
                    ev.name in ("intent_classification_accuracy", "compliance_status_match")
                    and ev.value == 0.0
                )
                or (
                    ev.name in ("response_factuality", "tool_call_correctness")
                    and ev.value is not None
                    and ev.value < 0.5
                )
                for ev in ir.evaluations
            )
            if not should_queue:
                continue
            try:
                body = json.dumps(
                    {
                        "objectId": ir.trace_id,
                        "objectType": "TRACE",
                        "status": "PENDING",
                    }
                ).encode()
                req = urllib.request.Request(
                    f"{self._host}/api/public/annotation-queues/{queue_id}/items",
                    data=body,
                    headers=headers,
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=10)
                queued += 1
            except Exception as e:
                print(
                    f"  Warning: failed to queue trace {str(ir.trace_id)[:12]}...: {e}",
                    file=sys.stderr,
                )

        if queued:
            print(
                f"\n  Queued {queued} items for human review in '{queue_name}'",
                file=sys.stderr,
            )

    def flush(self) -> None:
        self._client.flush()

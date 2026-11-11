"""Keep the promotion planner's trace summary focused on the user request."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler


class PromoPlannerCallbackHandler(LangfuseCallbackHandler):
    """Format root IO while retaining full state on the workflow's child spans."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._planner_runs: set[UUID] = set()

    def on_chain_start(
        self,
        serialized: Any,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        if parent_run_id is None and isinstance(inputs, dict) and "query" in inputs:
            self._planner_runs.add(run_id)
            inputs = {"query": inputs["query"]}
        return super().on_chain_start(
            serialized, inputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs
        )

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> Any:
        if run_id in self._planner_runs:
            if isinstance(outputs, dict):
                outputs = outputs.get("final_brief")
            # LangChain may supply the original state again at chain end.
            kwargs.pop("inputs", None)
        try:
            return super().on_chain_end(outputs, run_id=run_id, **kwargs)
        finally:
            self._planner_runs.discard(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> Any:
        if run_id in self._planner_runs:
            kwargs.pop("inputs", None)
        try:
            return super().on_chain_error(error, run_id=run_id, **kwargs)
        finally:
            self._planner_runs.discard(run_id)

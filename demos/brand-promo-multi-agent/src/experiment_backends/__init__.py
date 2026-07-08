"""Experiment-runner backends.

Each backend implements the same surface:
  - load_dataset(name) -> iterable of items, each with `input` (dict),
    `expected_output` (dict), and an `id`
  - run_experiment(dataset, run_name, task, evaluators, run_evaluators,
    metadata, max_concurrency) -> ExperimentResult
  - persist_run_scores(item_results, run_evaluations)
  - queue_failed_items(item_results, queue_name)

The Langfuse backend wraps the existing `dataset.run_experiment(...)` SDK call.

`get_backend(name)` resolves a backend by name.
"""

from __future__ import annotations

from typing import Any, Protocol


class ExperimentResult(Protocol):
    item_results: list[Any]
    run_evaluations: list[Any]


class ExperimentBackend(Protocol):
    def load_dataset(self, name: str) -> Any: ...
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
    ) -> ExperimentResult: ...

    def persist_run_scores(
        self,
        item_results: list[Any],
        run_evaluations: list[Any],
    ) -> None: ...

    def queue_failed_items(
        self,
        item_results: list[Any],
        queue_name: str,
    ) -> None: ...


def get_backend(name: str) -> ExperimentBackend:
    if name == "langfuse":
        from src.experiment_backends.langfuse_backend import LangfuseBackend

        return LangfuseBackend()
    raise ValueError(f"Unknown experiment backend: {name}")

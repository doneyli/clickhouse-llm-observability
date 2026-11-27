"""Shared types: questions, a run's result, and the scenario grid."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Question:
    """A question for the agent.

    ``answer_keys`` (dataset questions only) are the names the agent must report its
    final answer under, e.g. ``["origin", "carrier", "month"]``; ``expected`` holds the
    ground truth for those keys, computed from ``reference_sql`` at seed time.
    """

    id: str
    text: str
    answer_keys: list[str] = field(default_factory=list)
    expected: Optional[dict[str, Any]] = None
    reference_sql: Optional[str] = None
    notes: str = ""


@dataclass
class Lever:
    name: str  # identifier, also the {name:Type} placeholder in the template
    ch_type: str  # ClickHouse type of the placeholder, e.g. String, UInt16, Array(String)
    values: list[Any]


@dataclass
class Plan:
    """One round's search: a SQL template and the grid of lever values to sweep."""

    sql_template: str
    levers: list[Lever]
    metric_name: str
    objective: str  # "maximize" | "minimize"
    reasoning: str = ""


@dataclass
class ScenarioOutcome:
    scenario: dict[str, Any]  # lever name -> value
    metric: Optional[float]
    support: Optional[int]
    row: Optional[dict[str, Any]]
    server_ms: Optional[float]
    roundtrip_ms: Optional[float]
    read_rows: Optional[int]
    error: Optional[str] = None


@dataclass
class RoundRecord:
    index: int
    plan: Plan
    outcomes: list[ScenarioOutcome]
    sweep_wall_ms: float
    truncated: bool = False

    @property
    def ok(self) -> list[ScenarioOutcome]:
        return [o for o in self.outcomes if o.error is None and o.metric is not None]


@dataclass
class RunResult:
    trace_id: str
    question_id: str
    stop_reason: str  # answered | max_rounds | budget | error
    summary: str
    answer_values: dict[str, Any]
    best_scenario: dict[str, Any]
    metric_value: Optional[float]
    rounds: int
    scenarios_evaluated: int
    llm_calls: int
    db_server_ms_total: float
    db_wait_ms_total: float
    wall_ms: float
    cost_usd: float
    recommendation_grounded: Optional[bool]
    trace_url: Optional[str] = None
    error: Optional[str] = None

    @property
    def db_time_share(self) -> float:
        return self.db_wait_ms_total / self.wall_ms if self.wall_ms else 0.0

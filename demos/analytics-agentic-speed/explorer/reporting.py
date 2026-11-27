"""Render the final report from recorded scenario facts, without model-written prose.

The analyst still chooses when to stop and which scenario to recommend. Its draft
summary stays on its generation for inspection; the user-facing report states only
what was observed, what was verified, and how much work was actually done.
"""
from __future__ import annotations

import json
import math
from typing import Any, Optional

from .models import RoundRecord, ScenarioOutcome
from .scenarios import scenario_key


def find_scenario(rounds: list[RoundRecord], named: dict[str, Any]) -> Optional[tuple[RoundRecord, ScenarioOutcome]]:
    key = scenario_key(named)
    for rec in reversed(rounds):
        for outcome in reversed(rec.outcomes):
            if scenario_key(outcome.scenario) == key:
                return rec, outcome
    return None


def has_metric(outcome: ScenarioOutcome) -> bool:
    return outcome.error is None and outcome.metric is not None and math.isfinite(outcome.metric)


def build_summary(rounds: list[RoundRecord], named: dict[str, Any], *, grounded: Optional[bool],
                  stop_reason: str) -> tuple[str, dict[str, Any]]:
    """A bounded set of claims, each derived from complete result records.

    Counts include repeat queries across rounds. A NULL metric is reported as missing
    evidence, never as a successful threshold crossing or a measured zero. Even a
    verified scenario establishes neither a global optimum nor population coverage.
    """
    outcomes = [o for rec in rounds for o in rec.outcomes]
    with_metric = sum(has_metric(o) for o in outcomes)
    failed = sum(o.error is not None for o in outcomes)
    evidence: dict[str, Any] = {
        "format": "scenario-evidence-v1", "scope": "tested_scenarios",
        "scenario_queries": len(outcomes), "rounds": len(rounds),
        "with_metric": with_metric, "without_metric": len(outcomes) - with_metric - failed,
        "failed": failed, "selected": None, "metric_verified": False,
    }
    hit = find_scenario(rounds, named)
    if grounded is True and stop_reason == "answered" and hit is not None and has_metric(hit[1]):
        rec, outcome = hit
        evidence["selected"] = {"round": rec.index, "scenario": outcome.scenario,
                                "metric": outcome.metric, "support": outcome.support}
        evidence["metric_verified"] = True
        point = json.dumps(outcome.scenario, sort_keys=True, ensure_ascii=False)
        support = f" (reported support: {outcome.support:,})" if outcome.support is not None else ""
        lead = (f"For scenario {point}, the measured metric was {outcome.metric:.6g}{support}. "
                "A repeat query confirmed this metric.")
    elif stop_reason == "error":
        lead = "The run ended with an error; no verified recommendation is reported."
    elif stop_reason in ("max_rounds", "budget"):
        limit = "round" if stop_reason == "max_rounds" else "budget"
        lead = f"The run reached its {limit} limit without a verified recommendation."
    else:
        lead = "The scenario results do not support a verified recommendation."
    summary = (f"{lead} Evaluated {len(outcomes):,} scenario queries across {len(rounds)} round(s): "
               f"{with_metric:,} returned a usable metric, {evidence['without_metric']:,} returned no usable metric, "
               f"and {failed:,} failed. These measurements cover only the tested scenarios.")
    return summary, evidence

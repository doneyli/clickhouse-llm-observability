"""Deterministic scoring for the `scenario-explorer-questions` dataset, plus the dataset's own helpers.

Ground truth comes from each question's ``reference_sql`` (computed at seed time), so no
LLM judge is involved: an answer is right when every answer key matches the reference.

Evaluator signatures follow the installed Langfuse SDK (4.18, ``langfuse/experiment.py``):

- item evaluator: ``f(*, input, output, expected_output, metadata, **kwargs)``
  returning ``Evaluation`` or ``list[Evaluation]``;
- run evaluator: ``f(*, item_results, **kwargs)`` where each item result has
  ``.item`` (a ``DatasetItem``), ``.output`` and ``.evaluations``.

The task output these evaluators read is the dict built by
``scripts/run_experiment.py`` (``task_output()``): ``answer_values`` plus the run's
numbers, and ``error`` when the run failed. A failed run is scored as incorrect, never
dropped.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
from langfuse import Evaluation

from .config import DEMO_DIR
from .models import Question

QUESTIONS_PATH = DEMO_DIR / "data" / "questions.yaml"
FLOAT_REL_TOL = 0.01

# Numbers the run reports about itself, passed through as NUMERIC item scores so the
# experiment compare view can put accuracy next to database time and cost.
PASS_THROUGH_METRICS = (
    "db_time_share",
    "scenarios_evaluated",
    "wall_ms",
    "run_cost_usd",
    "llm_calls",
    "db_server_ms_total",
)
# The agent already writes these as trace scores on the item's own trace, and the
# experiment-items API returns trace scores with each item. Re-emitting them as item
# scores would show every one twice in the compare view, so the item evaluator emits
# only the pass-throughs the agent doesn't write itself.
AGENT_TRACE_SCORES = frozenset({"db_time_share", "scenarios_evaluated", "run_cost_usd",
                                "llm_calls", "db_server_ms_total"})
ITEM_PASS_THROUGH = tuple(m for m in PASS_THROUGH_METRICS if m not in AGENT_TRACE_SCORES)

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+)$")  # no exponents: '1E5' stays a string


# ----------------------------------------------------------------------- normalisation
def normalize(value: Any) -> Any:
    """Canonical form of one answer value.

    Strings are trimmed (including FixedString NUL padding) and upper-cased; numeric
    strings become numbers ('09' -> 9, '12.50' -> 12.5); ints stay ints; a float with
    an integral value stays a float (``values_match`` compares 9.0 == 9 as equal).
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip().strip("\x00").strip()
        if _INT_RE.match(s):
            return int(s)
        if _FLOAT_RE.match(s):
            return float(s)
        return s.upper()
    return value


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def values_match(expected: Any, actual: Any) -> bool:
    """One key: ints exact, floats within 1% relative, strings case-insensitive."""
    e, a = normalize(expected), normalize(actual)
    if e is None or a is None:
        return False
    if _is_number(e) and _is_number(a):
        if isinstance(e, int):
            return float(a) == float(e)  # 9 == 9.0, but 9.4 != 9
        if not math.isfinite(e) or not math.isfinite(a):
            return False
        return math.isclose(a, e, rel_tol=FLOAT_REL_TOL, abs_tol=1e-9)
    if isinstance(e, str) and isinstance(a, str):
        return e == a
    return False


def _expected_dict(expected_output: Any) -> dict[str, Any]:
    return dict(expected_output) if isinstance(expected_output, Mapping) else {}


def _answer_dict(output: Any) -> dict[str, Any]:
    if not isinstance(output, Mapping):
        return {}
    answer = output.get("answer_values")
    if not isinstance(answer, Mapping):
        return {}
    # Key names are matched case-insensitively ("Origin" == "origin").
    return {str(k).strip().lower(): v for k, v in answer.items()}


def key_results(expected_output: Any, output: Any) -> dict[str, bool]:
    """Per answer key: did the run report the expected value?"""
    expected = _expected_dict(expected_output)
    answer = _answer_dict(output)
    return {k: values_match(v, answer.get(k.lower())) for k, v in expected.items()}


def _error_of(output: Any) -> Optional[str]:
    if not isinstance(output, Mapping):
        return "task returned no output"
    err = output.get("error")
    return str(err) if err else None


def is_correct(expected_output: Any, output: Any) -> bool:
    keys = key_results(expected_output, output)
    return bool(keys) and all(keys.values())


# ----------------------------------------------------------------------- item evaluators
def answer_correct(*, input: Any, output: Any, expected_output: Any,
                   metadata: Optional[dict] = None, **kwargs: Any) -> Evaluation:
    """BOOLEAN: every answer key matches the reference answer."""
    keys = key_results(expected_output, output)
    ok = bool(keys) and all(keys.values())
    err = _error_of(output)
    if not keys:
        comment = "no expected output on the dataset item"
    elif ok:
        comment = f"correct: {_answer_dict(output)}"
    else:
        wrong = [k for k, good in keys.items() if not good]
        comment = (f"expected {_expected_dict(expected_output)}, got {_answer_dict(output) or '{}'}; "
                   f"wrong: {', '.join(wrong)}")
        if err:
            comment += f"; run error: {err[:300]}"
    return Evaluation(name="answer_correct", value=1.0 if ok else 0.0, data_type="BOOLEAN",
                      comment=comment)


def answer_key_match(*, input: Any, output: Any, expected_output: Any,
                     metadata: Optional[dict] = None, **kwargs: Any) -> Evaluation:
    """NUMERIC 0-1: fraction of answer keys that match (partial credit)."""
    keys = key_results(expected_output, output)
    frac = sum(keys.values()) / len(keys) if keys else 0.0
    return Evaluation(name="answer_key_match", value=round(frac, 4), data_type="NUMERIC",
                      comment=", ".join(f"{k}={'ok' if v else 'wrong'}" for k, v in keys.items()) or None)


def run_metrics(*, input: Any, output: Any, expected_output: Any = None,
                metadata: Optional[dict] = None, **kwargs: Any) -> list[Evaluation]:
    """Pass-through NUMERIC scores the agent doesn't already write, plus ``task_error`` (BOOLEAN)."""
    out = output if isinstance(output, Mapping) else {}
    evals: list[Evaluation] = []
    for name in ITEM_PASS_THROUGH:
        v = out.get(name)
        if _is_number(v) and math.isfinite(float(v)):
            evals.append(Evaluation(name=name, value=round(float(v), 4), data_type="NUMERIC"))
    err = _error_of(output)
    evals.append(Evaluation(name="task_error", value=1.0 if err else 0.0, data_type="BOOLEAN",
                            comment=err[:500] if err else None))
    return evals


ITEM_EVALUATORS = [answer_correct, answer_key_match, run_metrics]


# ----------------------------------------------------------------------- run evaluator
def _item_expected(item: Any) -> Any:
    return item.get("expected_output") if isinstance(item, Mapping) else getattr(item, "expected_output", None)


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def summarize(item_results: list[Any]) -> dict[str, Any]:
    """Aggregates over a run's item results (pure; also used for the console table)."""
    outputs = [r.output if isinstance(r.output, Mapping) else {} for r in item_results]
    correct = [1.0 if is_correct(_item_expected(r.item), r.output) else 0.0 for r in item_results]

    def col(name: str) -> list[float]:
        return [float(o[name]) for o in outputs if _is_number(o.get(name))]

    return {
        "items": len(item_results),
        "errors": sum(1 for o in outputs if o.get("error")) + sum(1 for r in item_results if not isinstance(r.output, Mapping)),
        "accuracy": _mean(correct),
        "mean_db_time_share": _mean(col("db_time_share")),
        "mean_wall_ms": _mean(col("wall_ms")),
        "mean_run_cost_usd": _mean(col("run_cost_usd")),
        "mean_llm_calls": _mean(col("llm_calls")),
        "mean_scenarios_evaluated": _mean(col("scenarios_evaluated")),
    }


def run_summary(*, item_results: list[Any], **kwargs: Any) -> list[Evaluation]:
    """Run-level scores: accuracy (mean answer_correct), mean db_time_share, wall, cost."""
    s = summarize(item_results)
    evals = []
    if s["accuracy"] is not None:
        n_ok = round(s["accuracy"] * s["items"])
        evals.append(Evaluation(name="accuracy", value=round(s["accuracy"], 4), data_type="NUMERIC",
                                comment=f"{n_ok}/{s['items']} correct, {s['errors']} errored"))
    for name in ("mean_db_time_share", "mean_wall_ms", "mean_run_cost_usd"):
        if s[name] is not None:
            evals.append(Evaluation(name=name, value=round(s[name], 4), data_type="NUMERIC"))
    return evals


RUN_EVALUATORS = [run_summary]


# ----------------------------------------------------------------------- the question set
def load_question_specs(path: Path | str = QUESTIONS_PATH) -> list[dict[str, Any]]:
    """Raw entries of ``data/questions.yaml`` (id, difficulty, text, answer_keys, reference_sql, notes)."""
    with open(path) as f:
        specs = yaml.safe_load(f)
    if not isinstance(specs, list):
        raise ValueError(f"{path}: expected a list of questions")
    return specs


def load_questions(path: Path | str = QUESTIONS_PATH) -> list[Question]:
    return [
        Question(id=s["id"], text=" ".join(str(s["text"]).split()), answer_keys=list(s["answer_keys"]),
                 reference_sql=s["reference_sql"], notes=str(s.get("notes", "")).strip())
        for s in load_question_specs(path)
    ]


# A reference query builds a CTE named `ranked` (answer keys + `metric` + `support`, one
# row per candidate) and ends with `SELECT <answer keys> FROM ranked ORDER BY ... LIMIT 1`.
# The same SQL with the final SELECT widened gives the runner-up, for margin checks.
_FINAL_SELECT = re.compile(
    r"(?is)^SELECT\s+(?P<keys>[\w\s,]+?)\s+FROM\s+ranked\s+ORDER\s+BY\s+(?P<order>.+?)\s+LIMIT\s+1\s*;?\s*$")


def split_reference_sql(sql: str) -> tuple[str, list[str], str]:
    """Return (everything before the final SELECT, its selected keys, its ORDER BY)."""
    s = sql.strip()
    starts = [m.start() for m in re.finditer(r"(?i)\bSELECT\b", s)]
    if not starts:
        raise ValueError("reference_sql has no SELECT")
    m = _FINAL_SELECT.match(s[starts[-1]:])
    if not m:
        raise ValueError("reference_sql must end with `SELECT <keys> FROM ranked ORDER BY ... LIMIT 1`")
    keys = [k.strip() for k in m.group("keys").split(",")]
    return s[:starts[-1]], keys, m.group("order").strip()


def ranking_sql(reference_sql: str, *, top: int = 3) -> str:
    """The reference query's top ``top`` candidates with their metric and support."""
    head, _keys, order = split_reference_sql(reference_sql)
    return f"{head}SELECT * FROM ranked ORDER BY {order} LIMIT {int(top)}"


def relative_margin(best: float, runner_up: float) -> float:
    """How clearly the winner wins: |best - runner_up| / max(|best|, |runner_up|)."""
    denom = max(abs(best), abs(runner_up))
    return abs(best - runner_up) / denom if denom else 0.0

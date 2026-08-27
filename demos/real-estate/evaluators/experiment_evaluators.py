"""
Adapters that expose the scoring functions as Langfuse experiment evaluators.

`dataset.run_experiment(...)` calls each evaluator with
`(*, input, output, expected_output, metadata, **kwargs)` and expects a
Langfuse `Evaluation` back. The code evaluators additionally merge the dataset
item's ground-truth constraints (expected_output["expected"]) so budget /
location / language are checked against the dataset, not just the agent's own
parsing.
"""

from typing import Any, Dict, Optional

from langfuse import Evaluation

from agent import scoring


def _score_to_eval(s: scoring.Score) -> Evaluation:
    return Evaluation(name=s.name, value=s.value, data_type=s.data_type, comment=s.comment)


def _as_result(output: Any) -> Dict[str, Any]:
    return dict(output) if isinstance(output, dict) else {"answer": str(output), "constraints": {}}


def _with_expected(output: Any, expected_output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Overlay the dataset's ground-truth constraints onto the agent output."""
    result = _as_result(output)
    exp = (expected_output or {}).get("expected") if isinstance(expected_output, dict) else None
    if exp:
        merged = dict(result.get("constraints") or {})
        for k, v in exp.items():
            if v is not None:
                merged[k] = v
        result["constraints"] = merged
    return result


def _code_evaluator(fn):
    def evaluator(*, input=None, output=None, expected_output=None, metadata=None, **kwargs):
        return _score_to_eval(fn(_with_expected(output, expected_output)))
    evaluator.__name__ = fn.__name__
    return evaluator


def _llm_evaluator(fn):
    def evaluator(*, input=None, output=None, expected_output=None, metadata=None, **kwargs):
        return _score_to_eval(fn(_as_result(output)))
    evaluator.__name__ = fn.__name__
    return evaluator


# Item-level evaluators (one Evaluation each) --------------------------------
CODE_EVALUATORS = [_code_evaluator(fn) for fn in scoring.CODE_EVALUATORS]
LLM_EVALUATORS = [_llm_evaluator(fn) for fn in scoring.LLM_JUDGES]
ALL_EVALUATORS = CODE_EVALUATORS + LLM_EVALUATORS


# Run-level aggregate evaluators ---------------------------------------------
def _percent_comment(score_name: str, avg: float, n: int) -> str:
    return f"mean {score_name}: {avg:.1%} over {n} items"


def _count_comment(score_name: str, avg: float, n: int) -> str:
    return f"mean {score_name}: {avg:.2f} over {n} items"


def _mean_evaluator(score_name: str, comment_fmt=_percent_comment):
    """Mean of one score name across a run, for the Runs-tab comparison.

    `comment_fmt` exists because the default percentage rendering is right for a
    pass rate and nonsense for a count — a mean of 3.8 turns would read
    "380.0%". Pass `_count_comment` for scores whose unit is not a fraction.
    """
    def evaluator(*, item_results, **kwargs):
        vals = []
        for r in item_results:
            for ev in getattr(r, "evaluations", []) or []:
                if ev.name == score_name:
                    v = ev.value
                    if isinstance(v, bool):
                        vals.append(1.0 if v else 0.0)
                    elif isinstance(v, (int, float)):
                        vals.append(float(v))
        if not vals:
            return Evaluation(name=f"avg-{score_name}", value=None, comment="no values")
        avg = sum(vals) / len(vals)
        return Evaluation(name=f"avg-{score_name}", value=round(avg, 3),
                          comment=comment_fmt(score_name, avg, len(vals)))
    evaluator.__name__ = f"avg_{score_name}".replace("-", "_")
    return evaluator


RUN_EVALUATORS = [_mean_evaluator(n) for n in [
    "used-search-tool", "grounded-listings", "budget-adherence", "location-match",
    "language-match", "helpfulness", "relevance", "groundedness",
]]

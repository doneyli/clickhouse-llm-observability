"""
Adapters that expose the cross-turn scorers as Langfuse experiment evaluators.

Same contract as `evaluators/experiment_evaluators.py`:
`dataset.run_experiment(...)` calls each evaluator with
`(*, input, output, expected_output, metadata, **kwargs)` and expects a Langfuse
`Evaluation` back. The overlay semantics are reused from that module rather than
re-implemented, so the two experiments agree on what "ground truth" means.

The one extra thing an N+1 item needs is the conversation itself: `run_turn`
returns only the turn it just ran and never echoes the history it was handed, so
`item.input["history"]` is the only place turns 1..N exist at scoring time. That
is what `_with_conversation` adds.
"""

from typing import Any, Dict, Optional

from agent import conversation_scoring
from evaluators.experiment_evaluators import (
    CODE_EVALUATORS,
    _mean_evaluator,
    _score_to_eval,
    _with_expected,
)


def _with_conversation(input: Any, output: Any,
                       expected_output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """`_with_expected` (ground-truth constraints) plus the replayed history.

    For an N+1 item the merged constraints are the ACCUMULATED ones — the dataset
    states everything still in force at turn N+1 — which is precisely what
    `code_stated_constraint_respected` needs, and it also supplies
    `referenced_listing` for `code_reference_resolved`.
    """
    result = _with_expected(output, expected_output)
    if isinstance(input, dict) and input.get("history"):
        result["history"] = input["history"]
    return result


def _conversation_evaluator(fn):
    """Adapt a scorer, honouring a `None` return as NOT APPLICABLE.

    A scorer that returns None emits no Evaluation for that item, so the item is
    absent from the run-level mean rather than counted as a pass. Without this,
    `reference-resolved` averaged 6 non-applicable items in as 1.0 and the metric
    could not detect a regression on them. `run_experiment` tolerates an
    evaluator returning None (no score is recorded).
    """
    def evaluator(*, input=None, output=None, expected_output=None, metadata=None, **kwargs):
        score = fn(_with_conversation(input, output, expected_output))
        return _score_to_eval(score) if score is not None else None
    evaluator.__name__ = fn.__name__
    return evaluator


# Item-level evaluators (one Evaluation each) --------------------------------
CONVERSATION_CODE_EVALUATORS = [
    _conversation_evaluator(fn) for fn in conversation_scoring.CONVERSATION_CODE_EVALUATORS
]
CONVERSATION_LLM_EVALUATORS = [
    _conversation_evaluator(fn) for fn in conversation_scoring.CONVERSATION_LLM_JUDGES
]

# The full N+1 board: the cross-turn scorers PLUS the single-turn code evaluators.
# Budget, location, grounding, language and tool-use are all still live questions
# at turn N+1 — an agent that forgets a constraint fails `budget-adherence` too,
# and seeing both move together is what makes the diagnosis obvious. The
# single-turn *LLM* judges (helpfulness/relevance/groundedness/tone) are
# deliberately left out: they score the answer with no view of the conversation,
# which is the blind spot this dataset exists to cover, and they would add four
# judge calls per item on top of `context-retention`.
N_PLUS_1_EVALUATORS = (
    CONVERSATION_CODE_EVALUATORS + CONVERSATION_LLM_EVALUATORS + CODE_EVALUATORS
)


# Run-level aggregate evaluators ---------------------------------------------
# Means over the run, so a Runs-tab comparison (model vs model, prompt vs prompt)
# reads at a glance. Names must match the item-level score names exactly.
N_PLUS_1_RUN_EVALUATORS = [_mean_evaluator(n) for n in [
    "stated-constraint-respected", "reference-resolved", "context-retention",
    "used-search-tool", "grounded-listings", "budget-adherence", "location-match",
    "language-match",
]]

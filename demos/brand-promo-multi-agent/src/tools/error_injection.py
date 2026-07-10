"""Probabilistic error injection for demo failure-mode scenarios."""

from __future__ import annotations

import os
import random
from enum import StrEnum
from functools import lru_cache

from src.config import load_config


class InjectedFault(StrEnum):
    SALES_API_TIMEOUT = "sales_api_timeout"
    HALLUCINATED_SKU = "hallucinated_sku"
    COMPLIANCE_REJECTION = "compliance_rejection"
    CREW_MAX_ITERATIONS = "crew_max_iterations"
    TOOL_ERROR = "tool_error"


@lru_cache(maxsize=1)
def _get_rng() -> random.Random:
    """One seeded RNG per process so demo runs are reproducible."""
    import uuid

    try:
        from src.observability import _DEMO_RUN_ID

        seed = int(uuid.UUID(_DEMO_RUN_ID).int % (2**32))
    except Exception:
        seed = 12345
    return random.Random(seed)


def maybe_inject(tool_name: str) -> InjectedFault | None:
    """Roll the dice for a fault injection. Returns None most of the time.

    Disabled entirely when PROMO_DISABLE_FAULT_INJECTION is set. Fault injection
    exists for synthetic-history / live-demo realism; during a golden-dataset
    experiment it only adds noise and scores items against labels they can't
    match (e.g. a TOOL_ERROR -> compliance status "ERROR", which is never an
    expected label), so run_experiment.py sets this to keep eval/CI scoring clean.
    """
    if os.getenv("PROMO_DISABLE_FAULT_INJECTION"):
        return None
    cfg = load_config()
    dist = cfg.synthetic_history.failure_mode_distribution
    rng = _get_rng()

    roll = rng.random()
    cumulative = 0.0
    for fault_name, probability in dist.items():
        cumulative += probability
        if roll < cumulative:
            try:
                return InjectedFault(fault_name)
            except ValueError:
                return None
    return None

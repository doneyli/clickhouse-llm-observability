"""
Loop backstops — the control layer, NOT the termination mechanism.

Per the pattern guide, an unattended loop must never omit a turn cap and a spend
cap. This module provides three independent backstops plus a kill switch:

  * turn counter        -> error_max_turns
  * cumulative $ cost    -> error_max_budget_usd   (from real Anthropic usage)
  * wall-clock watchdog  -> error_watchdog
  * kill sentinel/SIGINT -> killed                 (checkpoint + resumable)

`check()` runs BEFORE every turn. A run that ends on any of these is recorded as
a FAILURE MODE, not normal termination — normal termination is the agent calling
`finish` and the controller verifying the claim.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import checkpoint

# Published USD-per-token prices (input, output). Cost is computed from REAL
# Anthropic usage so the spend cap and the Langfuse cost Monitor agree. Native
# cost tracking on the generation observations uses Langfuse's own price list;
# this table is the app-side cap's ground truth. Update alongside pricing.
_PRICES: Dict[str, Tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0e-6, 15.0e-6),
    "claude-sonnet-4-5": (3.0e-6, 15.0e-6),
    "claude-haiku-4-5": (1.0e-6, 5.0e-6),
    "claude-opus-4-1": (15.0e-6, 75.0e-6),
}
_DEFAULT_PRICE = (3.0e-6, 15.0e-6)  # assume Sonnet-class if unknown


@dataclass
class Caps:
    """Backstop limits. Env-tunable, never removable."""

    max_turns: int = int(os.getenv("TUNER_MAX_TURNS", "15"))
    max_budget_usd: float = float(os.getenv("TUNER_MAX_BUDGET_USD", "0.75"))
    watchdog_s: float = float(os.getenv("TUNER_WATCHDOG_S", "600"))

    def as_dict(self) -> Dict[str, float]:
        return {"max_turns": self.max_turns, "max_budget_usd": self.max_budget_usd,
                "watchdog_s": self.watchdog_s}


def cost_of(usage, model: str) -> float:
    """USD cost of one Anthropic response from its usage object/dict."""
    in_rate, out_rate = _PRICES.get(model, _DEFAULT_PRICE)
    it = _get(usage, "input_tokens")
    ot = _get(usage, "output_tokens")
    # Cache tokens, when present, are billed near input rate; approximate.
    it += _get(usage, "cache_creation_input_tokens") + _get(usage, "cache_read_input_tokens")
    return it * in_rate + ot * out_rate


def check(state, caps: Caps) -> Optional[str]:
    """Return a termination_reason if a backstop is tripped, else None.

    Order matters: kill switch first (respected even at turn 0), then the caps.
    """
    if state.sigint or os.path.exists(checkpoint.kill_path(state.run_id)):
        return "killed"
    if state.turn > caps.max_turns:
        return "error_max_turns"
    if state.cost_usd >= caps.max_budget_usd:
        return "error_max_budget_usd"
    if (time.monotonic() - state.t0) >= caps.watchdog_s:
        return "error_watchdog"
    return None


def _get(usage, attr: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        v = usage.get(attr)
    else:
        v = getattr(usage, attr, None)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0

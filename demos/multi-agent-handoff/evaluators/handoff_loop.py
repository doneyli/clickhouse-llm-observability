"""
Langfuse code evaluator `handoff-loop` (Python, standard library only).

Runs inside Langfuse on the root of every customer turn (`support-turn`, rule:
metadata.role = orchestrator, in production). An online evaluator sees only the
observation it matched, so the orchestrator writes the turn's trajectory onto the root:
`agent_path` ("concierge>tech-support>billing"), `handoff_count`, `hop_cap_hit`. Scores:

  handoff-loop   BOOLEAN  the turn ping-ponged (A>B>A), repeated a hop, or hit the hop cap
  handoff-count  NUMERIC  handoffs in the turn

Fault F3 (specialists bouncing an ambiguous request) is what this catches: every single
hop looks reasonable on its own; only the path shows the loop.
"""

import json
from dataclasses import dataclass, field
from typing import Any

try:  # the Langfuse runtime may provide these; define them for local runs and tests
    Score  # noqa: B018
    EvaluationResult  # noqa: B018
except NameError:
    @dataclass
    class Score:
        name: str
        value: Any
        data_type: str
        comment: Any = None
        config_id: Any = None
        metadata: Any = None

    @dataclass
    class EvaluationResult:
        scores: list = field(default_factory=list)

MAX_HANDOFFS = 4


def _decode(value):
    """The Langfuse Python SDK v5 JSON-encodes metadata values on spans; accept both forms."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def evaluate(ctx):
    md = _decode(ctx.observation.metadata) or {}
    md = {k: _decode(v) for k, v in md.items()} if isinstance(md, dict) else {}
    path = [p for p in str(md.get("agent_path") or "").split(">") if p]
    try:
        count = int(md.get("handoff_count") or 0)
    except (TypeError, ValueError):
        count = 0
    cap_hit = str(md.get("hop_cap_hit")).lower() == "true"
    pairs = list(zip(path, path[1:]))
    repeated = sorted({f"{a}>{b}" for a, b in pairs if a != b and pairs.count((a, b)) > 1})
    bounces = sorted({f"{a}>{b}>{a}" for a, b, c in zip(path, path[1:], path[2:]) if a == c and a != b})
    why = []
    if cap_hit or count > MAX_HANDOFFS:
        why.append(f"{count} handoffs (cap {MAX_HANDOFFS})")
    if bounces:
        why.append("ping-pong " + ", ".join(bounces))
    if repeated:
        why.append("repeated hop " + ", ".join(repeated))
    return EvaluationResult(scores=[
        Score(name="handoff-loop", value=bool(why), data_type="BOOLEAN",
              comment="; ".join(why) if why else f"no loop: {'>'.join(path) or 'no path'}"),
        Score(name="handoff-count", value=count, data_type="NUMERIC", comment=">".join(path)),
    ])

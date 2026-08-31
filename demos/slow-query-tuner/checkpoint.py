"""
Durable per-run state + the pause/resume contract.

`LoopState` is the working memory of one tuning run. It is JSON-serialisable
(messages are stored as plain content-block dicts, never SDK objects) and is
written to `checkpoints/<run_id>.json` after EVERY turn, so:

  * a SIGINT / kill-sentinel / crash mid-loop loses at most the in-flight turn;
  * `--resume <run_id>` restores it in a FRESH process and — crucially — reuses
    the stored `session_id`, so Langfuse stitches the paused and resumed traces
    into one continuous investigation in the Sessions view.

LoopState lives here (not in agent_loop) so both `budget` and `agent_loop` can
depend on it without an import cycle.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def checkpoint_dir() -> str:
    """Directory for run state + kill sentinels. Volume-mounted in the container
    (survives `run --rm`). Overridable via TUNER_CHECKPOINT_DIR (tests use a tmp
    dir)."""
    d = os.getenv("TUNER_CHECKPOINT_DIR", "checkpoints")
    os.makedirs(d, exist_ok=True)
    return d


def state_path(run_id: str) -> str:
    return os.path.join(checkpoint_dir(), f"{run_id}.json")


def kill_path(run_id: str) -> str:
    """`touch checkpoints/<run_id>.kill` is the external kill switch."""
    return os.path.join(checkpoint_dir(), f"{run_id}.kill")


def exists(run_id: str) -> bool:
    return os.path.exists(state_path(run_id))


@dataclass
class LoopState:
    run_id: str
    session_id: str
    goal: Dict[str, Any]
    goal_prompt: str                                    # initial user-message text
    messages: List[Dict[str, Any]] = field(default_factory=list)   # raw turn pairs
    summaries: List[str] = field(default_factory=list)             # one line per pair
    turn: int = 1
    cost_usd: float = 0.0
    best_speedup: float = 1.0
    best_sql: Optional[str] = None
    plateau: int = 0
    baseline_ms: Optional[float] = None
    baseline_signature: Optional[str] = None
    # Runtime-only (never serialised): wall-clock start + SIGINT flag.
    t0: float = field(default_factory=time.monotonic, compare=False)
    sigint: bool = field(default=False, compare=False)

    # -- construction --------------------------------------------------------
    @classmethod
    def fresh(cls, *, run_id: str, session_id: str, goal: Dict[str, Any],
              goal_prompt: str) -> "LoopState":
        return cls(run_id=run_id, session_id=session_id, goal=goal,
                   goal_prompt=goal_prompt)

    # -- message assembly ----------------------------------------------------
    def goal_user_message(self, extra: str = "") -> Dict[str, Any]:
        content = self.goal_prompt + (("\n\n" + extra) if extra else "")
        return {"role": "user", "content": content}

    def append_pair(self, assistant_msg: Dict[str, Any], tool_use_id: str,
                    tool_result: Any, summary: str) -> None:
        """Append one (assistant tool_use, user tool_result) pair + its summary.
        Keeps tool_use/tool_result pairing intact so compaction can slice whole
        pairs safely."""
        self.messages.append(assistant_msg)
        content = tool_result if isinstance(tool_result, str) else json.dumps(tool_result, default=str)
        self.messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        })
        self.summaries.append(summary)

    def compacted_messages(self, compact_after: int) -> List[Dict[str, Any]]:
        """Context-rot mitigation: fold turns older than `compact_after` into
        one-line summaries carried on the goal message; keep the most recent
        pairs verbatim. Visible in the `plan-next-action` input during a demo."""
        pairs = len(self.messages) // 2
        if compact_after <= 0 or pairs <= compact_after:
            return [self.goal_user_message()] + list(self.messages)
        compacted = pairs - compact_after
        summary_lines = "\n".join(f"- {s}" for s in self.summaries[:compacted])
        head = self.goal_user_message(
            "Progress so far (compacted; every timing was measured against the live DB):\n"
            + summary_lines
        )
        recent = self.messages[compacted * 2:]
        return [head] + list(recent)

    # -- progress bookkeeping -----------------------------------------------
    def record_progress(self, obs, candidate_sql: Optional[str] = None) -> float:
        """Update best_speedup + plateau counter from an observation; return the
        per-turn improvement_delta (0 for non-improving / info-gathering turns)."""
        is_candidate = (getattr(obs, "ok", False) and getattr(obs, "kind", None) == "query"
                        and getattr(obs, "candidate_checked", False))
        if not is_candidate or not self.baseline_ms:
            return 0.0
        if obs.equivalent and obs.elapsed_ms:
            speedup = self.baseline_ms / max(obs.elapsed_ms, 0.001)
            if speedup > self.best_speedup + 1e-9:
                delta = speedup - self.best_speedup
                self.best_speedup = speedup
                self.best_sql = candidate_sql
                self.plateau = 0
                return round(delta, 4)
        # Ran a candidate but it did not improve (slower/equal or non-equivalent).
        self.plateau += 1
        return 0.0

    # -- serialisation -------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "goal_prompt": self.goal_prompt,
            "messages": self.messages,
            "summaries": self.summaries,
            "turn": self.turn,
            "cost_usd": self.cost_usd,
            "best_speedup": self.best_speedup,
            "best_sql": self.best_sql,
            "plateau": self.plateau,
            "baseline_ms": self.baseline_ms,
            "baseline_signature": self.baseline_signature,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LoopState":
        return cls(
            run_id=d["run_id"], session_id=d["session_id"], goal=d["goal"],
            goal_prompt=d["goal_prompt"], messages=d.get("messages", []),
            summaries=d.get("summaries", []), turn=d.get("turn", 1),
            cost_usd=d.get("cost_usd", 0.0), best_speedup=d.get("best_speedup", 1.0),
            best_sql=d.get("best_sql"), plateau=d.get("plateau", 0),
            baseline_ms=d.get("baseline_ms"), baseline_signature=d.get("baseline_signature"),
        )


def save(run_id: str, state: LoopState) -> None:
    """Atomic write (temp + rename) so a crash mid-write never corrupts state."""
    path = state_path(run_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def load(run_id: str) -> LoopState:
    with open(state_path(run_id), "r", encoding="utf-8") as f:
        return LoopState.from_dict(json.load(f))

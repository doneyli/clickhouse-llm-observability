"""
Tool schemas + dispatcher for the tuning agent.

Six tools. Five act on the environment (`run_query`, `explain_query`,
`get_schema`, `check_equivalence`, `propose_ddl`); `finish` is the TERMINATION
CONTRACT — calling it is the agent's self-assessed "I'm done / I give up", and
its claim is independently re-executed by the controller before being accepted.

Each executed tool is one `tool`-typed Langfuse observation. The controller (in
agent_loop.py) owns the finish + propose_ddl paths (verification + HITL gate);
`dispatch()` here handles the read-only environment tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import ch_env

# Stable, low-cardinality span names (turn number lives in metadata, not names —
# repo convention, and what lets the Aggregated Agent Graph collapse the loop).
SPAN_NAMES: Dict[str, str] = {
    "run_query": "run-query",
    "explain_query": "explain-query",
    "get_schema": "get-schema",
    "check_equivalence": "check-equivalence",
    "propose_ddl": "propose-ddl",
    "finish": "finish",
}

RUN_QUERY = {
    "name": "run_query",
    "description": ("Execute a candidate SELECT against the live tuning lab and read back the "
                    "MEASURED elapsed time, rows/bytes read, a 5-row preview and a result "
                    "signature. This is your ground truth — you cannot know a rewrite is faster "
                    "until you run it. Each candidate is automatically checked for result-set "
                    "equivalence against the original query."),
    "input_schema": {"type": "object", "properties": {
        "sql": {"type": "string", "description": "A single read-only SELECT/WITH statement."},
        "settings": {"type": "object", "description": "Optional per-query ClickHouse settings."},
    }, "required": ["sql"]},
}

EXPLAIN_QUERY = {
    "name": "explain_query",
    "description": "Get the EXPLAIN plan (with index usage) for a query to see WHY it is slow.",
    "input_schema": {"type": "object", "properties": {
        "sql": {"type": "string", "description": "A SELECT/WITH statement (or a full EXPLAIN ...)."},
    }, "required": ["sql"]},
}

GET_SCHEMA = {
    "name": "get_schema",
    "description": "Show the CREATE TABLE DDL so you can see column types, engine and sort key.",
    "input_schema": {"type": "object", "properties": {
        "table": {"type": "string", "description": "Table name (default web_events)."},
    }, "required": []},
}

CHECK_EQUIVALENCE = {
    "name": "check_equivalence",
    "description": ("Prove a candidate returns the SAME rows as the original query. The database "
                    "arbitrates via an order-independent signature — a rewrite that got fast by "
                    "changing what it returns will fail here."),
    "input_schema": {"type": "object", "properties": {
        "candidate_sql": {"type": "string"},
    }, "required": ["candidate_sql"]},
}

PROPOSE_DDL = {
    "name": "propose_ddl",
    "description": ("Propose a schema change (e.g. ADD PROJECTION / ORDER BY) when NO query rewrite "
                    "can meet the target. This PAUSES for human approval; you cannot run DDL "
                    "yourself. Include a clear rationale — a human decides."),
    "input_schema": {"type": "object", "properties": {
        "ddl": {"type": "string", "description": "The exact DDL statement(s) to apply."},
        "rationale": {"type": "string", "description": "Why a rewrite alone cannot meet the target."},
    }, "required": ["ddl", "rationale"]},
}

FINISH = {
    "name": "finish",
    "description": ("Call when you are DONE: either the target is verifiably met, or you have "
                    "concluded it cannot be met with query rewrites alone (explain the structural "
                    "blocker). Your claim will be independently re-executed and REJECTED if it "
                    "does not verify — never claim a speedup you have not measured."),
    "input_schema": {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["success", "gave_up"]},
        "final_sql": {"type": "string", "description": "The query you are submitting as final."},
        "claimed_speedup": {"type": "number", "description": "Your measured speedup vs the original."},
        "summary": {"type": "string", "description": "What you changed and why (or the blocker)."},
    }, "required": ["status", "final_sql", "summary"]},
}

# Full tool set, and the subset available in the naive/runaway configuration
# (no schema-change escape hatch — the runaway fuel).
SCHEMAS: List[Dict[str, Any]] = [
    RUN_QUERY, EXPLAIN_QUERY, GET_SCHEMA, CHECK_EQUIVALENCE, PROPOSE_DDL, FINISH,
]
RUNAWAY_SCHEMAS: List[Dict[str, Any]] = [
    RUN_QUERY, EXPLAIN_QUERY, GET_SCHEMA, CHECK_EQUIVALENCE, FINISH,
]
NAMES: List[str] = [t["name"] for t in SCHEMAS]


@dataclass
class Action:
    name: str
    args: Dict[str, Any]
    tool_use_id: str

    @property
    def span_name(self) -> str:
        return SPAN_NAMES.get(self.name, self.name)


def extract_tool_call(resp) -> Optional[Action]:
    """Return the first tool_use block as an Action, or None (end_turn)."""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            return Action(name=block.name, args=dict(block.input or {}), tool_use_id=block.id)
    return None


def to_plain_assistant(resp) -> Dict[str, Any]:
    """Convert an Anthropic response into a plain-dict assistant message so it is
    JSON-serialisable (checkpointing) and re-sendable to the API on resume."""
    content: List[Dict[str, Any]] = []
    for block in getattr(resp, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text" and (block.text or "").strip():
            content.append({"type": "text", "text": block.text})
        elif btype == "tool_use":
            content.append({"type": "tool_use", "id": block.id, "name": block.name,
                            "input": dict(block.input or {})})
    if not content:
        content = [{"type": "text", "text": "(no content)"}]
    return {"role": "assistant", "content": content}


def dispatch(action: Action, env: "ch_env.TuningLabEnv", baseline_sql: str,
             baseline_signature: Optional[str]) -> "ch_env.Obs":
    """Execute a read-only environment tool. finish/propose_ddl are handled by
    the controller (verification / HITL) and never reach here."""
    if action.name == "run_query":
        sql = action.args.get("sql", "")
        obs = env.run_query(sql, settings=action.args.get("settings"))
        # Auto-verify candidate equivalence against the original query's signature
        # so every candidate execution carries a semantics_preserved verdict.
        if obs.ok and baseline_signature is not None:
            obs.candidate_checked = True
            obs.equivalent = (obs.signature == baseline_signature)
            obs.extra["candidate_sql"] = sql
        return obs
    if action.name == "explain_query":
        return env.explain_query(action.args.get("sql", ""))
    if action.name == "get_schema":
        return env.get_schema(action.args.get("table", "web_events"))
    if action.name == "check_equivalence":
        return env.check_equivalence(baseline_sql, action.args.get("candidate_sql", ""))
    return ch_env.Obs.error_obs(f"unknown tool '{action.name}'", kind="query")

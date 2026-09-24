"""
Environment interface — the live ClickHouse tuning lab the agent acts on.

Two connections:
  * tuner_agent (read-only, quota'd) — every query the AGENT runs. This is the
    ground truth: measured elapsed_ms / read_rows / read_bytes on every
    execution, so the agent CANNOT hallucinate a speedup.
  * tuner_admin — opened ONLY inside the human-approved propose_ddl path.

Defense-in-depth: an app-side SQL-shape allow-list (SELECT/WITH/EXPLAIN/SHOW,
single statement) sits in front of the grants. The grants are the real security
boundary; the allow-list is a belt to the grants' suspenders and gives clean,
demoable error-as-observation behaviour.

clickhouse_connect is imported lazily so the module (and the unit tests, which
mock the env) import with no driver and no live database.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Belt: allow only read-only single statements. Grants are the suspenders.
_ALLOWED = re.compile(r"^\s*(SELECT|WITH|EXPLAIN|SHOW)\b", re.IGNORECASE)

# Per-query safety settings the agent's connection always carries. readonly=2
# lets the agent SET additional per-query settings but never write.
SAFE_SETTINGS: Dict[str, Any] = {
    "max_execution_time": int(os.getenv("TUNING_MAX_EXECUTION_TIME", "30")),
    "max_result_rows": int(os.getenv("TUNING_MAX_RESULT_ROWS", "10000")),
    "max_result_bytes": int(os.getenv("TUNING_MAX_RESULT_BYTES", "50000000")),
}


def sql_allowed(sql: str) -> "tuple[bool, Optional[str]]":
    """Return (ok, reason). Rejects writes/DDL and multi-statement input.

    Pure-python — no driver, no DB. Unit-tested directly.
    """
    if not sql or not sql.strip():
        return False, "empty statement"
    if not _ALLOWED.match(sql):
        return False, "blocked by SQL-shape allow-list (SELECT/WITH/EXPLAIN/SHOW only)"
    # Reject multi-statement input: a trailing ';' is fine, an interior one is not.
    if ";" in sql.strip().rstrip(";"):
        return False, "blocked by SQL-shape allow-list (single statement only)"
    return True, None


def result_signature(rows: List[Any]) -> str:
    """Order-independent semantic signature of a result set.

    The DB arbitrates equivalence, not a string match: two syntactically
    different queries returning the same rows (in any order) hash identically.
    """
    try:
        normalized = sorted(tuple(r) for r in rows)
    except TypeError:
        # Unsortable/heterogeneous rows: fall back to a stable repr per row.
        normalized = sorted(repr(tuple(r)) for r in rows)
    return hashlib.sha256(repr(normalized).encode()).hexdigest()[:16]


@dataclass
class Obs:
    """One environment observation. `public` is the JSON-safe span/tool output."""

    ok: bool
    kind: str                       # query | explain | schema | equivalence | ddl
    error: Optional[str] = None
    elapsed_ms: Optional[float] = None
    read_rows: Optional[int] = None
    read_bytes: Optional[int] = None
    rows_preview: List[Any] = field(default_factory=list)
    signature: Optional[str] = None
    text: Optional[str] = None      # EXPLAIN plan / schema DDL
    # Controller-side equivalence bookkeeping (set by the caller for candidates).
    candidate_checked: bool = False
    equivalent: Optional[bool] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_error(self) -> bool:
        return not self.ok

    @classmethod
    def error_obs(cls, msg: str, kind: str = "query") -> "Obs":
        return cls(ok=False, kind=kind, error=msg)

    @property
    def public(self) -> Dict[str, Any]:
        """Truncated, JSON-safe view for the trace + the model's next observation."""
        if not self.ok:
            return {"error": self.error, "kind": self.kind}
        out: Dict[str, Any] = {"kind": self.kind}
        if self.elapsed_ms is not None:
            out["elapsed_ms"] = round(self.elapsed_ms, 1)
        if self.read_rows is not None:
            out["read_rows"] = self.read_rows
        if self.read_bytes is not None:
            out["read_bytes"] = self.read_bytes
        if self.signature is not None:
            out["result_signature"] = self.signature
        if self.rows_preview:
            out["rows_preview"] = [list(r) for r in self.rows_preview[:5]]
        if self.text is not None:
            out["text"] = self.text[:2000]
        if self.candidate_checked:
            out["equivalent_to_baseline"] = self.equivalent
        out.update(self.extra)
        return out


class TuningLabEnv:
    """Live ClickHouse tuning lab, sandboxed as tuner_agent (+ admin gate)."""

    def __init__(self) -> None:
        self.host = os.getenv("TUNING_CH_HOST", "clickhouse-tuning")
        self.port = int(os.getenv("TUNING_CH_PORT", "8123"))
        self.db = os.getenv("TUNING_CH_DB", "tuning_lab")
        self.agent_user = os.getenv("TUNING_CH_AGENT_USER", "tuner_agent")
        self.agent_password = os.getenv("TUNING_CH_AGENT_PASSWORD", "tuner_agent123")
        self.admin_user = os.getenv("TUNING_CH_ADMIN_USER", "tuner_admin")
        self.admin_password = os.getenv("TUNING_CH_ADMIN_PASSWORD", "tuner_admin123")
        self._agent = None
        self._admin = None

    # -- connections ---------------------------------------------------------
    def _client(self, user: str, password: str):
        import clickhouse_connect  # lazy: keeps module import driver-free
        return clickhouse_connect.get_client(
            host=self.host, port=self.port, database=self.db,
            username=user, password=password,
        )

    def _agent_client(self):
        if self._agent is None:
            self._agent = self._client(self.agent_user, self.agent_password)
        return self._agent

    def _admin_client(self):
        """Admin connection — opened ONLY from the approved propose_ddl path."""
        if self._admin is None:
            self._admin = self._client(self.admin_user, self.admin_password)
        return self._admin

    # -- read-only agent surface --------------------------------------------
    def run_query(self, sql: str, settings: Optional[Dict[str, Any]] = None) -> Obs:
        """Execute a candidate/baseline query and MEASURE it. Errors are returned
        as observations (never raised) so the environment can fail independently
        of the model and the error becomes the next turn's observation."""
        ok, reason = sql_allowed(sql)
        if not ok:
            return Obs.error_obs(reason or "blocked", kind="query")
        merged = {**SAFE_SETTINGS, **(settings or {})}
        t0 = time.perf_counter()
        try:
            res = self._agent_client().query(sql, settings=merged)
        except Exception as e:  # env fails independently of the model
            return Obs.error_obs(f"{type(e).__name__}: {str(e)[:400]}", kind="query")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        summary = getattr(res, "summary", {}) or {}
        rows = list(res.result_rows)
        return Obs(
            ok=True, kind="query",
            elapsed_ms=elapsed_ms,
            read_rows=_as_int(summary.get("read_rows")),
            read_bytes=_as_int(summary.get("read_bytes")),
            rows_preview=rows[:5],
            signature=result_signature(rows),
        )

    def explain_query(self, sql: str) -> Obs:
        stripped = sql.strip().rstrip(";")
        # Accept a bare SELECT and wrap it, or an already-EXPLAIN statement.
        explain_sql = stripped if _ALLOWED.match(stripped) and stripped.upper().startswith("EXPLAIN") \
            else f"EXPLAIN indexes = 1 {stripped}"
        ok, reason = sql_allowed(explain_sql)
        if not ok:
            return Obs.error_obs(reason or "blocked", kind="explain")
        try:
            res = self._agent_client().query(explain_sql)
        except Exception as e:
            return Obs.error_obs(f"{type(e).__name__}: {str(e)[:400]}", kind="explain")
        plan = "\n".join(str(r[0]) for r in res.result_rows)
        full_scan = "tuple()" in plan or "PrimaryKey" not in plan
        return Obs(ok=True, kind="explain", text=plan, extra={"full_scan_suspected": full_scan})

    def get_schema(self, table: str = "web_events") -> Obs:
        sql = f"SHOW CREATE TABLE {self.db}.{table}"
        ok, reason = sql_allowed(sql)
        if not ok:
            return Obs.error_obs(reason or "blocked", kind="schema")
        try:
            res = self._agent_client().query(sql)
        except Exception as e:
            return Obs.error_obs(f"{type(e).__name__}: {str(e)[:400]}", kind="schema")
        ddl = res.result_rows[0][0] if res.result_rows else ""
        return Obs(ok=True, kind="schema", text=str(ddl))

    def signature_of(self, sql: str) -> Obs:
        """Run a query purely to capture its result signature (for equivalence)."""
        return self.run_query(sql)

    def check_equivalence(self, baseline_sql: str, candidate_sql: str) -> Obs:
        """Deterministic result-set equivalence probe: run both, compare
        order-independent signatures. The DB arbitrates, not the model."""
        base = self.run_query(baseline_sql)
        if not base.ok:
            return Obs.error_obs(f"baseline failed: {base.error}", kind="equivalence")
        cand = self.run_query(candidate_sql)
        if not cand.ok:
            return Obs.error_obs(f"candidate failed: {cand.error}", kind="equivalence")
        equivalent = base.signature == cand.signature
        return Obs(
            ok=True, kind="equivalence",
            candidate_checked=True, equivalent=equivalent,
            signature=cand.signature,
            extra={"baseline_signature": base.signature,
                   "candidate_signature": cand.signature,
                   "equivalent": equivalent},
        )

    # -- admin / DDL gate ----------------------------------------------------
    def apply_ddl(self, ddl: str) -> Obs:
        """Execute a DDL statement via the ADMIN connection. Callers MUST only
        reach this after the human-approval gate has approved the action."""
        try:
            self._admin_client().command(ddl)
        except Exception as e:
            return Obs.error_obs(f"{type(e).__name__}: {str(e)[:400]}", kind="ddl")
        return Obs(ok=True, kind="ddl", text=f"applied: {ddl[:200]}")


def _as_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

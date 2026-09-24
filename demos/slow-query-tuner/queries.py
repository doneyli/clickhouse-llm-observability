"""
Bad-query catalog — engineered so specific queries reliably produce specific
demo moments (short self-terminating run / long run / blocked-or-runaway run).

Each query is a real, deliberately-pessimal SELECT against tuning_lab.web_events
(see sql/init/01_schema.sql). The agent gets ONLY the SQL + a target latency; it
must produce a semantically-equivalent query that meets the target, or explain
why it can't without schema changes.

`expected_turn_band` is consumed by `trajectory_efficiency` scoring and dataset
metadata. It is a soft expectation, never a hard assertion — the whole point of
the pattern is that the real turn count is not knowable in advance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Goal:
    """One tuning objective handed to the agent."""

    sql: str
    target_ms: int
    id: str = "adhoc"
    expected_turn_band: Tuple[int, int] = (2, 12)
    designed_outcome: str = ""

    def as_dict(self) -> Dict:
        return {
            "id": self.id,
            "sql": self.sql,
            "target_ms": self.target_ms,
            "expected_turn_band": list(self.expected_turn_band),
        }


# --- Q1 EASY -----------------------------------------------------------------
# Daily unique users for June. Sins: parses a String date in the WHERE (blocks
# any index use, forces per-row parse) and count(DISTINCT) (exact, expensive).
# Fix path: use event_date, swap count(DISTINCT) -> uniq(). 2-4 turns, clean
# self_completed — the short-trace money shot.
Q1_SQL = """\
SELECT toDate(parseDateTimeBestEffortOrNull(ts_raw)) AS day,
       count(DISTINCT user_id) AS uniques
FROM tuning_lab.web_events
WHERE toDate(parseDateTimeBestEffortOrNull(ts_raw)) BETWEEN '2024-06-01' AND '2024-06-30'
GROUP BY day
ORDER BY day
"""

# --- Q2 MEDIUM ---------------------------------------------------------------
# Top-10 URLs by average duration for US traffic in June. Sins: SELECT * in a
# subquery (materialises every column), lower(country)='us' (function-wrapped
# predicate), string-date parse, a global ORDER BY. Fix path is multi-step and
# order-independent: PREWHERE / country='US' / event_date rewrite / drop the
# subquery. 5-10 turns incl. an env error and a plateau — the long-trace and
# pause/resume vehicle.
Q2_SQL = """\
SELECT url, avg(duration_ms) AS avg_dur, count() AS n
FROM (
    SELECT *
    FROM tuning_lab.web_events
    WHERE lower(country) = 'us'
      AND toDate(parseDateTimeBestEffortOrNull(ts_raw)) BETWEEN '2024-06-01' AND '2024-06-30'
)
GROUP BY url
ORDER BY avg_dur DESC
LIMIT 10
"""

# --- Q3 BLOCKED / RUNAWAY-BAIT -----------------------------------------------
# High-selectivity needle: one user, one day. With ORDER BY tuple() this is a
# full 30M-row scan no rewrite can fix — it physically needs a sort key /
# projection on (event_date, user_id). Default mode -> agent exhausts rewrites
# and proposes DDL (HITL beat). --runaway mode with the naive prompt + an
# impossible 50ms target -> churns to error_max_budget_usd (Monitor beat).
Q3_SQL = """\
SELECT event_id, ts_raw, url, duration_ms
FROM tuning_lab.web_events
WHERE user_id = 12345
  AND event_date = '2024-06-15'
ORDER BY ts_raw
"""


CATALOG: Dict[str, Goal] = {
    "q1": Goal(
        sql=Q1_SQL,
        target_ms=800,
        id="q1",
        expected_turn_band=(2, 4),
        designed_outcome="short self_completed (event_date + uniq() rewrite)",
    ),
    "q2": Goal(
        sql=Q2_SQL,
        target_ms=800,
        id="q2",
        expected_turn_band=(5, 10),
        designed_outcome="long self_completed with one env error + one plateau",
    ),
    "q3": Goal(
        sql=Q3_SQL,
        target_ms=800,
        id="q3",
        expected_turn_band=(4, 12),
        designed_outcome="blocked -> propose_ddl (HITL) OR --runaway -> error_max_budget_usd",
    ),
}

# In --runaway mode Q3 is retargeted to a physically-unreachable latency so no
# rewrite ever verifies and the naive prompt never gives up — the deliberate
# runaway that trips the cost cap.
RUNAWAY_TARGET_MS = 50


def get_goal(query_id: str) -> Goal:
    if query_id not in CATALOG:
        raise KeyError(f"unknown query id '{query_id}'; choose from {sorted(CATALOG)}")
    return CATALOG[query_id]


# Concise schema hint injected into the goal prompt so the agent knows the table
# shape without a mandatory get_schema round-trip (it can still call it to see
# the exact DDL / confirm types).
SCHEMA_HINT = (
    "Table tuning_lab.web_events (MergeTree, ORDER BY tuple() — no sort key):\n"
    "  event_id String, ts_raw String (ISO-8601), event_date Date,\n"
    "  country String (~20 values), url String, user_id UInt64,\n"
    "  duration_ms UInt32, referrer String\n"
    "Note: event_date is a properly-typed copy of ts_raw's date "
    "(toDate(parseDateTimeBestEffortOrNull(ts_raw)) == event_date)."
)

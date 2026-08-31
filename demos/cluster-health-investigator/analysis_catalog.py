"""
Analysis catalog — the worker task menu for the Cluster Health Investigator.

This is the "bounded dynamism" guardrail of the orchestrator-workers pattern:
the planner LLM chooses WHICH of these vetted analyses to run and HOW MANY, but
it never authors SQL. Each entry is a fixed, read-only SELECT against ClickHouse
`system.*` tables with a hard-coded LIMIT. The planner's free-text `focus` is
carried only as a leading SQL comment (sanitised), never interpolated into the
query body — so there is no planner-authored-SQL execution path.

Two things are exported for the graph:
- CATALOG        : {analysis_type -> Analysis}   (render(focus) -> safe SQL)
- CATALOG_DIGEST : the one-line-per-analysis menu string fed to the planner
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Analysis:
    """One vetted system-table analysis the planner may delegate to a worker."""

    analysis_type: str
    system_tables: str          # human-readable list, shown in the digest
    description: str            # one line — the planner's menu entry
    sql_template: str           # fixed SQL; LIMIT hard-coded; no {focus} in body

    def render(self, focus: str = "") -> str:
        """Return the executable SQL, with the planner's focus as a safe comment.

        `focus` is the planner's natural-language instruction (e.g. "last 6h,
        insert queries only"). It is NEVER interpolated into the SQL structure —
        only emitted as a leading `-- focus:` comment after stripping anything
        that could break out of the comment line. The query body is immutable.
        """
        note = _sanitize_focus(focus)
        header = f"-- analysis: {self.analysis_type}\n"
        if note:
            header += f"-- focus: {note}\n"
        return header + self.sql_template.strip()


def _sanitize_focus(focus: str) -> str:
    """Collapse to a single safe comment line (no newlines, bounded length)."""
    if not focus:
        return ""
    one_line = re.sub(r"\s+", " ", str(focus)).strip()
    # A comment can't contain a newline; nothing here can reach the SQL body.
    return one_line[:160]


# --------------------------------------------------------------------------- #
# The 10 vetted analyses. All queries: single SELECT, system.* only, LIMIT set.
# Time windows are fixed in the template (the planner tunes emphasis via focus).
# --------------------------------------------------------------------------- #

CATALOG: dict[str, Analysis] = {
    "slow_queries": Analysis(
        analysis_type="slow_queries",
        system_tables="system.query_log",
        description="Top queries by duration and bytes read over the last 24h.",
        sql_template="""
            SELECT
                left(replaceRegexpAll(query, '\\\\s+', ' '), 120) AS query_preview,
                round(query_duration_ms / 1000, 2)                AS duration_s,
                formatReadableSize(read_bytes)                    AS read,
                formatReadableQuantity(read_rows)                 AS rows_read,
                user
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND event_time > now() - INTERVAL 24 HOUR
              AND query_kind = 'Select'
            ORDER BY query_duration_ms DESC
            LIMIT 15
        """,
    ),
    "query_errors": Analysis(
        analysis_type="query_errors",
        system_tables="system.query_log, system.errors",
        description="Recent query exceptions and error-code frequency (last 24h).",
        sql_template="""
            SELECT
                exception_code,
                any(left(exception, 140)) AS example,
                count()                   AS occurrences
            FROM system.query_log
            WHERE type = 'ExceptionWhileProcessing'
              AND event_time > now() - INTERVAL 24 HOUR
            GROUP BY exception_code
            ORDER BY occurrences DESC
            LIMIT 20
        """,
    ),
    "parts_pressure": Analysis(
        analysis_type="parts_pressure",
        system_tables="system.parts",
        description="Active part counts per table — the too-many-parts risk signal.",
        sql_template="""
            SELECT
                database,
                table,
                count()                            AS active_parts,
                formatReadableQuantity(sum(rows))  AS rows,
                formatReadableSize(sum(bytes_on_disk)) AS on_disk
            FROM system.parts
            WHERE active = 1
            GROUP BY database, table
            ORDER BY active_parts DESC
            LIMIT 20
        """,
    ),
    "merge_backlog": Analysis(
        analysis_type="merge_backlog",
        system_tables="system.merges, system.metrics",
        description="Active merges and merge-queue depth right now.",
        sql_template="""
            SELECT
                database,
                table,
                round(elapsed, 1)                 AS elapsed_s,
                round(progress, 3)                AS progress,
                num_parts,
                formatReadableSize(total_size_bytes_compressed) AS size,
                is_mutation
            FROM system.merges
            ORDER BY elapsed DESC
            LIMIT 20
        """,
    ),
    "insert_profile": Analysis(
        analysis_type="insert_profile",
        system_tables="system.query_log (query_kind='Insert')",
        description="Insert rate, batch sizes and the small-insert antipattern (last 24h).",
        sql_template="""
            SELECT
                toStartOfHour(event_time)                 AS hour,
                count()                                   AS inserts,
                formatReadableQuantity(sum(written_rows)) AS rows_written,
                round(avg(written_rows))                  AS avg_rows_per_insert,
                round(quantile(0.5)(written_rows))        AS median_rows_per_insert
            FROM system.query_log
            WHERE type = 'QueryFinish'
              AND query_kind = 'Insert'
              AND event_time > now() - INTERVAL 24 HOUR
            GROUP BY hour
            ORDER BY hour DESC
            LIMIT 24
        """,
    ),
    "memory_pressure": Analysis(
        analysis_type="memory_pressure",
        system_tables="system.metrics, system.asynchronous_metrics",
        description="Current memory usage, tracked allocations and overcommit signals.",
        sql_template="""
            SELECT metric, formatReadableSize(value) AS value
            FROM system.asynchronous_metrics
            WHERE metric IN ('MemoryResident', 'MemoryTracking',
                             'OSMemoryTotal', 'OSMemoryAvailable',
                             'CGroupMemoryTotal', 'CGroupMemoryUsed')
            UNION ALL
            SELECT metric, toString(value) AS value
            FROM system.metrics
            WHERE metric IN ('MemoryTracking', 'Query', 'BackgroundMergesAndMutationsPoolTask')
            LIMIT 20
        """,
    ),
    "disk_usage": Analysis(
        analysis_type="disk_usage",
        system_tables="system.disks",
        description="Free vs total space per disk — disk-filling / pressure check.",
        sql_template="""
            SELECT
                name,
                path,
                formatReadableSize(free_space)                          AS free,
                formatReadableSize(total_space)                         AS total,
                round(100 * (total_space - free_space) / total_space, 1) AS used_pct
            FROM system.disks
            ORDER BY used_pct DESC
            LIMIT 20
        """,
    ),
    "table_growth": Analysis(
        analysis_type="table_growth",
        system_tables="system.parts, system.tables",
        description="Largest tables by on-disk size and row count.",
        sql_template="""
            SELECT
                database,
                table,
                formatReadableSize(sum(bytes_on_disk)) AS on_disk,
                formatReadableQuantity(sum(rows))      AS rows,
                count()                                AS parts
            FROM system.parts
            WHERE active = 1
            GROUP BY database, table
            ORDER BY sum(bytes_on_disk) DESC
            LIMIT 20
        """,
    ),
    "mutation_status": Analysis(
        analysis_type="mutation_status",
        system_tables="system.mutations",
        description="Stuck or failed mutations (is_done = 0 or with a fail reason).",
        sql_template="""
            SELECT
                database,
                table,
                mutation_id,
                is_done,
                parts_to_do,
                left(latest_fail_reason, 140) AS latest_fail_reason,
                latest_fail_time
            FROM system.mutations
            WHERE is_done = 0 OR latest_fail_reason != ''
            ORDER BY create_time DESC
            LIMIT 20
        """,
    ),
    "settings_audit": Analysis(
        analysis_type="settings_audit",
        system_tables="system.settings (changed = 1)",
        description="Non-default server settings worth flagging.",
        sql_template="""
            SELECT name, value, left(description, 100) AS description
            FROM system.settings
            WHERE changed = 1
            ORDER BY name
            LIMIT 40
        """,
    ),
}


def catalog_digest() -> str:
    """The planner's menu: one line per analysis (name — tables — description)."""
    lines = [
        f"- {a.analysis_type} ({a.system_tables}): {a.description}"
        for a in CATALOG.values()
    ]
    return "\n".join(lines)


CATALOG_DIGEST: str = catalog_digest()
CATALOG_KEYS: frozenset[str] = frozenset(CATALOG.keys())

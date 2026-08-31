"""Every catalog SQL is a single SELECT, LIMIT-bounded, and renders safely.

Runs LLM-free and DB-free (pure string checks). Guards the invariant that no
planner-authored SQL ever executes — the worker only ever runs a fixed template.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ch_client
from analysis_catalog import CATALOG, CATALOG_DIGEST, CATALOG_KEYS


def test_catalog_has_ten_analyses():
    assert len(CATALOG) == 10
    assert CATALOG_KEYS == frozenset(CATALOG.keys())


def test_every_rendered_sql_is_read_only_and_limited():
    for atype, analysis in CATALOG.items():
        sql = analysis.render("last 24h, all tables")
        assert ch_client.is_read_only(sql), f"{atype} rendered non-read-only SQL"
        # a LIMIT must already be present (never rely on the auto-append)
        stripped = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        assert "limit" in stripped.lower(), f"{atype} has no LIMIT"
        # queries only ever touch system.* tables
        assert "system." in sql.lower(), f"{atype} does not query a system table"


def test_render_only_touches_system_tables_no_mutations():
    # Word-boundary check for genuinely dangerous statement verbs. (Substring
    # checks would false-positive on aliases like `AS inserts` or the string
    # literal query_kind='Insert' — which are harmless inside a SELECT.)
    banned = re.compile(r"\b(drop|truncate|alter|attach|detach|delete|optimize)\b",
                        re.IGNORECASE)
    for atype, analysis in CATALOG.items():
        body = analysis.sql_template
        assert not banned.search(body), f"{atype} contains a dangerous statement verb"
        # and the whole thing is a single read-only statement
        assert ch_client.is_read_only(analysis.render()), f"{atype} is not read-only"


def test_focus_is_a_comment_and_cannot_inject():
    # A malicious focus is neutralised into a single comment line — the query
    # body (post-comment-strip) is byte-identical whatever the focus is.
    analysis = CATALOG["parts_pressure"]
    benign = analysis.render("normal focus")
    malicious = analysis.render("x\nDROP TABLE system.parts; --")

    def body(sql):
        return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).strip()

    assert body(benign) == body(malicious)
    # and the whole thing is still a single read-only statement
    assert ch_client.is_read_only(malicious)
    assert "drop table" not in body(malicious).lower()


def test_is_read_only_rejects_non_select_and_multistatement():
    assert ch_client.is_read_only("SELECT 1 LIMIT 1")
    assert ch_client.is_read_only("WITH x AS (SELECT 1) SELECT * FROM x LIMIT 1")
    assert not ch_client.is_read_only("INSERT INTO t VALUES (1)")
    assert not ch_client.is_read_only("DROP TABLE t")
    assert not ch_client.is_read_only("SELECT 1; DROP TABLE t")  # multi-statement
    assert not ch_client.is_read_only("")


def test_ensure_limit_appends_only_when_missing():
    assert ch_client.ensure_limit("SELECT 1").lower().endswith("limit 50")
    already = "SELECT 1 LIMIT 5"
    assert ch_client.ensure_limit(already) == already


def test_catalog_digest_lists_every_analysis():
    for atype in CATALOG:
        assert atype in CATALOG_DIGEST

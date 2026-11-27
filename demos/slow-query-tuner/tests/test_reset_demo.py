"""reset_demo.py offline: finding leftover DDL, the drop statements, kill sentinels, and a run
against a fake ClickHouse. No services."""
from __future__ import annotations

import pytest

import reset_demo as r
import verify_demo as v
from fake_services import PESSIMAL_DDL, PROJECTED_DDL


def test_leftover_ddl_finds_projections_and_indexes():
    ddl = PROJECTED_DDL.replace("PROJECTION p_user", "INDEX idx_u user_id TYPE minmax GRANULARITY 1,\\n    PROJECTION p_user")
    assert r.leftover_ddl(ddl) == [("PROJECTION", "p_user"), ("INDEX", "idx_u")]
    assert r.leftover_ddl(PESSIMAL_DDL) == []


def test_drop_statements_are_idempotent():
    assert r.drop_statements("db.t", [("PROJECTION", "p")]) == ["ALTER TABLE db.t DROP PROJECTION IF EXISTS `p`"]


def test_kill_sentinels_are_cleared_but_state_is_kept(tmp_path):
    (tmp_path / "run-1.kill").write_text("")
    (tmp_path / "run-1.json").write_text("{}")
    assert r.clear_kill_sentinels(tmp_path, dry_run=True) == ["run-1.kill"]
    assert (tmp_path / "run-1.kill").exists()
    assert r.clear_kill_sentinels(tmp_path, dry_run=False) == ["run-1.kill"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run-1.json"]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    state = {"ddl": PROJECTED_DDL, "executed": []}

    def query(url, user, password, sql, timeout=v.TIMEOUT_S):
        assert user == "tuner_admin"
        return state["ddl"]

    def execute(url, user, password, sql):
        state["executed"].append(sql)
        state["ddl"] = PESSIMAL_DDL

    monkeypatch.setattr(r.v, "clickhouse_query", query)
    monkeypatch.setattr(r, "clickhouse_exec", execute)
    monkeypatch.setattr(r, "CHECKPOINTS", tmp_path)
    monkeypatch.setattr(r.v, "load_env", lambda path: True)
    return state


def test_reset_drops_the_projection(lab, capsys):
    assert r.main([]) == 0
    assert lab["executed"] == [f"ALTER TABLE {v.LAB_TABLE} DROP PROJECTION IF EXISTS `p_user`"]
    assert "back to ORDER BY tuple()" in capsys.readouterr().out


def test_dry_run_changes_nothing(lab):
    assert r.main(["--dry-run"]) == 0
    assert lab["executed"] == []


def test_changed_sort_key_needs_a_recreate(lab, capsys):
    lab["ddl"] = PESSIMAL_DDL.replace("ORDER BY tuple()", "ORDER BY user_id")
    assert r.main([]) == 1
    assert "Recreate the lab" in capsys.readouterr().out

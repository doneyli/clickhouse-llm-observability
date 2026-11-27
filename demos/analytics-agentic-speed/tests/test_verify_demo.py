"""verify_demo.py, offline: its probe query is one the agent itself would be allowed to send."""
from __future__ import annotations

import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent.parent
for p in (DEMO, DEMO / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import verify_demo as v  # noqa: E402
from explorer.ch_client import check_sql  # noqa: E402
from explorer.scenarios import check_template_measures, placeholders  # noqa: E402


def test_hero_probe_passes_the_agents_own_rules():
    sql = v.HERO_SCENARIO.replace("{table}", "flights.ontime")
    check_sql(sql, allowed_tables=["flights.ontime"])
    check_template_measures(sql)
    assert placeholders(sql) == {"origin": "String", "buffer": "UInt16"}
    assert "Year = 2019" in sql  # filters on the sort key, like the fixed hero question asks


def test_targets_match_the_seeded_contract():
    assert v.PROJECT == "analytics-agentic-speed"
    assert v.DATASET == "scenario-explorer-questions"
    assert v.seed.ROOT == "analytics-agentic-speed" and v.seed.LEGACY_ROOT == "scenario-explorer"

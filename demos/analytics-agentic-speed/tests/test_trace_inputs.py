"""Every observation the agent opens carries an input (Langfuse shows `undefined` otherwise).

Runs `profile` and one full round against a fake ClickHouse and a fake Langfuse client
that records each observation it opens. No network.
"""
from contextlib import contextmanager

import pytest

from explorer import agent as A
from explorer.ch_client import QueryResult, QueryStats
from explorer.config import ClickHouseSettings, RunConfig
from explorer.models import Lever, Plan


class FakeSpan:
    def __init__(self, rec):
        self.rec = rec

    def update(self, **kw):
        self.rec.setdefault("updates", []).append(kw)

    def score(self, **kw):
        pass

    score_trace = score


class FakeLF:
    def __init__(self):
        self.opened = []

    @contextmanager
    def start_as_current_observation(self, *, name, as_type="span", input=None, **kw):
        rec = {"name": name, "as_type": as_type, "input": input}
        self.opened.append(rec)
        yield FakeSpan(rec)


def result(cols, row):
    return QueryResult(columns=cols, types=["String"] * len(cols), rows=[row],
                       stats=QueryStats(server_ms=5.0, roundtrip_ms=50.0, read_rows=1000, read_bytes=8000,
                                        result_rows=1, query_id="q"))


class FakeCH:
    settings = ClickHouseSettings(host="abcd.us-west-2.aws.clickhouse.cloud", user="u", password="p")
    allowed_tables = ["flights.ontime"]

    async def query(self, sql, params=None, *, settings=None, check=True):
        if "system.columns" in sql:
            return QueryResult(["name", "type"], ["String", "String"], [["Origin", "String"]],
                               result(["x"], [1]).stats)
        if "first_day" in sql:
            return result(["flights", "first_day", "last_day"], [123456, "1987-10-01", "2026-08-31"])
        if "HAVING m = 12" in sql:
            return result(["y"], [2024])
        if "groupArray" in sql:
            return result(["blocks"], [["0001-0559", "0600-0659"]])
        if "uniq(Origin)" in sql:
            return result(["airports", "carriers"], [438, 34])
        if sql.startswith("SELECT count() AS flights"):
            return result(["flights"], [123456])
        return result(["metric", "support"], [0.8, 1000])  # a scenario


@pytest.mark.asyncio
async def test_profile_and_round_observations_all_have_input(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "PROFILE_CACHE_DIR", tmp_path)  # force a real (uncached) profile
    lf, ch, meter, cfg = FakeLF(), FakeCH(), A.DbMeter(), RunConfig(score_each_query=False)
    await A.profile(lf, ch, meter, cfg)
    plan = Plan(sql_template="SELECT count() AS support, avg(ArrDelay <= 15 + {buffer:UInt16}) AS metric "
                             "FROM flights.ontime WHERE Origin = {origin:String}",
                levers=[Lever("origin", "String", ["ATL", "ORD"]), Lever("buffer", "UInt16", ["0", "5"])],
                metric_name="on_time", objective="maximize", reasoning="sweep buffers")
    rec = await A._run_round(lf, ch, meter, cfg, plan, 1, lambda _m: None)

    assert len(rec.outcomes) == 4
    names = {o["name"] for o in lf.opened}
    assert {"profile", "round", "compile", "sweep", "clickhouse.query"} <= names
    missing = [o["name"] for o in lf.opened if o["input"] in (None, {}, [])]
    assert not missing, f"observations without input: {missing}"
    rnd = next(o for o in lf.opened if o["name"] == "round")
    assert rnd["input"]["grid"] == {"origin": {"values": 2, "first": "ATL", "last": "ORD"},
                                    "buffer": {"values": 2, "first": "0", "last": "5"}}  # as the model wrote them
    sweep = next(o for o in lf.opened if o["name"] == "sweep")
    assert sweep["input"]["scenarios"] == 4 and "{buffer:UInt16}" in sweep["input"]["sql_template"]

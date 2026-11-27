"""No-network tests for the SQL allow-list, parameter encoding and the grid compiler."""
import pytest

from explorer.ch_client import UnsafeSQLError, check_sql, encode_param, _num
from explorer.models import Lever, Plan
from explorer.scenarios import PlanError, expand, placeholders, scenario_key, validate

T = ["flights.ontime"]
TEMPLATE = ("SELECT count() AS support, countIf(ArrDelay <= 15 + {buffer:UInt16}) / count() AS metric "
            "FROM flights.ontime WHERE Year = 2019 AND Origin = {origin:String}")


def plan(**kw):
    base = dict(sql_template=TEMPLATE, metric_name="on_time", objective="maximize",
                levers=[Lever("buffer", "UInt16", ["0", "5", "10"]), Lever("origin", "String", ["ATL", "ORD"])])
    base.update(kw)
    return Plan(**base)


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT toYear(FlightDate) FROM flights.ontime",
    "SELECT extract(YEAR FROM FlightDate) FROM flights.ontime",
    "SELECT count() FROM ontime LIMIT 10 OFFSET 5",
])
def test_allowed(sql):
    check_sql(sql, allowed_tables=T)


@pytest.mark.parametrize("sql", [
    "INSERT INTO flights.ontime SELECT 1",
    "SELECT 1; DROP TABLE flights.ontime",
    "SELECT * FROM s3('https://x/y.csv')",
    "SELECT * FROM url('https://x')",
    "SELECT * FROM remoteSecure('h', 'db', 't')",
    "SELECT * FROM system.users",
    "SELECT * FROM default.secret JOIN flights.ontime USING (Year)",
    "SELECT 1 SETTINGS max_threads = 1000",
    "DROP TABLE flights.ontime",
])
def test_rejected(sql):
    with pytest.raises(UnsafeSQLError):
        check_sql(sql, allowed_tables=T)


def test_encode_param():
    assert encode_param(5) == "5"
    assert encode_param("ATL") == "ATL"
    assert encode_param(["ATL", "O'R"]) == "['ATL','O\\'R']"
    assert encode_param([1, 2]) == "[1,2]"


def test_num_strips_fixedstring_padding_and_parses_ints():
    assert _num("ATL\x00\x00") == "ATL"
    assert _num("123") == 123 and _num("0.5") == 0.5


def test_placeholders_and_validate_ok():
    assert placeholders(TEMPLATE) == {"buffer": "UInt16", "origin": "String"}
    validate(plan(), allowed_tables=T, max_scenarios=10)


@pytest.mark.parametrize("kw,msg", [
    ({"levers": [Lever("buffer", "UInt16", ["0"])]}, "must match lever names"),
    ({"levers": [Lever("buffer", "String", ["0"]), Lever("origin", "String", ["ATL"])]}, "placeholder says"),
    ({"levers": [Lever("buffer", "UInt16", ["x"]), Lever("origin", "String", ["ATL"])]}, "not an integer"),
    ({"sql_template": TEMPLATE.replace(" AS metric", " AS m")}, "metric"),
    ({"sql_template": "SELECT 1 AS metric, 1 AS support FROM s3('x') WHERE {buffer:UInt16} = 1 AND {origin:String} = ''"}, "rejected"),
])
def test_validate_rejects(kw, msg):
    with pytest.raises(PlanError, match=msg):
        validate(plan(**kw), allowed_tables=T, max_scenarios=10)


def test_grid_budget_enforced_and_expand_typed():
    with pytest.raises(PlanError, match="budget"):
        validate(plan(), allowed_tables=T, max_scenarios=5)
    sc, truncated = expand(plan(), max_scenarios=10)
    assert len(sc) == 6 and not truncated
    assert sc[0] == {"buffer": 0, "origin": "ATL"}
    sc, truncated = expand(plan(), max_scenarios=4)
    assert len(sc) == 4 and truncated


def test_scenario_key_is_lenient():
    assert scenario_key({"origin": "atl", "buffer": 10}) == scenario_key({"buffer": "10", "origin": "ATL"})
    assert scenario_key({"m": 1.0}) == scenario_key({"m": "1"})


# --- "measure, don't search", enforced in code -------------------------------------
from explorer.scenarios import check_lookup  # noqa: E402

LOOKUP_THAT_GAVE_THE_ANSWER_AWAY = (  # from the first Experiment A run (q-weather-share)
    "SELECT Origin, Year, Month, sum(WeatherDelay) / sum(CarrierDelay + WeatherDelay + NASDelay + "
    "SecurityDelay + LateAircraftDelay) AS share, countIf(ArrDel15 = 1) AS late FROM flights.ontime "
    "WHERE Year BETWEEN 2015 AND 2019 AND Cancelled = 0 GROUP BY Origin, Year, Month ORDER BY share DESC LIMIT 60")


@pytest.mark.parametrize("sql", [
    "SELECT Origin, count() AS n FROM flights.ontime WHERE Year = 2019 GROUP BY Origin ORDER BY n DESC LIMIT 20",
    "SELECT DISTINCT Reporting_Airline FROM flights.ontime WHERE Year = 2019 AND Origin = 'ORD'",
    "SELECT min(FlightDate), max(FlightDate), uniq(Dest) FROM flights.ontime WHERE Origin = 'ATL'",
])
def test_lookup_may_list_candidates(sql):
    check_lookup(sql)


@pytest.mark.parametrize("sql,msg", [
    (LOOKUP_THAT_GAVE_THE_ANSWER_AWAY, "outcome columns"),
    ("SELECT Origin, count() FROM flights.ontime WHERE Cancelled = 1 GROUP BY Origin", "outcome columns"),
    ("SELECT Reporting_Airline, avg(Distance) FROM flights.ontime GROUP BY 1", "metrics"),
    ("SELECT Origin, countIf(Month = 1) FROM flights.ontime GROUP BY 1", "metrics"),
])
def test_lookup_may_not_measure(sql, msg):
    with pytest.raises(PlanError, match=msg):
        check_lookup(sql)


@pytest.mark.parametrize("bad", [
    # the hero's first attempt: search all buffers inside one query per airport
    "SELECT arrayFirst(b -> countIf(ArrDelay <= 15 + b) / count() > 0.85, range(61)) AS metric, count() AS support "
    "FROM flights.ontime WHERE Origin = {origin:String} AND {buffer:UInt16} >= 0",
    "SELECT argMax(Month, c) AS metric, count() AS support FROM flights.ontime WHERE Origin = {origin:String} AND {buffer:UInt16} >= 0",
])
def test_template_may_not_search(bad):
    with pytest.raises(PlanError, match="searches over candidate values"):
        validate(plan(sql_template=bad), allowed_tables=T, max_scenarios=10)


@pytest.mark.parametrize("metric", [
    "quantileExact(0.85)(ArrDelay) - 15", "quantileTDigest(0.85)(ArrDelay) - 15",
    "median(ArrDelay)", '`quantileExact` /* gap */ (0.85)(ArrDelay) - 15',
])
def test_threshold_cannot_be_solved_without_sweeping_it(metric):
    sql = f"SELECT {metric} AS metric, count() AS support FROM flights.ontime WHERE Origin = {{origin:String}}"
    p = plan(sql_template=sql, levers=[Lever("origin", "String", ["ATL", "LAX"])])
    with pytest.raises(PlanError, match="searches over candidate values"):
        validate(p, allowed_tables=T, max_scenarios=10)


@pytest.mark.parametrize("sql", [
    "SELECT avg(ArrDelay) AS metric, count() AS support FROM flights.ontime GROUP BY Origin ORDER BY metric LIMIT 1",
    "SELECT metric, support FROM (SELECT avg(ArrDelay) AS metric, count() AS support FROM flights.ontime GROUP BY Origin) LIMIT 1",
    "WITH best AS (SELECT avg(ArrDelay) AS metric, count() AS support FROM flights.ontime) SELECT * FROM best",
    "SELECT avg(ArrDelay) OVER () AS metric, count() OVER () AS support FROM flights.ontime LIMIT 1",
    "SELECT avg(ArrDelay) AS metric, count() AS support FROM flights.ontime UNION ALL SELECT 1, 1",
    "SELECT count() AS metric, count() AS support FROM flights.ontime ARRAY JOIN [1,2,3] AS n",
    "SELECT arrayJoin([1,2,3]) AS metric, count() AS support FROM flights.ontime",
    "SELECT avg(ArrDelay) AS metric, count() AS support FROM flights.ontime JOIN flights.ontime AS b USING (Origin)",
    "SELECT 1 AS metric, 1 AS support FROM flights.ontime",
    "SELECT count() AS metric, count() AS support FROM secret",
])
def test_template_rejects_sql_side_search_and_non_scalar_shapes(sql):
    with pytest.raises(PlanError):
        validate(plan(sql_template=sql, levers=[]), allowed_tables=T, max_scenarios=10)


def test_template_parser_preserves_scalar_guards_and_typed_arrays():
    sql = ("SELECT if(count() >= 500, round(sum(WeatherDelay) / nullIf(sum(ArrDelay), 0), 4), NULL) AS metric, "
           "count() AS support FROM flights.ontime WHERE Origin IN {origins:Array(String)} AND Year={year:UInt16}")
    p = plan(sql_template=sql, levers=[Lever("origins", "Array(String)", [["LAX", "ORD"]]),
                                     Lever("year", "UInt16", [2019])])
    validate(p, allowed_tables=T, max_scenarios=10)


def test_function_names_in_literals_do_not_trigger_search_rule():
    p = plan(sql_template=TEMPLATE + " AND DepTimeBlk != 'quantileExact(0.85)' AND Month IN [1,2,3]")
    validate(p, allowed_tables=T, max_scenarios=10)

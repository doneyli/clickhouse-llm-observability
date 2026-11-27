"""Schema and conventions of data/questions.yaml. No network."""
from __future__ import annotations

import re

import pytest

from explorer.ch_client import check_sql
from explorer.evaluators import load_question_specs, load_questions, ranking_sql, split_reference_sql

SPECS = load_question_specs()
IDS = [s["id"] for s in SPECS]
TABLE = "flights.ontime"
# Columns of flights.ontime the questions may use (the agent sees the same table).
COLUMNS = {
    "Year", "Quarter", "Month", "DayofMonth", "DayOfWeek", "FlightDate", "Reporting_Airline", "Tail_Number",
    "Flight_Number_Reporting_Airline", "Origin", "OriginCityName", "OriginState", "Dest", "DestCityName",
    "DestState", "CRSDepTime", "DepDelay", "DepDel15", "DepTimeBlk", "TaxiOut", "TaxiIn", "ArrDelay", "ArrDel15",
    "ArrTimeBlk", "Cancelled", "CancellationCode", "Diverted", "AirTime", "Distance", "DistanceGroup",
    "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
}
# Capitalised identifiers in the SQL that are keywords/functions, not columns.
NOT_COLUMNS = {"SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "LIMIT", "WITH", "AS", "AND", "OR", "IN",
               "HAVING", "ASC", "DESC", "ARRAY", "JOIN", "BETWEEN", "NOT"}


def test_question_count_and_unique_ids():
    assert 12 <= len(SPECS) <= 16
    assert len(set(IDS)) == len(IDS)
    for qid in IDS:
        assert re.fullmatch(r"q-[a-z0-9-]+", qid), qid


def test_hero_question():
    hero = next(s for s in SPECS if s["id"] == "q-padding")
    assert hero["answer_keys"] == ["origin", "buffer_minutes"]
    text = " ".join(hero["text"].split())
    for needle in ("evening", "10 busiest", "2019", "85%", "ArrDelay <= 15 + buffer"):
        assert needle in text, needle


PERIOD = re.compile(r"(In|From|Pooling|Between) ((19|20)\d\d)\b")


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_text_opens_with_the_measured_period(spec):
    """The text names the period the reference SQL measures, up front.

    "The busiest airports of 2019" only ranks; without "In 2019, ..." the agent may measure
    every year (it did: ~4.4M rows per scenario instead of one year's slice, right by luck).
    """
    text = " ".join(spec["text"].split())
    m = PERIOD.match(text)
    assert m, f"{spec['id']}: text should open with its period, e.g. 'In 2019, ...'"
    measured = set(re.findall(r"Year (?:=|IN|BETWEEN) \(?((?:19|20)\d\d)", spec["reference_sql"]))
    assert m.group(2) in measured, f"{spec['id']}: opens with {m.group(2)}, SQL measures {measured}"


def test_difficulty_mix():
    levels = [s["difficulty"] for s in SPECS]
    assert set(levels) <= {"easy", "medium", "hard"}
    assert levels.count("easy") >= 2
    assert 2 <= levels.count("hard") <= 3
    assert levels.count("medium") >= len(SPECS) // 2


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_fields(spec):
    assert set(spec) == {"id", "difficulty", "text", "answer_keys", "reference_sql", "notes"}
    assert isinstance(spec["text"], str) and len(spec["text"].split()) >= 15
    keys = spec["answer_keys"]
    assert isinstance(keys, list) and 1 <= len(keys) <= 4 and len(set(keys)) == len(keys)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", k) for k in keys)
    # The question tells the agent how to format each key.
    assert "report" in spec["text"].lower()
    # Notes record the validated answer and the margin over the runner-up.
    assert "margin" in spec["notes"].lower() and "runner-up" in spec["notes"].lower()


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_reference_sql_formats_and_passes_allow_list(spec):
    raw = spec["reference_sql"]
    assert "{table}" in raw
    sql = raw.format(table=TABLE)  # no stray braces: str.format must work
    assert "{" not in sql and "}" not in sql
    check_sql(sql, allowed_tables=[TABLE])  # the agent's own allow-list accepts it


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_reference_sql_convention(spec):
    sql = spec["reference_sql"].format(table=TABLE)
    head, keys, order = split_reference_sql(sql)
    assert keys == spec["answer_keys"], "final SELECT must return exactly the answer keys, in order"
    assert re.search(r"(?i)\branked\s+AS\s*\(", head), "needs a CTE named ranked"
    assert re.search(r"(?i)\bAS\s+metric\b", head) and re.search(r"(?i)\bAS\s+support\b", head)
    assert order.split()[0] == "metric", "rank on the metric first"
    # Deterministic tie-break: every answer key appears in the ORDER BY after the metric
    # (except a key that is the metric itself, e.g. buffer_minutes).
    for k in keys:
        assert re.search(rf"\b{k}\b", order) or re.search(rf"\b{k}\s+AS\s+metric\b", head), k
    widened = ranking_sql(sql, top=3)
    assert widened.rstrip().endswith("LIMIT 3") and "SELECT * FROM ranked" in widened


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_reference_sql_uses_only_known_columns(spec):
    sql = spec["reference_sql"].format(table=TABLE)
    sql = re.sub(r"'[^']*'", "''", sql)  # drop string literals
    idents = set(re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", sql)) - NOT_COLUMNS
    assert idents <= COLUMNS, idents - COLUMNS


@pytest.mark.parametrize("spec", SPECS, ids=IDS)
def test_completed_flights_filter(spec):
    # Cancelled and diverted rows carry ArrDelay = 0 (not NULL): any delay metric must exclude them.
    sql = spec["reference_sql"]
    if re.search(r"ArrDelay|ArrDel15|DepDelay|DepDel15|TaxiOut|Delay\b", sql) and "avg(Cancelled)" not in sql:
        assert "Cancelled = 0" in sql and "Diverted = 0" in sql
        assert "Cancelled = 0" in spec["text"] or "completed" in spec["text"].lower()


def test_load_questions_returns_models():
    qs = load_questions()
    assert [q.id for q in qs] == IDS
    assert all(q.text and "\n" not in q.text and q.answer_keys and q.reference_sql for q in qs)

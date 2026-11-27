"""Compile a plan into concrete scenarios: validate the template, cast values, expand the grid.

Deterministic on purpose: the LLM writes ONE template and a grid; this module turns that
into N parameterised queries without another model call. LLM cost scales with rounds;
database work scales with scenarios.
"""
from __future__ import annotations

import itertools
import json
import re
from typing import Any, Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .ch_client import UnsafeSQLError, check_sql
from .models import Lever, Plan

ALLOWED_TYPES = ("String", "UInt8", "UInt16", "UInt32", "Int32", "Int64", "Float64", "Date", "Array(String)")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^{}]+?)\s*\}")
_INT_TYPES = {"UInt8", "UInt16", "UInt32", "Int32", "Int64"}


class PlanError(ValueError):
    """The plan can't be compiled; the message goes back to the model to fix it."""


# "Measure, don't search", enforced in code rather than left to the prompt.
# A template measures ONE scenario; these functions are how a template searches
# candidate values internally (what the agent did before the rule existed).
_SEARCH_FUNCS = re.compile(
    r"array\w*|range|numbers\w*|argMax\w*|argMin\w*|topK\w*|groupArray\w*|groupUniqArray\w*|"
    r"quantile\w*|median\w*",
    re.IGNORECASE,
)

# Lookups find WHO is in the population (the busiest airports, the active carriers).
# They never measure outcomes: that's the sweep's job. Without this a lookup ranks the
# whole space by the question's own metric in one GROUP BY, and the sweep only confirms
# the winner (Experiment A, first run: a 10-scenario budget scored the same as 300).
OUTCOME_COLUMNS = (
    "DepDelay", "DepDel15", "TaxiOut", "TaxiIn", "ArrDelay", "ArrDel15", "Cancelled",
    "CancellationCode", "Diverted", "AirTime", "CarrierDelay", "WeatherDelay", "NASDelay",
    "SecurityDelay", "LateAircraftDelay",
)
_OUTCOME_RE = re.compile(r"\b(" + "|".join(OUTCOME_COLUMNS) + r")\b")
_METRIC_AGGS = re.compile(
    r"\b(avg\w*|sum\w*|quantile\w*|median\w*|\w+If|argMax|argMin|stddev\w*|varPop\w*|varSamp\w*|"
    r"corr\w*|covar\w*|topK\w*|simpleLinearRegression|entropy)\s*\(",
    re.IGNORECASE,
)


def check_template_measures(sql: str, *, allowed_tables: list[str]) -> exp.Select:
    """Accept a single aggregate over one table, with no SQL-side candidate search.

    Parse the original ClickHouse placeholders, rather than scanning keywords: nested
    SELECTs, ARRAY JOIN and quoted function names must obey the same grammar too.
    Quantiles/medians are deliberately excluded from this demo's scenario grammar:
    otherwise padding questions can be solved without ever sweeping a buffer lever.
    """
    try:
        statements = sqlglot.parse(sql, read="clickhouse")
    except SqlglotError as e:
        raise PlanError(f"can't parse scenario template: {e}") from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise PlanError("a template must be one aggregate SELECT, with no set operations")
    tree = statements[0]
    if len(list(tree.find_all(exp.Select))) != 1 or tree.find(exp.Subquery, exp.Window):
        raise PlanError("a template measures ONE scenario: no subqueries or window functions")
    # A narrow grammar also rules out GROUP BY ... ORDER BY ... LIMIT 1, DISTINCT,
    # CTEs and joins that select a winner before the sweep sees the results.
    for clause, value in tree.args.items():
        if value and clause not in {"expressions", "from_", "where"}:
            raise PlanError(f"a template measures ONE scenario: {clause} is not allowed")
    source = tree.args.get("from_")
    table = source.this if source is not None else None
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier):
        raise PlanError("a template must read the demo table directly, with no table functions")
    ref = f"{table.db}.{table.name}" if table.db else table.name
    allowed = {t.lower() for t in allowed_tables}
    allowed.update(t.rsplit(".", 1)[-1] for t in list(allowed))
    if table.catalog or ref.lower() not in allowed or any(
        v for k, v in table.args.items() if k not in {"this", "db", "catalog", "alias"}
    ):
        raise PlanError(f"scenario table {ref!r} is not allowed")
    for fn in tree.find_all(exp.Func):
        name = fn.name if isinstance(fn, (exp.Anonymous, exp.CombinedAggFunc, exp.ParameterizedAgg)) else fn.sql_name()
        if isinstance(fn, exp.Explode) or (
            not isinstance(fn, exp.Array) and _SEARCH_FUNCS.fullmatch(name.replace("_", ""))
        ):
            raise PlanError(
                f"the template uses {name}(), which searches over candidate values inside the SQL. "
                "A template measures ONE scenario: make every value you want to try a lever and let the "
                "sweep run them.")
    if not tree.find(exp.AggFunc):
        raise PlanError("a template must aggregate its rows into ONE scenario result")
    return tree


def check_lookup(sql: str) -> None:
    """Raise PlanError unless the lookup only lists candidates (no outcome measurement)."""
    m = _OUTCOME_RE.search(sql)
    if m:
        raise PlanError(
            f"lookups list candidates; they may not read outcome columns ({m.group(1)}). "
            "Measuring outcomes is the sweep's job.")
    m = _METRIC_AGGS.search(sql)
    if m:
        raise PlanError(
            f"lookups may count and list (count, uniq, min, max) but not compute metrics ({m.group(1)}()).")


def placeholders(sql: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, typ in _PLACEHOLDER.findall(sql):
        typ = re.sub(r"\s+", "", typ)
        if name in found and found[name] != typ:
            raise PlanError(f"placeholder {name!r} used with two types: {found[name]} and {typ}")
        found[name] = typ
    return found


def cast_value(raw: Any, ch_type: str) -> Any:
    t = re.sub(r"\s+", "", ch_type)
    if t in _INT_TYPES:
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            raise PlanError(f"value {raw!r} is not an integer for type {t}") from None
    if t == "Float64":
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise PlanError(f"value {raw!r} is not a number") from None
    if t == "Array(String)":
        if isinstance(raw, (list, tuple)):
            return [str(x).strip() for x in raw]
        s = str(raw).strip()
        if s.startswith("["):
            try:
                return [str(x).strip() for x in json.loads(s.replace("'", '"'))]
            except json.JSONDecodeError:
                raise PlanError(f"can't parse array value {raw!r}") from None
        return [p.strip() for p in s.split(",") if p.strip()]
    return str(raw).strip()


def grid_size(levers: Iterable[Lever]) -> int:
    n = 1
    for lv in levers:
        n *= max(len(lv.values), 1)
    return n


def validate(plan: Plan, *, allowed_tables: list[str], max_scenarios: int) -> None:
    """Raise PlanError with a message the model can act on."""
    if not plan.sql_template.strip():
        raise PlanError("sql_template is empty")
    try:
        check_sql(plan.sql_template, allowed_tables=allowed_tables)
    except UnsafeSQLError as e:
        raise PlanError(f"template rejected: {e}") from None
    tree = check_template_measures(plan.sql_template, allowed_tables=allowed_tables)
    ph = placeholders(plan.sql_template)
    names = [lv.name for lv in plan.levers]
    if len(set(names)) != len(names):
        raise PlanError("lever names must be unique")
    if set(ph) != set(names):
        raise PlanError(
            f"placeholders {sorted(ph)} must match lever names {sorted(names)} exactly"
        )
    for lv in plan.levers:
        t = re.sub(r"\s+", "", lv.ch_type)
        if t not in ALLOWED_TYPES:
            raise PlanError(f"lever {lv.name!r}: type {lv.ch_type!r} not in {ALLOWED_TYPES}")
        if ph[lv.name] != t:
            raise PlanError(f"lever {lv.name!r} is {t} but the placeholder says {ph[lv.name]}")
        if not lv.values:
            raise PlanError(f"lever {lv.name!r} has no values")
        for v in lv.values:
            cast_value(v, t)
    aliases = [e.alias.lower() for e in tree.expressions]
    if "metric" not in aliases:
        raise PlanError("the template must return a column named `metric` (… AS metric)")
    if "support" not in aliases:
        raise PlanError("the template must return a column named `support` (count() AS support)")
    if aliases.count("metric") != 1 or aliases.count("support") != 1:
        raise PlanError("metric and support must each be returned exactly once")
    n = grid_size(plan.levers)
    if n > max_scenarios:
        raise PlanError(f"grid has {n} scenarios; the budget is {max_scenarios}. Shrink it.")


def expand(plan: Plan, *, max_scenarios: int) -> tuple[list[dict[str, Any]], bool]:
    """All combinations (lever name -> typed value), capped. Returns (scenarios, truncated)."""
    axes = [[(lv.name, cast_value(v, lv.ch_type)) for v in lv.values] for lv in plan.levers]
    combos = itertools.product(*axes)
    out = [dict(c) for c in itertools.islice(combos, max_scenarios + 1)]
    truncated = len(out) > max_scenarios
    return out[:max_scenarios], truncated


def scenario_key(scenario: dict[str, Any]) -> tuple:
    """Order-independent, type-lenient key used to find a scenario the model names."""
    def norm(v: Any) -> Any:
        if isinstance(v, (list, tuple)):
            return tuple(norm(x) for x in v)
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return round(float(v), 6)
        s = str(v).strip()
        try:
            return round(float(s), 6)
        except ValueError:
            return s.upper()

    return tuple(sorted((k, norm(v)) for k, v in scenario.items()))


def plan_from_json(d: dict[str, Any]) -> Plan:
    return Plan(
        sql_template=d.get("sql_template", "") or "",
        levers=[Lever(name=l["name"], ch_type=l["ch_type"], values=list(l["values"])) for l in d.get("levers", [])],
        metric_name=d.get("metric_name", "metric") or "metric",
        objective=d.get("objective", "maximize") or "maximize",
        reasoning=d.get("reasoning", "") or "",
    )

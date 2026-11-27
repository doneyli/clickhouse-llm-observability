"""The analytics agent: profile -> plan -> (compile -> sweep -> analyse)* -> verify.

Trace shape (stable names; parameters live in metadata, never in names):

    analytics-agentic-speed      agent       question in, answer out
    ├─ profile                   chain       deterministic schema/profile queries
    │  └─ clickhouse.query ×k    tool
    ├─ plan                      generation  (+ lookups chain and a 2nd plan call if asked)
    ├─ round                     chain       round_* scores
    │  ├─ compile                span
    │  └─ sweep                  chain
    │     └─ clickhouse.query ×N tool        db_server_ms score on each
    ├─ analyse                   generation  -> answer | refine (loops to the next round)
    └─ verify                    chain       re-runs the winning scenario
"""
from __future__ import annotations

import contextvars
import json
import math
import re
import time
from dataclasses import asdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from langfuse import propagate_attributes

from . import prompts as P
from .ch_client import ClickHouseClient, ClickHouseError, QueryResult, gather_bounded
from .config import DEMO_DIR, RunConfig
from .llm import DECISION_SCHEMA, LLM, PLAN_SCHEMA, LLMError
from .models import Plan, Question, RoundRecord, RunResult, ScenarioOutcome
from .scenarios import PlanError, check_lookup, expand, plan_from_json, scenario_key, validate

QUERY_SPAN = "clickhouse.query"
# One row per scenario: anything else is a template bug, and the server says so.
SCENARIO_SETTINGS = {"max_result_rows": "1", "result_overflow_mode": "throw"}
LOOKUP_SETTINGS = {"max_result_rows": "100", "result_overflow_mode": "break"}
LOOKUP_MAX_CHARS = 8000
CELL_MAX_CHARS = 200


def _cell(v: Any) -> str:
    s = str(v)
    return s if len(s) <= CELL_MAX_CHARS else s[:CELL_MAX_CHARS] + "…"

Emit = Optional[Callable[[str], None]]
Progress = Optional[Callable[[dict], None]]

# Structured live events for the web UI (webapp/). A contextvar rather than a parameter
# threaded through every helper: asyncio tasks copy the context, so the sweep's query
# tasks see the same callback.
_PROGRESS: contextvars.ContextVar[Progress] = contextvars.ContextVar("analytics_agentic_speed_progress",
                                                                     default=None)


def _p(event: dict) -> None:
    cb = _PROGRESS.get()
    if cb is not None:
        try:
            cb({"t": round(time.time(), 3), **event})
        except Exception:  # noqa: BLE001 - a broken listener must never break a run
            pass


async def _think(llm: "LLM", lf: Any, *, name: str, **kw: Any) -> dict:
    """One model call, bracketed by phase events so the UI can show 'thinking…'."""
    t0 = time.perf_counter()
    _p({"type": "phase", "phase": name, "state": "start"})
    try:
        return await llm.structured(lf, name=name, **kw)
    finally:
        _p({"type": "phase", "phase": name, "state": "end", "ms": round((time.perf_counter() - t0) * 1000),
            "llm_calls": llm.calls, "cost_usd": round(llm.cost_usd, 4)})


@dataclass
class DbMeter:
    """What the database cost this run: server time per query, and time spent waiting."""

    server_ms: list[float] = field(default_factory=list)
    wait_ms: float = 0.0
    queries: int = 0
    errors: int = 0

    @property
    def total_server_ms(self) -> float:
        return sum(self.server_ms)

    def p(self, q: float) -> float:
        return percentile(self.server_ms, q)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def _md_value(v: Any) -> Any:
    return v if isinstance(v, (int, float, str, bool)) else json.dumps(v)


async def traced_query(lf: Any, ch: ClickHouseClient, sql: str, params: Optional[dict] = None, *,
                       meter: DbMeter, purpose: str, score: bool, scenario: Optional[dict] = None,
                       round_index: Optional[int] = None, settings: Optional[dict] = None,
                       check: bool = True, sequential: bool = True) -> QueryResult:
    """One ClickHouse query as one `clickhouse.query` tool span with the database's own timing."""
    md: dict[str, Any] = {"purpose": purpose}
    if round_index is not None:
        md["round_index"] = round_index
    for k, v in (scenario or {}).items():
        md[f"scenario_{k}"] = _md_value(v)
    with lf.start_as_current_observation(as_type="tool", name=QUERY_SPAN,
                                         input={"sql": sql, "params": params or {}},
                                         metadata=md) as span:
        t0 = time.perf_counter()
        try:
            res = await ch.query(sql, params, settings=settings, check=check)
        except Exception as e:  # noqa: BLE001 - recorded on the span, then re-raised
            meter.errors += 1
            if sequential:
                meter.wait_ms += (time.perf_counter() - t0) * 1000.0
            msg = str(e)
            span.update(level="ERROR", status_message=msg[:300], output={"error": msg[:2000]},
                        metadata={**md, "db_error_code": getattr(e, "code", None) or ""})
            raise
        meter.queries += 1
        meter.server_ms.append(res.stats.server_ms)
        if sequential:
            meter.wait_ms += res.stats.roundtrip_ms
        span.update(output={"columns": res.columns, "rows": res.rows[:5]},
                    metadata={**md, **res.stats.as_metadata()})
        if score:
            span.score(name="db_server_ms", value=round(res.stats.server_ms, 2), data_type="NUMERIC")
        return res


# --------------------------------------------------------------------------- profile
PROFILE_CACHE_DIR = DEMO_DIR / ".cache"


def _profile_cache_path(ch: ClickHouseClient, rows: int) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{ch.settings.host}-{ch.settings.qualified_table}-{rows}")
    return PROFILE_CACHE_DIR / f"profile-{safe}.json"


async def profile(lf: Any, ch: ClickHouseClient, meter: DbMeter, cfg: RunConfig) -> tuple[str, str]:
    """Schema, size, date range and value domains. Returns (schema, profile).

    These are facts about the table, not about the question, so they're cached on disk
    keyed by host, table and row count: each run pays one metadata-only `count()` (it
    shows in the trace) and re-profiles only when the table changes. The full profile
    scans every row a few times, which would otherwise be charged to every question.
    """
    t = ch.settings.qualified_table
    with lf.start_as_current_observation(as_type="chain", name="profile",
                                         input={"table": t,
                                                "host": ch.settings.host.split(".")[0][:4] + "…"}) as span:
        t0 = time.perf_counter()
        q = lambda sql, params=None, check=True: traced_query(  # noqa: E731
            lf, ch, sql, params, meter=meter, purpose="profile", score=cfg.score_each_query,
            check=check, sequential=False)
        rows = int((await q(f"SELECT count() AS flights FROM {t}")).first()["flights"])
        cache = _profile_cache_path(ch, rows)
        if cache.exists():
            data = json.loads(cache.read_text())
            meter.wait_ms += (time.perf_counter() - t0) * 1000.0
            span.update(output={"profile": data["profile"], "cached": True}, metadata={"cached": True})
            return data["schema"], data["profile"]
        results = await gather_bounded([
            q("SELECT name, type FROM system.columns WHERE database = {db:String} AND table = {t:String} "
              "ORDER BY position", {"db": ch.settings.database, "t": ch.settings.table}, check=False),
            q(f"SELECT count() AS flights, min(FlightDate) AS first_day, max(FlightDate) AS last_day FROM {t}"),
            q(f"SELECT max(Year) AS y FROM (SELECT Year, uniqExact(Month) AS m FROM {t} GROUP BY Year HAVING m = 12)"),
            q(f"SELECT groupArray(b) AS blocks FROM (SELECT DISTINCT DepTimeBlk AS b FROM {t} ORDER BY b)"),
            q(f"SELECT uniq(Origin) AS airports, uniq(Reporting_Airline) AS carriers FROM {t}"),
        ], limit=8)
        meter.wait_ms += (time.perf_counter() - t0) * 1000.0
        for r in results:
            if isinstance(r, Exception):
                raise r
        cols, size, full_year, blocks, domains = results
        schema = "Columns:\n" + "\n".join(f"  {c['name']} {c['type']}" for c in cols.records())
        s, fy, b, d = size.first(), full_year.first(), blocks.first(), domains.first()
        prof = (
            f"- {s['flights']:,} flights from {s['first_day']} to {s['last_day']}\n"
            f"- latest complete calendar year: {fy['y']}\n"
            f"- {d['airports']} origin airports, {d['carriers']} reporting carriers\n"
            f"- DepTimeBlk values: {', '.join(b['blocks'])}\n"
            "- ArrDelay/DepDelay are minutes (negative = early); ArrDel15 = 1 if 15+ min late;\n"
            "  Cancelled/Diverted are 0/1; cancelled and diverted flights have no ArrDelay value (0)."
        )
        span.update(output={"schema_columns": len(cols.rows), "profile": prof, "cached": False},
                    metadata={"cached": False})
        PROFILE_CACHE_DIR.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"schema": schema, "profile": prof}))
        return schema, prof


# --------------------------------------------------------------------------- helpers
def _results_table(rec: RoundRecord, *, top: int = 25, bottom: int = 5) -> str:
    ok = rec.ok
    rev = rec.plan.objective == "maximize"
    ranked = sorted(ok, key=lambda o: o.metric, reverse=rev)
    names = [lv.name for lv in rec.plan.levers]
    head = "\t".join(names + ["metric", "support"])

    def line(o: ScenarioOutcome) -> str:
        vals = [json.dumps(o.scenario[n]) if isinstance(o.scenario[n], list) else str(o.scenario[n]) for n in names]
        return "\t".join(vals + [f"{o.metric:.6g}", str(o.support)])

    parts = [f"Round {rec.index}: {len(rec.outcomes)} scenarios run, {len(ok)} with a metric, "
             f"{sum(1 for o in rec.outcomes if o.error)} errors; objective: {rec.plan.objective} "
             f"{rec.plan.metric_name}.", f"Template:\n{rec.plan.sql_template}",
             f"Best {min(top, len(ranked))}:", head, *[line(o) for o in ranked[:top]]]
    if len(ranked) > top:
        parts += [f"Worst {min(bottom, len(ranked) - top)}:", head, *[line(o) for o in ranked[-bottom:]]]
    errs = [o for o in rec.outcomes if o.error][:3]
    if errs:
        parts.append("Sample errors: " + " | ".join(f"{o.scenario}: {o.error[:200]}" for o in errs))
    nulls = [o for o in rec.outcomes if o.error is None and o.metric is None]
    if nulls:
        parts.append(f"{len(nulls)} scenarios returned a NULL metric (e.g. below minimum support).")
    return "\n".join(parts)


def _pairs(items: list[dict[str, str]]) -> dict[str, str]:
    return {str(p.get("name", p.get("key", ""))).strip(): p.get("value") for p in items or []}


def _coerce(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        try:
            f = float(s)
            return int(f) if f.is_integer() and "." not in s else f
        except ValueError:
            return s
    return v


# --------------------------------------------------------------------------- the run
async def run_question(question: Question, cfg: RunConfig, *, ch: ClickHouseClient, lf: Any,
                       tags: Optional[list[str]] = None, session_id: Optional[str] = None,
                       metadata: Optional[dict[str, Any]] = None, on_event: Emit = None,
                       version: Optional[str] = None, on_progress: Progress = None) -> RunResult:
    """Run one question. ``version`` sets the trace version (Experiment A uses
    ``breadth-<N>`` so dashboard widgets can group runs by sweep breadth).
    ``on_progress`` receives structured live events (dicts with a ``type``) for the UI."""
    token = _PROGRESS.set(on_progress)
    try:
        return await _run_question(question, cfg, ch=ch, lf=lf, tags=tags, session_id=session_id,
                                   metadata=metadata, on_event=on_event, version=version)
    finally:
        _PROGRESS.reset(token)


async def _run_question(question: Question, cfg: RunConfig, *, ch: ClickHouseClient, lf: Any,
                        tags: Optional[list[str]], session_id: Optional[str],
                        metadata: Optional[dict[str, Any]], on_event: Emit,
                        version: Optional[str]) -> RunResult:
    _emit = on_event or (lambda _m: None)

    def emit(msg: str) -> None:
        _emit(msg)
        _p({"type": "log", "message": msg})

    _p({"type": "start", "question": question.text, "question_id": question.id,
        "answer_keys": question.answer_keys,
        "config": {"max_scenarios_per_round": cfg.max_scenarios_per_round, "max_rounds": cfg.max_rounds,
                   "concurrency": cfg.concurrency, "model": cfg.model, "effort": cfg.effort},
        "table": ch.settings.qualified_table})
    llm = LLM(cfg.model, cfg.effort)
    meter = DbMeter()
    rounds: list[RoundRecord] = []
    t_start = time.perf_counter()
    stop_reason, error = "error", None
    decision: dict[str, Any] = {}
    grounded: Optional[bool] = None
    trace_id = ""
    all_tags = ["analytics-agentic-speed", *(tags or ["live"])]
    trace_md = {"question_id": question.id, "max_scenarios_per_round": str(cfg.max_scenarios_per_round),
                "max_rounds": str(cfg.max_rounds), "model": cfg.model, "effort": cfg.effort,
                **{k: str(v) for k, v in (metadata or {}).items()}}

    with propagate_attributes(session_id=session_id, tags=all_tags, trace_name="analytics-agentic-speed",
                              metadata=trace_md, version=version):
        with lf.start_as_current_observation(
            as_type="agent", name="analytics-agentic-speed",
            input={"question": question.text, "question_id": question.id,
                   "answer_keys": question.answer_keys,
                   "config": {"max_scenarios_per_round": cfg.max_scenarios_per_round,
                              "max_rounds": cfg.max_rounds, "concurrency": cfg.concurrency,
                              "model": cfg.model, "effort": cfg.effort}},
        ) as root:
            trace_id = root.trace_id
            try:
                emit("profiling the table…")
                t_prof = time.perf_counter()
                _p({"type": "phase", "phase": "profile", "state": "start"})
                schema, prof = await profile(lf, ch, meter, cfg)
                _p({"type": "phase", "phase": "profile", "state": "end",
                    "ms": round((time.perf_counter() - t_prof) * 1000)})
                plan = await _plan(lf, llm, ch, meter, cfg, question, schema, prof, emit)
                analyst_prompt = lf.get_prompt(P.ANALYST, label=cfg.prompt_label, fallback=P.ANALYST_TEXT)
                for r in range(1, cfg.max_rounds + 1):
                    rec = await _run_round(lf, ch, meter, cfg, plan, r, emit)
                    rounds.append(rec)
                    if llm.cost_usd >= cfg.budget_usd:
                        stop_reason = "budget"
                        emit(f"budget cap ${cfg.budget_usd:.2f} reached — stopping")
                        break
                    decision, next_plan = await _analyse(lf, llm, ch, cfg, question, rounds, analyst_prompt, r)
                    _p({"type": "decision", "round": r, "decision": decision.get("decision"),
                        "reasoning": (decision.get("reasoning") or "")[:800]})
                    if decision.get("decision") == "answer":
                        stop_reason = "answered"
                        break
                    if r == cfg.max_rounds or next_plan is None:
                        stop_reason = "max_rounds"
                        break
                    emit(f"refining: {decision.get('reasoning', '')[:160]}")
                    plan = next_plan
                t_ver = time.perf_counter()
                _p({"type": "phase", "phase": "verify", "state": "start"})
                grounded = await _verify(lf, ch, meter, cfg, rounds, decision)
                _p({"type": "phase", "phase": "verify", "state": "end", "grounded": grounded,
                    "ms": round((time.perf_counter() - t_ver) * 1000)})
            except Exception as e:  # noqa: BLE001 - a run must always end with a scored trace
                stop_reason, error = "error", f"{type(e).__name__}: {e}"
                root.update(level="ERROR", status_message=error[:500])
                emit(f"error: {error[:300]}")

            wall_ms = (time.perf_counter() - t_start) * 1000.0
            answer_values = {k: _coerce(v) for k, v in _pairs(decision.get("answer_values", [])).items()}
            best = {k: _coerce(v) for k, v in _pairs(decision.get("best_scenario", [])).items()}
            if stop_reason in ("max_rounds", "budget") and not best and rounds:
                best, answer_values = _fallback_best(rounds[-1], question)
            scen = sum(len(r.outcomes) for r in rounds)
            result = RunResult(
                trace_id=trace_id, question_id=question.id, stop_reason=stop_reason,
                summary=decision.get("summary", "") or "", answer_values=answer_values, best_scenario=best,
                metric_value=decision.get("metric_value") if decision.get("decision") == "answer" else None,
                rounds=len(rounds), scenarios_evaluated=scen, llm_calls=llm.calls,
                db_server_ms_total=meter.total_server_ms, db_wait_ms_total=meter.wait_ms,
                wall_ms=wall_ms, cost_usd=llm.cost_usd, recommendation_grounded=grounded, error=error,
            )
            root.update(output={"summary": result.summary, "answer_values": answer_values,
                                "best_scenario": best, "metric_value": result.metric_value,
                                "stop_reason": stop_reason, "rounds": len(rounds),
                                "scenarios_evaluated": scen, "error": error})
            _score_trace(root, result, meter)
    try:
        result.trace_url = lf.get_trace_url(trace_id=trace_id)
    except Exception:  # noqa: BLE001
        result.trace_url = None
    # Close the per-run Anthropic client inside the loop that opened it; otherwise its
    # connections are collected after the loop closes ("Event loop is closed" noise).
    await llm.client.close()
    _p({"type": "done", "result": {**asdict(result), "db_time_share": result.db_time_share,
                                   "db_p50_ms": meter.p(0.5), "db_p95_ms": meter.p(0.95),
                                   "queries": meter.queries}})
    return result


async def _plan(lf, llm: LLM, ch: ClickHouseClient, meter: DbMeter, cfg: RunConfig, question: Question,
                schema: str, prof: str, emit) -> Plan:
    prompt = lf.get_prompt(P.PLANNER, label=cfg.prompt_label, fallback=P.PLANNER_TEXT)
    system = prompt.compile(table=ch.settings.qualified_table, schema=schema, profile=prof,
                            max_scenarios=str(cfg.max_scenarios_per_round))
    user = f"Question: {question.text}"
    if question.answer_keys:
        user += f"\n(Your final answer will be reported under these keys: {', '.join(question.answer_keys)}.)"
    lookups_done = False
    feedback = ""
    for attempt in range(4):
        data = await _think(llm, lf, name="plan", system=system, user=user + feedback,
                                    schema=PLAN_SCHEMA, prompt=prompt)
        if data.get("lookups") and not lookups_done:
            lookups_done = True
            emit(f"looking up {len(data['lookups'])} facts first…")
            feedback += "\n\nLookup results:\n" + await _lookups(lf, ch, meter, cfg, data["lookups"][:5])
            feedback += "\n\nNow return the plan (no more lookups)."
            continue
        plan = plan_from_json(data)
        try:
            validate(plan, allowed_tables=ch.allowed_tables, max_scenarios=cfg.max_scenarios_per_round)
            return plan
        except PlanError as e:
            emit(f"plan rejected ({e}); asking for a fix")
            feedback += f"\n\nYour previous plan was rejected: {e}\nFix it and return the full plan."
    raise LLMError("planner did not produce a valid plan")


async def _lookups(lf, ch: ClickHouseClient, meter: DbMeter, cfg: RunConfig, sqls: list[str]) -> str:
    with lf.start_as_current_observation(as_type="chain", name="lookups", input={"queries": sqls}) as span:
        t0 = time.perf_counter()
        _p({"type": "phase", "phase": "lookups", "state": "start", "queries": sqls})

        async def run_one(s: str) -> Any:
            try:
                check_lookup(s)  # candidates only: never measure outcomes in a lookup
            except PlanError as e:
                return e
            return await traced_query(lf, ch, s, meter=meter, purpose="lookup", score=cfg.score_each_query,
                                      settings=LOOKUP_SETTINGS, sequential=False)

        res = await gather_bounded([run_one(s) for s in sqls], limit=5)
        rejected = [str(r) for r in res if isinstance(r, PlanError)]
        if rejected:
            span.update(metadata={"rejected_lookups": len(rejected)})
        meter.wait_ms += (time.perf_counter() - t0) * 1000.0
        _p({"type": "phase", "phase": "lookups", "state": "end",
            "ms": round((time.perf_counter() - t0) * 1000),
            "server_ms": [None if isinstance(r, Exception) else round(r.stats.server_ms, 1) for r in res],
            "rejected": [isinstance(r, PlanError) for r in res]})
        out = []
        for s, r in zip(sqls, res):
            if isinstance(r, PlanError):
                out.append(f"-- {s}\nREJECTED (not run): {r}")
            elif isinstance(r, Exception):
                out.append(f"-- {s}\nERROR: {str(r)[:300]}")
            else:
                # One row can still be huge (a groupArray over millions of flights once
                # produced a 1.5M-token prompt), so cap each cell and each lookup.
                rows = "\n".join("\t".join(_cell(v) for v in row) for row in r.rows[:100])
                text = f"-- {s}\n" + "\t".join(r.columns) + "\n" + rows
                if len(text) > LOOKUP_MAX_CHARS:
                    text = text[:LOOKUP_MAX_CHARS] + f"\n… (truncated at {LOOKUP_MAX_CHARS} characters)"
                out.append(text)
        text = "\n\n".join(out)
        span.update(output=text[:5000])
        return text


async def _run_round(lf, ch: ClickHouseClient, meter: DbMeter, cfg: RunConfig, plan: Plan, r: int, emit) -> RoundRecord:
    # The round's input is the plan it executes: what is being measured, over which grid.
    grid = {l.name: {"values": len(l.values), "first": _md_value(l.values[0]) if l.values else None,
                     "last": _md_value(l.values[-1]) if l.values else None} for l in plan.levers}
    with lf.start_as_current_observation(
        as_type="chain", name="round", metadata={"round_index": r},
        input={"round": r, "objective": f"{plan.objective} {plan.metric_name}", "grid": grid,
               "max_scenarios": cfg.max_scenarios_per_round, "reasoning": plan.reasoning[:1000]},
    ) as rspan:
        with lf.start_as_current_observation(as_type="span", name="compile",
                                             input={"sql_template": plan.sql_template,
                                                    "levers": [vars(l) for l in plan.levers]}) as cspan:
            scenarios, truncated = expand(plan, max_scenarios=cfg.max_scenarios_per_round)
            cspan.update(output={"scenarios": len(scenarios), "truncated": truncated})
        emit(f"round {r}: sweeping {len(scenarios)} scenarios ({cfg.concurrency} in flight)…")
        _p({"type": "round_start", "round": r, "n": len(scenarios), "truncated": truncated,
            "levers": [{"name": l.name, "values": [_md_value(v) for v in l.values]} for l in plan.levers],
            "template": plan.sql_template, "metric_name": plan.metric_name, "objective": plan.objective,
            "concurrency": cfg.concurrency})

        async def one(i: int, sc: dict) -> QueryResult:
            # Same query as before; the wrapper only reports each finished scenario live.
            try:
                res = await traced_query(lf, ch, plan.sql_template, sc, meter=meter, purpose="scenario",
                                         score=cfg.score_each_query, scenario=sc, round_index=r,
                                         settings=SCENARIO_SETTINGS, sequential=False)
            except Exception as e:  # noqa: BLE001 - reported, then re-raised into gather
                _p({"type": "query", "round": r, "i": i, "error": str(e)[:200]})
                raise
            o = _outcome(sc, res)
            _p({"type": "query", "round": r, "i": i, "metric": o.metric, "support": o.support,
                "server_ms": round(res.stats.server_ms, 2), "roundtrip_ms": round(res.stats.roundtrip_ms, 1),
                "read_rows": res.stats.read_rows})
            return res

        with lf.start_as_current_observation(as_type="chain", name="sweep",
                                             metadata={"round_index": r, "scenarios": len(scenarios),
                                                       "concurrency": cfg.concurrency},
                                             input={"sql_template": plan.sql_template,
                                                    "scenarios": len(scenarios),
                                                    "concurrency": cfg.concurrency,
                                                    "first_scenario": scenarios[0] if scenarios else None,
                                                    "last_scenario": scenarios[-1] if scenarios else None}) as sspan:
            t0 = time.perf_counter()
            results = await gather_bounded([one(i, sc) for i, sc in enumerate(scenarios)],
                                           limit=cfg.concurrency)
            sweep_ms = (time.perf_counter() - t0) * 1000.0
            meter.wait_ms += sweep_ms
            outcomes = [_outcome(sc, res) for sc, res in zip(scenarios, results)]
            server = [o.server_ms for o in outcomes if o.server_ms is not None]
            sspan.update(output={"ok": sum(1 for o in outcomes if o.error is None),
                                 "errors": sum(1 for o in outcomes if o.error),
                                 "sweep_wall_ms": round(sweep_ms, 1)})
        rec = RoundRecord(index=r, plan=plan, outcomes=outcomes, sweep_wall_ms=sweep_ms, truncated=truncated)
        p50, p95 = percentile(server, 0.5), percentile(server, 0.95)
        rspan.update(output={"scenarios": len(outcomes), "with_metric": len(rec.ok),
                             "sweep_wall_ms": round(sweep_ms, 1), "server_p50_ms": round(p50, 1),
                             "server_p95_ms": round(p95, 1)})
        for name, val in (("round_scenarios", len(outcomes)), ("round_db_server_ms_total", sum(server)),
                          ("round_db_p95_ms", p95), ("round_sweep_wall_ms", sweep_ms)):
            rspan.score(name=name, value=round(float(val), 2), data_type="NUMERIC")
        emit(f"round {r}: {len(outcomes)} scenarios in {sweep_ms/1000:.2f}s wall · "
             f"server p50 {p50:.0f} ms, p95 {p95:.0f} ms · {sum(server)/1000:.2f}s total DB time")
        _p({"type": "round_end", "round": r, "n": len(outcomes), "with_metric": len(rec.ok),
            "errors": sum(1 for o in outcomes if o.error), "sweep_wall_ms": round(sweep_ms, 1),
            "server_p50_ms": round(p50, 1), "server_p95_ms": round(p95, 1),
            "server_ms_total": round(sum(server), 1)})
        return rec


def _outcome(sc: dict, res: Any) -> ScenarioOutcome:
    if isinstance(res, Exception):
        return ScenarioOutcome(sc, None, None, None, None, None, None, error=str(res)[:500])
    row = res.first() or {}
    metric = row.get("metric")
    try:
        metric = None if metric is None else float(metric)
        if metric is not None and (math.isnan(metric) or math.isinf(metric)):
            metric = None
    except (TypeError, ValueError):
        metric = None
    support = row.get("support")
    return ScenarioOutcome(sc, metric, int(support) if isinstance(support, (int, float)) else None, row,
                           res.stats.server_ms, res.stats.roundtrip_ms, res.stats.read_rows)


async def _analyse(lf, llm: LLM, ch: ClickHouseClient, cfg: RunConfig, question: Question,
                   rounds: list[RoundRecord], prompt: Any, r: int) -> tuple[dict, Optional[Plan]]:
    final = r == cfg.max_rounds
    system = prompt.compile(
        table=ch.settings.qualified_table,
        round=str(r), max_rounds=str(cfg.max_rounds), max_scenarios=str(cfg.max_scenarios_per_round),
        answer_keys=", ".join(question.answer_keys) or "(free-form: use the lever names)",
        final_round_note="This is the FINAL round: you must answer now." if final else "")
    history = "\n\n".join(_results_table(rec, top=8, bottom=0) for rec in rounds[:-1])
    user = f"Question: {question.text}\n\n"
    if history:
        user += f"Earlier rounds (best results only):\n{history}\n\n"
    user += "Latest round:\n" + _results_table(rounds[-1])
    feedback = ""
    for _ in range(3):
        data = await _think(llm, lf, name="analyse", system=system, user=user + feedback,
                                    schema=DECISION_SCHEMA, prompt=prompt)
        if data.get("decision") == "answer" or final:
            return data, None
        plan = plan_from_json(data)
        try:
            validate(plan, allowed_tables=ch.allowed_tables, max_scenarios=cfg.max_scenarios_per_round)
            return data, plan
        except PlanError as e:
            feedback = f"\n\nYour refine plan was rejected: {e}\nFix it, or answer instead."
    return data, None


async def _verify(lf, ch: ClickHouseClient, meter: DbMeter, cfg: RunConfig, rounds: list[RoundRecord],
                  decision: dict) -> Optional[bool]:
    """Re-run the scenario the answer names and compare the number it states."""
    if decision.get("decision") != "answer" or not rounds:
        return None
    named = {k: _coerce(v) for k, v in _pairs(decision.get("best_scenario", [])).items()}
    claimed = decision.get("metric_value")
    with lf.start_as_current_observation(as_type="chain", name="verify",
                                         input={"best_scenario": named, "claimed_metric": claimed}) as span:
        for rec in reversed(rounds):
            by_key = {scenario_key(o.scenario): o for o in rec.outcomes}
            hit = by_key.get(scenario_key(named))
            if hit is None:
                continue
            if hit.metric is None or claimed is None:
                # An answer resting on a NULL metric (no data, or below minimum support)
                # is never grounded, whatever a re-run returns.
                span.update(output={"found_in_round": rec.index, "grounded": False,
                                    "note": "the named scenario has no metric"})
                return False
            res = await traced_query(lf, ch, rec.plan.sql_template, hit.scenario, meter=meter,
                                     purpose="verify", score=cfg.score_each_query, scenario=hit.scenario,
                                     round_index=rec.index, settings=SCENARIO_SETTINGS)
            rerun = (res.first() or {}).get("metric")
            ok = (rerun is not None and claimed is not None
                  and math.isclose(float(rerun), float(claimed), rel_tol=5e-3, abs_tol=1e-9))
            span.update(output={"found_in_round": rec.index, "rerun_metric": rerun, "grounded": ok})
            return ok
        span.update(output={"found_in_round": None, "grounded": False,
                            "note": "the named scenario is not in any round's results"})
        return False


def _fallback_best(rec: RoundRecord, question: Question) -> tuple[dict, dict]:
    ok = rec.ok
    if not ok:
        return {}, {}
    best = (max if rec.plan.objective == "maximize" else min)(ok, key=lambda o: o.metric)
    vals = {k: (v if not isinstance(v, list) else ",".join(v)) for k, v in best.scenario.items()}
    answer = {k: vals[k] for k in question.answer_keys if k in vals}
    return vals, answer


def _score_trace(root: Any, res: RunResult, meter: DbMeter) -> None:
    minutes = res.wall_ms / 60000.0 if res.wall_ms else 0.0
    numeric = {
        "scenarios_evaluated": res.scenarios_evaluated,
        "db_server_ms_total": res.db_server_ms_total,
        "db_p95_ms": meter.p(0.95),
        "db_wait_ms_total": res.db_wait_ms_total,
        "db_time_share": res.db_time_share,
        "scenarios_per_minute": res.scenarios_evaluated / minutes if minutes else 0.0,
        "llm_calls": res.llm_calls,
        "rounds": res.rounds,
        "run_cost_usd": res.cost_usd,
    }
    for name, val in numeric.items():
        root.score_trace(name=name, value=round(float(val), 4), data_type="NUMERIC")
    root.score_trace(name="stop_reason", value=res.stop_reason, data_type="CATEGORICAL")
    if res.recommendation_grounded is not None:
        root.score_trace(name="recommendation_grounded", value=1.0 if res.recommendation_grounded else 0.0,
                         data_type="BOOLEAN")

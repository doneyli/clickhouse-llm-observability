"""Synthetic trace generator - builds backfilled traces via Langfuse batch ingestion API."""

from __future__ import annotations

import random
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from langfuse.api.commons.types.score_config_data_type import ScoreConfigDataType
from langfuse.api.ingestion.types.create_generation_body import CreateGenerationBody
from langfuse.api.ingestion.types.create_span_body import CreateSpanBody
from langfuse.api.ingestion.types.ingestion_event import (
    IngestionEvent_GenerationCreate,
    IngestionEvent_ScoreCreate,
    IngestionEvent_SpanCreate,
    IngestionEvent_TraceCreate,
)
from langfuse.api.ingestion.types.score_body import ScoreBody
from langfuse.api.ingestion.types.trace_body import TraceBody

from src.config import load_config
from src.synthetic.distributions import compute_cost_details, sample_latency, sample_tokens
from src.synthetic.query_templates import (
    build_promo_planner_input,
    build_promo_planner_output,
    build_simple_agent_input,
    build_simple_agent_output,
)

_BATCH_SIZE = 300  # events per ingestion.batch() call


# Deterministic ids for an owned history batch. When a trace id is given, every
# observation and score id inside that trace is drawn from an RNG seeded by it, so
# re-ingesting the same batch upserts the same rows instead of appending new ones.
_ID_RNG: random.Random | None = None
# Day the batch's timestamps count back from. None means "now" (the old behaviour).
_ANCHOR: datetime | None = None
# Version stamped on every trace and observation of an owned batch, so a read-back can
# count that batch's rows apart from any other data (src/synthetic/history_batch.py).
_VERSION: str | None = None
# Config pinned for one batch: the builders would otherwise re-read the YAML per trace.
_CFG: Any = None


def _cfg() -> Any:
    return _CFG or load_config()


def _uid() -> str:
    if _ID_RNG is not None:
        return str(uuid.UUID(int=_ID_RNG.getrandbits(128), version=4))
    return str(uuid.uuid4())


def _eid() -> str:
    """Ingestion envelope id. Always random: the server may drop an envelope id it has seen,
    which would turn a retry of a failed batch into a silent no-op."""
    return str(uuid.uuid4())


def _start_trace(trace_id: str | None) -> str:
    """The trace id to use, switching observation ids to deterministic when one is given."""
    global _ID_RNG
    if trace_id is None:
        _ID_RNG = None
        return _uid()
    _ID_RNG = random.Random(trace_id)
    return trace_id


def _ts(dt: datetime) -> str:
    return dt.isoformat()


def _now_ts() -> str:
    return _ts(datetime.now(UTC))


def _ms(ms: int) -> timedelta:
    return timedelta(milliseconds=ms)


def _mk_trace(
    trace_id: str,
    t: datetime,
    name: str,
    inp: Any,
    out: Any,
    metadata: dict,
    tags: list,
    user_id: str,
    session_id: str,
) -> IngestionEvent_TraceCreate:
    return IngestionEvent_TraceCreate(
        type="trace-create",
        id=_eid(),
        timestamp=_now_ts(),
        body=TraceBody(
            id=trace_id,
            timestamp=_ts(t),
            name=name,
            input=inp,
            output=out,
            metadata=metadata,
            tags=tags,
            user_id=user_id,
            session_id=session_id,
            version=_VERSION,
        ),
    )


def _mk_span(
    trace_id: str,
    span_id: str,
    parent_id: str | None,
    name: str,
    start: datetime,
    end: datetime,
    metadata: dict | None = None,
    inp: Any = None,
    out: Any = None,
) -> IngestionEvent_SpanCreate:
    return IngestionEvent_SpanCreate(
        type="span-create",
        id=_eid(),
        timestamp=_now_ts(),
        body=CreateSpanBody(
            trace_id=trace_id,
            id=span_id,
            parent_observation_id=parent_id,
            name=name,
            start_time=_ts(start),
            end_time=_ts(end),
            metadata=metadata,
            input=inp,
            output=out,
            version=_VERSION,
        ),
    )


def _mk_gen(
    trace_id: str,
    gen_id: str,
    parent_id: str | None,
    name: str,
    model: str,
    inp: Any,
    out: Any,
    usage_in: int,
    usage_out: int,
    start: datetime,
    end: datetime,
    metadata: dict | None = None,
) -> IngestionEvent_GenerationCreate:
    return IngestionEvent_GenerationCreate(
        type="generation-create",
        id=_eid(),
        timestamp=_now_ts(),
        body=CreateGenerationBody(
            trace_id=trace_id,
            id=gen_id,
            parent_observation_id=parent_id,
            name=name,
            model=model,
            input=inp,
            output=out,
            usage_details={"input": usage_in, "output": usage_out},
            cost_details=compute_cost_details(model, usage_in, usage_out),
            start_time=_ts(start),
            end_time=_ts(end),
            metadata=metadata,
            version=_VERSION,
        ),
    )


def _mk_score(trace_id: str, name: str, value: float, comment: str) -> IngestionEvent_ScoreCreate:
    return IngestionEvent_ScoreCreate(
        type="score-create",
        id=_eid(),
        timestamp=_now_ts(),
        body=ScoreBody(
            id=_uid(),
            trace_id=trace_id,
            name=name,
            value=value,
            data_type=ScoreConfigDataType.NUMERIC,
            comment=comment,
        ),
    )


def _pick_failure_mode(rng: random.Random) -> str | None:
    cfg = _cfg()
    dist = cfg.synthetic_history.failure_mode_distribution
    roll = rng.random()
    cumulative = 0.0
    for mode, prob in dist.items():
        cumulative += prob
        if roll < cumulative:
            return mode
    return None


def _business_hours_timestamp(
    days_back: int,
    rng: random.Random,
    business_hours: bool = True,
) -> datetime:
    now = _ANCHOR or datetime.now(tz=UTC)
    day_offset = rng.randint(0, days_back - 1)
    base = now - timedelta(days=day_offset)
    if business_hours and rng.random() < 0.75:
        hour = rng.randint(9, 17)
    else:
        hour = rng.randint(0, 23)
    return base.replace(
        hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0
    )


def _build_promo_planner_events(
    rng: random.Random,
    days_back: int,
    business_hours: bool,
    trace_id: str | None = None,
) -> list[Any]:
    """Build a full PromoPlanner trace event list (no LLM calls)."""
    cfg = _cfg()
    failure_mode = _pick_failure_mode(rng)
    start_ts = _business_hours_timestamp(days_back, rng, business_hours)
    t = start_ts
    user_input = build_promo_planner_input(rng, cfg)
    final_output = build_promo_planner_output(rng, failure_mode, cfg)

    trace_id = _start_trace(trace_id)
    events: list[Any] = []

    # classify_intent generation
    ci_lat = sample_latency("classify_intent", rng)
    ci_in, ci_out = sample_tokens("classify_intent", rng)
    events.append(
        _mk_gen(
            trace_id, _uid(), None, "classify_intent", cfg.llm.models.orchestrator,
            {"prompt": user_input[:200]},
            {"intent": "plan_promo", "rationale": "User requesting promotional plan"},
            ci_in, ci_out, t, t + _ms(ci_lat),
        )
    )
    t += _ms(ci_lat)

    # --- research_crew ---
    research_start = t
    research_id = _uid()

    # data_analyst
    da_start = t
    da_id = _uid()

    sales_lat = sample_latency(
        "timeout_tool" if failure_mode == "sales_api_timeout" else "tool_call", rng
    )
    events.append(
        _mk_span(trace_id, _uid(), da_id, "tool.query_sales", t, t + _ms(sales_lat),
                 {"tool.outcome": "timeout" if failure_mode == "sales_api_timeout" else "ok"})
    )
    t += _ms(sales_lat)

    inv_lat = sample_latency("tool_call", rng)
    events.append(_mk_span(trace_id, _uid(), da_id, "tool.query_inventory", t, t + _ms(inv_lat), {"tool.outcome": "ok"}))
    t += _ms(inv_lat)

    da_sum_lat = sample_latency("sonnet_generation", rng)
    da_in, da_out = sample_tokens("research_summarize", rng)
    events.append(
        _mk_gen(trace_id, _uid(), da_id, "generation.summarize", cfg.llm.models.research_crew,
                {"context": "sales data"}, {"summary": "Sales data analyzed."}, da_in, da_out, t, t + _ms(da_sum_lat))
    )
    t += _ms(da_sum_lat)
    events.append(_mk_span(trace_id, da_id, research_id, "data_analyst", da_start, t))

    # market_researcher
    mr_start = t
    mr_id = _uid()

    mkt_lat = sample_latency("tool_call", rng)
    events.append(_mk_span(trace_id, _uid(), mr_id, "tool.market_trends", t, t + _ms(mkt_lat), {"tool.outcome": "ok"}))
    t += _ms(mkt_lat)

    mr_sum_lat = sample_latency("sonnet_generation", rng)
    mr_in, mr_out = sample_tokens("market_summarize", rng)
    events.append(
        _mk_gen(trace_id, _uid(), mr_id, "generation.summarize", cfg.llm.models.research_crew,
                {"context": "market data"}, {"summary": "Market trends summarized."}, mr_in, mr_out, t, t + _ms(mr_sum_lat))
    )
    t += _ms(mr_sum_lat)
    events.append(_mk_span(trace_id, mr_id, research_id, "market_researcher", mr_start, t))

    # historian
    hist_start = t
    hist_id = _uid()

    hist_tool_lat = sample_latency("tool_call", rng)
    events.append(
        _mk_span(trace_id, _uid(), hist_id, "tool.query_historical_promos", t, t + _ms(hist_tool_lat), {"tool.outcome": "ok"})
    )
    t += _ms(hist_tool_lat)

    hist_sum_lat = sample_latency("sonnet_generation", rng)
    hist_in, hist_out = sample_tokens("history_summarize", rng)
    events.append(
        _mk_gen(trace_id, _uid(), hist_id, "generation.summarize", cfg.llm.models.research_crew,
                {"context": "historical promos"}, {"summary": "Historical promo patterns identified."},
                hist_in, hist_out, t, t + _ms(hist_sum_lat))
    )
    t += _ms(hist_sum_lat)
    events.append(_mk_span(trace_id, hist_id, research_id, "historian", hist_start, t))

    events.append(
        _mk_span(trace_id, research_id, None, "research_crew", research_start, t,
                 {"crew": "research"}, {"query": user_input[:100]})
    )

    # --- strategy_crew ---
    strat_start = t
    strat_id = _uid()

    ps_start = t
    ps_id = _uid()

    ps_lat = sample_latency("opus_generation", rng)
    ps_in, ps_out = sample_tokens("generate_options", rng)
    events.append(
        _mk_gen(
            trace_id, _uid(), ps_id, "generation.generate_options", cfg.llm.models.strategy_crew,
            {"brief": "research package"}, {"options": "2-3 promo options generated"},
            ps_in, ps_out, t, t + _ms(ps_lat),
            {"iterations": 5 if failure_mode == "crew_max_iterations" else rng.randint(1, 3)},
        )
    )
    t += _ms(ps_lat)
    events.append(_mk_span(trace_id, ps_id, strat_id, "promo_strategist", ps_start, t))

    le_start = t
    le_id = _uid()

    le_lat = sample_latency("opus_generation", rng)
    le_in, le_out = sample_tokens("estimate_lift", rng)
    events.append(
        _mk_gen(trace_id, _uid(), le_id, "generation.estimate", cfg.llm.models.strategy_crew,
                {"options": "3 options"}, {"estimates": "lift estimates with confidence"},
                le_in, le_out, t, t + _ms(le_lat))
    )
    t += _ms(le_lat)
    events.append(_mk_span(trace_id, le_id, strat_id, "lift_estimator", le_start, t))

    strat_meta = {"crew": "strategy"}
    if failure_mode == "crew_max_iterations":
        strat_meta["terminated_reason"] = "max_iter"
    events.append(
        _mk_span(trace_id, strat_id, None, "strategy_crew", strat_start, t,
                 strat_meta, {"research_summary": "Research complete"})
    )

    # --- compliance_agent ---
    comp_start = t
    comp_id = _uid()

    bg_lat = sample_latency("haiku_generation", rng)
    bg_in, bg_out = sample_tokens("compliance_check", rng)
    comp_findings = (
        [{"severity": "HIGH", "rule": "Rule 8: Marketing to children under 12 requires legal review"}]
        if failure_mode == "compliance_rejection"
        else []
    )
    events.append(
        _mk_gen(trace_id, _uid(), comp_id, "check_brand_guidelines", cfg.llm.models.compliance,
                {"brief": "strategy brief"}, {"findings": comp_findings}, bg_in, bg_out, t, t + _ms(bg_lat))
    )
    t += _ms(bg_lat)

    reg_lat = sample_latency("haiku_generation", rng)
    reg_in, reg_out = sample_tokens("compliance_check", rng)
    events.append(
        _mk_gen(trace_id, _uid(), comp_id, "check_regulatory", cfg.llm.models.compliance,
                {"brief": "strategy brief"}, {"findings": []}, reg_in, reg_out, t, t + _ms(reg_lat))
    )
    t += _ms(reg_lat)

    comp_status = "REJECTED" if failure_mode == "compliance_rejection" else "APPROVED"
    events.append(
        _mk_span(trace_id, comp_id, None, "compliance_agent", comp_start, t, {"compliance_status": comp_status})
    )

    # compose_brief generation
    cb_lat = sample_latency("sonnet_generation", rng)
    cb_in, cb_out = sample_tokens("compose_brief", rng)
    events.append(
        _mk_gen(trace_id, _uid(), None, "generation.compose_brief", cfg.llm.models.orchestrator,
                {"context": "all crew outputs"}, {"brief": final_output}, cb_in, cb_out, t, t + _ms(cb_lat))
    )
    t += _ms(cb_lat)

    # root trace inserted first with the correct backdated start_ts
    events.insert(
        0,
        _mk_trace(
            trace_id, start_ts,
            "promo_planner_run",
            {"query": user_input}, {"brief": final_output},
            {
                "agent_name": cfg.agent_fleet.hero_agent.name,
                "failure_mode": failure_mode or "none",
                "customer": cfg.customer.display_name,
                "intent": "plan_promo",
            },
            ["synthetic", "PromoPlanner"],
            f"demo-user-{rng.randint(1, 20)}",
            f"session-{rng.randint(1, 500)}",
        ),
    )

    # Attach LLM-as-judge scores to ~10% of PromoPlanner traces (all 4 dimensions)
    if rng.random() < 0.10:
        is_failure = failure_mode in ("hallucinated_sku", "compliance_rejection", "crew_max_iterations")
        # Each dimension has its own realistic distribution depending on failure mode
        factuality_val = round(rng.uniform(0.3, 0.6), 2) if failure_mode == "hallucinated_sku" else round(rng.uniform(0.8, 1.0), 2)
        compliance_val = round(rng.uniform(0.1, 0.4), 2) if failure_mode == "compliance_rejection" else round(rng.uniform(0.75, 1.0), 2)
        tool_val = round(rng.uniform(0.4, 0.7), 2) if failure_mode == "crew_max_iterations" else round(rng.uniform(0.8, 1.0), 2)
        quality_val = round(rng.uniform(0.3, 0.6), 2) if is_failure else round(rng.uniform(0.7, 1.0), 2)

        events.append(_mk_score(trace_id, "response-factuality", factuality_val, "Auto-scored by LLM-as-judge"))
        events.append(_mk_score(trace_id, "compliance-adherence", compliance_val, "Auto-scored by LLM-as-judge"))
        events.append(_mk_score(trace_id, "tool-call-correctness", tool_val, "Auto-scored by LLM-as-judge"))
        events.append(_mk_score(trace_id, "brief-quality", quality_val, "Auto-scored by LLM-as-judge"))

    return events


def _build_simple_agent_events(
    agent_name: str,
    model: str,
    rng: random.Random,
    days_back: int,
    business_hours: bool,
    trace_id: str | None = None,
) -> list[Any]:
    """Build a simple 2-3 span trace for a fleet agent."""
    cfg = _cfg()
    failure_mode = _pick_failure_mode(rng)
    start_ts = _business_hours_timestamp(days_back, rng, business_hours)
    t = start_ts
    user_input = build_simple_agent_input(agent_name, rng, cfg)
    output = build_simple_agent_output(agent_name, rng)

    trace_id = _start_trace(trace_id)
    events: list[Any] = []

    tool_names = {
        "CustomerCareBot": "tool.retrieve",
        "SupplyChainPlanner": "tool.query_inventory",
        "ShelfImageAnalyzer": "tool.vision_call",
        "InternalKBSearch": "tool.retrieve",
        "PepGPT": "tool.retrieve",
        "FinanceCloseBot": "tool.sql_query",
    }
    tool_name = tool_names.get(agent_name, "tool.call")

    tool_lat = sample_latency("tool_call", rng)
    tool_outcome = "error" if failure_mode == "tool_error" else "ok"
    events.append(
        _mk_span(trace_id, _uid(), None, tool_name, t, t + _ms(tool_lat), {"tool.outcome": tool_outcome})
    )
    t += _ms(tool_lat)

    if agent_name == "SupplyChainPlanner":
        opt_lat = sample_latency("tool_call", rng)
        events.append(
            _mk_span(trace_id, _uid(), None, "tool.optimize", t, t + _ms(opt_lat), {"tool.outcome": "ok"})
        )
        t += _ms(opt_lat)

    gen_lat = sample_latency("sonnet_generation", rng)
    in_tok, out_tok = sample_tokens("research_summarize", rng)
    events.append(
        _mk_gen(trace_id, _uid(), None, "generation", model,
                {"context": user_input[:100]}, {"response": output}, in_tok, out_tok, t, t + _ms(gen_lat))
    )
    t += _ms(gen_lat)

    events.insert(
        0,
        _mk_trace(
            trace_id, start_ts,
            f"{agent_name.lower()}_run",
            {"query": user_input}, {"response": output},
            {
                "agent_name": agent_name,
                "customer": cfg.customer.display_name,
                "failure_mode": failure_mode or "none",
            },
            ["synthetic", agent_name],
            f"demo-user-{rng.randint(1, 50)}",
            f"session-{rng.randint(1, 1000)}",
        ),
    )

    return events


def iter_batch(
    total: int,
    hero_share: float,
    days_back: int,
    business_hours: bool,
    seed: int = 42,
    anchor: datetime | None = None,
    trace_id_for: Any = None,
    version: str | None = None,
    cfg: Any = None,
) -> Iterator[dict[str, Any]]:
    """Yield every trace of a batch in order, without sending anything.

    Each item is {"index", "agent", "events"}, or {"index", "agent", "error"} when a builder
    raised. With `anchor`, `trace_id_for(index)` and the same config the batch is fully
    deterministic, so it can be planned (counted) first and sent second, and re-sending it
    upserts the same rows. `version` is stamped on every trace and observation.
    """
    global _ANCHOR, _ID_RNG, _VERSION, _CFG
    cfg = cfg or load_config()
    _ANCHOR, _VERSION, _CFG = anchor, version, cfg
    try:
        rng = random.Random(seed)
        fleet_agents = cfg.agent_fleet.other_agents
        fleet_names = [a.name for a in fleet_agents]
        fleet_weights = [a.trace_share for a in fleet_agents]
        total_fleet = sum(fleet_weights)
        fleet_weights = [w / total_fleet for w in fleet_weights]
        hero_count = int(total * hero_share)

        for i in range(hero_count):
            try:
                tid = trace_id_for(i) if trace_id_for else None
                events = _build_promo_planner_events(rng, days_back, business_hours, trace_id=tid)
                yield {"index": i, "agent": "PromoPlanner", "events": events}
            except Exception as e:
                yield {"index": i, "agent": "PromoPlanner", "error": str(e)}

        for i in range(total - hero_count):
            agent = rng.choices(fleet_names, weights=fleet_weights, k=1)[0]
            model = cfg.llm.models.research_crew
            try:
                tid = trace_id_for(hero_count + i) if trace_id_for else None
                events = _build_simple_agent_events(agent, model, rng, days_back, business_hours, trace_id=tid)
                yield {"index": hero_count + i, "agent": agent, "events": events}
            except Exception as e:
                yield {"index": hero_count + i, "agent": agent, "error": str(e)}
    finally:
        _ANCHOR, _ID_RNG, _VERSION, _CFG = None, None, None, None


def batch_counts(items: Any) -> dict[str, Any]:
    """What a batch contains, counted the way history_batch.read_counts reads it back:
    observations per (trace name, observation name), scores per name, traces, and the
    builder errors (a batch with errors cannot be complete)."""
    obs: Counter = Counter()
    scores: Counter = Counter()
    traces, errors = 0, []
    for item in items:
        if "error" in item:
            errors.append(item)
            continue
        trace_name = None
        for e in item["events"]:
            if e.type == "trace-create":
                traces += 1
                trace_name = e.body.name
            elif e.type in ("span-create", "generation-create"):
                obs[(trace_name, e.body.name)] += 1
            elif e.type == "score-create":
                scores[e.body.name] += 1
    return {"observations": dict(obs), "scores": dict(scores), "traces": traces, "errors": errors}


def generate_traces(
    total: int,
    hero_share: float,
    days_back: int,
    business_hours: bool,
    seed: int = 42,
    anchor: datetime | None = None,
    trace_id_for: Any = None,
    version: str | None = None,
    cfg: Any = None,
    ingest: Any = None,
) -> list[dict[str, Any]]:
    """Generate and ingest synthetic traces via Langfuse batch ingestion (no LLM calls).

    The traces come from `iter_batch` (see there for determinism and the version stamp).
    `ingest(chunk)` sends one chunk of events and returns its per-event errors; by default
    it is the SDK's `ingestion.batch`. Returns one result per trace, plus one per error.

    Callers must ensure LANGFUSE_* env vars are set before calling this function.
    Call load_env() at module/script level, not inside this function.
    """
    if ingest is None:
        from langfuse import Langfuse

        lf = Langfuse()

        def ingest(chunk: list[Any]) -> list[Any]:
            return list(lf.api.ingestion.batch(batch=chunk).errors or [])

    results: list[dict[str, Any]] = []
    pending_events: list[Any] = []

    def flush_pending() -> None:
        if not pending_events:
            return
        # Send in sub-batches to avoid request size limits
        for i in range(0, len(pending_events), _BATCH_SIZE):
            chunk = pending_events[i : i + _BATCH_SIZE]
            try:
                for err in ingest(chunk):
                    results.append({"error": str(err), "agent": "batch"})
            except Exception as e:
                results.append({"error": str(e), "agent": "batch_flush"})
        pending_events.clear()

    from rich.progress import Progress

    with Progress() as progress:
        task = progress.add_task("[cyan]Generating traces...", total=total)
        for item in iter_batch(total, hero_share, days_back, business_hours, seed=seed, anchor=anchor,
                               trace_id_for=trace_id_for, version=version, cfg=cfg):
            if "error" in item:
                results.append({"error": item["error"], "agent": item["agent"]})
            else:
                pending_events.extend(item["events"])
                results.append({"agent": item["agent"]}
                               | ({"failure_mode": "synthetic"} if item["agent"] == "PromoPlanner" else {}))
            progress.advance(task)
            if len(pending_events) >= _BATCH_SIZE * 5:
                flush_pending()

    flush_pending()
    return results

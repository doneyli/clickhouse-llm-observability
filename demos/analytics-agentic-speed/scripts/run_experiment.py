#!/usr/bin/env python
"""Experiments over the `scenario-explorer-questions` dataset.

Experiment A, sweep breadth (the hero): the same agent, prompt and model with
``max_scenarios_per_round`` = 10 (narrow) vs 300 (wide), both with ``max_rounds`` = 2
(``--max-rounds`` overrides it for both). Rounds are held fixed because a round is a
model call; with unlimited rounds a narrow agent bisects instead, which measures
patience, not breadth (see SPEC §6, as built).

    .venv/bin/python scripts/run_experiment.py --breadth both
    .venv/bin/python scripts/run_experiment.py --breadth wide --limit 3

Experiment B, the usual comparison: two prompt labels or two models, at one breadth.

    .venv/bin/python scripts/run_experiment.py --compare prompt-labels production,candidate
    .venv/bin/python scripts/run_experiment.py --compare models claude-opus-5-5,claude-sonnet-5-5

    .venv/bin/python scripts/run_experiment.py --dry-run [--question q-padding]

``--dry-run`` runs ONE question through the agent with Langfuse tracing disabled (no
traces, scores or dataset runs are written), grades it against the reference SQL
computed live, and prints the result.

Every run gets a unique run name (``breadth-10-<UTC timestamp>``): reusing a name would
append to the old dataset run. The task never raises (the SDK silently drops items whose
task raises): a failed run comes back as an output with ``error`` set and is scored as
incorrect. After each run the script reads the run back from Langfuse and fails unless
every dataset item is an experiment item with an ``answer_correct`` score.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from explorer.ch_client import ClickHouseClient  # noqa: E402
from explorer.config import ClickHouseSettings, RunConfig, load_env  # noqa: E402
from explorer.evaluators import (  # noqa: E402
    ITEM_EVALUATORS,
    PASS_THROUGH_METRICS,
    RUN_EVALUATORS,
    answer_correct,
    answer_key_match,
    load_questions,
    summarize,
)
from explorer.models import Question  # noqa: E402

DATASET_NAME = "scenario-explorer-questions"
BREADTH = {"narrow": 10, "wide": 300}
EXPERIMENT_A_ROUNDS = 2  # same for both breadths: rounds are the model's time, scenarios are ClickHouse's
DEFAULT_ITEM_TIMEOUT_S = 900.0

RunFn = Callable[..., Awaitable[Any]]


@dataclass
class Variant:
    key: str  # run-name prefix and trace tag, e.g. "breadth-10"
    experiment: str  # experiment name (groups the runs of one comparison)
    description: str
    cfg: RunConfig


# ----------------------------------------------------------------------- variants
def build_variants(args: argparse.Namespace, base: RunConfig) -> list[Variant]:
    base = replace(base, max_rounds=args.max_rounds) if args.max_rounds else base
    if args.compare:
        kind, values = args.compare
        breadth = args.breadth or "wide"
        if breadth == "both":
            raise SystemExit("--compare runs at one breadth: pass --breadth narrow or --breadth wide")
        cfg = replace(base, max_scenarios_per_round=BREADTH[breadth])
        variants = []
        for v in values:
            if kind == "models":
                vcfg = replace(cfg, model=v)
                key = f"model-{v}"
            else:
                vcfg = replace(cfg, prompt_label=v)
                key = f"prompt-{v}"
            variants.append(Variant(
                key=key, cfg=vcfg,
                experiment=f"Experiment B: {kind} ({', '.join(values)})",
                description=(f"Experiment B, {kind}: {key} at {vcfg.max_scenarios_per_round} scenarios/round, "
                             f"max_rounds={vcfg.max_rounds}, model={vcfg.model}, prompt_label={vcfg.prompt_label}"),
            ))
        return variants

    breadths = ["narrow", "wide"] if (args.breadth or "both") == "both" else [args.breadth]
    # Hold the round budget fixed and small for both breadths. A round costs one model call
    # (~10-25 s); a scenario costs milliseconds. With unlimited rounds a narrow agent just
    # spends more model calls bisecting, which tests patience, not breadth.
    base = replace(base, max_rounds=args.max_rounds or EXPERIMENT_A_ROUNDS)
    variants = []
    for b in breadths:
        vcfg = replace(base, max_scenarios_per_round=BREADTH[b])
        variants.append(Variant(
            key=f"breadth-{BREADTH[b]}", cfg=vcfg,
            experiment="Experiment A: sweep breadth",
            description=(f"Experiment A, sweep breadth: {BREADTH[b]} scenarios/round ({b}), "
                         f"max_rounds={vcfg.max_rounds}, model={vcfg.model}, prompt_label={vcfg.prompt_label}"),
        ))
    return variants


def parse_compare(kind: str, values: str) -> tuple[str, list[str]]:
    """('models', 'A,B') -> ('models', ['A', 'B'])."""
    if kind not in ("models", "prompt-labels"):
        raise SystemExit("--compare takes 'prompt-labels A,B' or 'models A,B'")
    vals = [v.strip() for v in values.split(",") if v.strip()]
    if len(vals) != 2 or vals[0] == vals[1]:
        raise SystemExit("--compare needs two distinct values, e.g. --compare models A,B")
    return kind, vals


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ----------------------------------------------------------------------- the task
def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def question_from_item(item: Any) -> Question:
    inp = _field(item, "input") or {}
    if not isinstance(inp, Mapping) or "question" not in inp:
        raise ValueError(f"dataset item input has no 'question': {inp!r}")
    # Only what the agent may see: no reference SQL, no expected answer.
    return Question(id=str(inp.get("question_id") or _field(item, "id") or "unknown"),
                    text=str(inp["question"]), answer_keys=list(inp.get("answer_keys") or []))


def task_output(res: Any, cfg: RunConfig) -> dict[str, Any]:
    """JSON-safe output of one run: what the evaluators and the compare view read."""
    return {
        "question_id": res.question_id,
        "answer_values": dict(res.answer_values or {}),
        "summary": res.summary,
        "summary_evidence": dict(res.summary_evidence),
        "stop_reason": res.stop_reason,
        "best_scenario": dict(res.best_scenario or {}),
        "metric_value": res.metric_value,
        "rounds": res.rounds,
        "scenarios_evaluated": res.scenarios_evaluated,
        "llm_calls": res.llm_calls,
        "db_server_ms_total": round(res.db_server_ms_total, 2),
        "db_wait_ms_total": round(res.db_wait_ms_total, 2),
        "db_time_share": round(res.db_time_share, 6),
        "wall_ms": round(res.wall_ms, 1),
        "run_cost_usd": round(res.cost_usd, 6) if res.cost_usd is not None else None,
        "recommendation_grounded": res.recommendation_grounded,
        "max_scenarios_per_round": cfg.max_scenarios_per_round,
        "model": cfg.model,
        "prompt_label": cfg.prompt_label,
        "agent_trace_id": res.trace_id,
        "error": res.error,
    }


def error_output(question_id: str, error: str, wall_ms: float, cfg: RunConfig) -> dict[str, Any]:
    return {"question_id": question_id, "answer_values": {}, "stop_reason": "error",
            "wall_ms": round(wall_ms, 1), "max_scenarios_per_round": cfg.max_scenarios_per_round,
            "model": cfg.model, "prompt_label": cfg.prompt_label, "error": error}


def make_task(*, cfg: RunConfig, ch_settings: ClickHouseSettings, lf: Any, variant_key: str,
              run_name: str, run_fn: Optional[RunFn] = None,
              item_timeout_s: float = DEFAULT_ITEM_TIMEOUT_S) -> Callable[..., Awaitable[dict]]:
    """An async experiment task that never raises.

    ``variant_key`` (``breadth-10``, ``model-<id>``, ``prompt-<label>``) becomes the trace
    version: the dashboard's Experiment widgets group by it.

    The SDK awaits coroutine tasks (``langfuse.experiment._run_task``) inside the event
    loop ``run_experiment`` starts, so each item opens its own ClickHouse client in that
    loop (an httpx client must not cross loops).
    """

    async def task(*, item: Any, **kwargs: Any) -> dict[str, Any]:
        t0 = time.perf_counter()
        qid = "unknown"
        try:
            question = question_from_item(item)
            qid = question.id
            fn = run_fn
            if fn is None:
                from explorer.agent import run_question as fn  # imported late: the agent is optional for tests
            async with ClickHouseClient(ch_settings) as ch:
                res = await asyncio.wait_for(
                    fn(question, cfg, ch=ch, lf=lf, tags=["experiment", variant_key],
                       session_id=run_name, metadata={"experiment_run": run_name, "variant": variant_key},
                       version=variant_key),
                    timeout=item_timeout_s,
                )
            return task_output(res, cfg)
        except asyncio.TimeoutError:
            return error_output(qid, f"TimeoutError: item exceeded {item_timeout_s:.0f}s",
                                (time.perf_counter() - t0) * 1000.0, cfg)
        except Exception as e:  # noqa: BLE001 - a raising task is silently dropped by the SDK
            return error_output(qid, f"{type(e).__name__}: {e}"[:2000], (time.perf_counter() - t0) * 1000.0, cfg)

    return task


def experiment_items(items: list[Any]) -> list[Any]:
    """Dataset items with metadata trimmed to short scalars.

    The SDK propagates item metadata onto every span of the item's trace and drops (with a
    WARNING per item) any value over 200 characters, such as the reference SQL and notes.
    The dataset keeps the full metadata; the experiment only needs the short fields.
    """
    out = []
    for it in items:
        md = {k: v for k, v in (getattr(it, "metadata", None) or {}).items()
              if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 200}
        out.append(it.model_copy(update={"metadata": md}) if hasattr(it, "model_copy") else it)
    return out


# ----------------------------------------------------------------------- read-back
def _score_value(score: Any) -> Optional[float]:
    v = getattr(score, "value", None)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else None


def experiment_item_scores(lf: Any, experiment_id: str, since: dt.datetime) -> dict[str, dict[str, float]]:
    """{experiment item id: {score name: value}} from the experiments API (scores v3).

    The v3 ``GET /api/public/dataset-run-items`` was removed from Langfuse Cloud on
    2026-11-16 (HTTP 410); experiment items are its v4 successor. Every experiment item
    is listed, including the ones whose task failed (they carry ``task_error``).
    """
    out: dict[str, dict[str, float]] = {}
    cursor = None
    while True:
        res = lf.api.experiments.list_items(from_start_time=since, experiment_id=experiment_id,
                                            fields="core,scores", limit=100, cursor=cursor)
        for it in res.data:
            scores = out.setdefault(it.experiment_item_id, {})
            # Prefer the item (observation) score; the agent's same-named trace scores carry the same value.
            for s in sorted(it.scores or [], key=lambda s: getattr(getattr(s, "subject", None), "kind", "") != "observation"):
                val = _score_value(s)
                if val is not None and s.name not in scores:
                    scores[s.name] = val
        cursor = res.meta.cursor
        if not cursor or not res.data:
            return out


def run_scores(lf: Any, experiment_id: str, since: dt.datetime) -> dict[str, float]:
    """Run-level scores (the run evaluator's accuracy etc.) from ``GET /experiments``."""
    res = lf.api.experiments.list(from_start_time=since, id=experiment_id, fields="core,scores")
    for exp in res.data:
        return {s.name: v for s in (exp.scores or []) if (v := _score_value(s)) is not None}
    return {}


def read_back(lf: Any, *, experiment_id: Optional[str], expected_items: set[str], since: dt.datetime,
              wait_s: float) -> dict[str, Any]:
    """Poll Langfuse until every dataset item of the run is an experiment item with an
    ``answer_correct`` score (the experiments API may lag ingestion by a few minutes)."""
    deadline = time.monotonic() + wait_s
    listed: set[str] = set()
    scored: set[str] = set()
    summary, run_level, err = None, {}, None
    while experiment_id:
        try:
            per_item = experiment_item_scores(lf, experiment_id, since)
            listed = set(per_item) & expected_items
            scored = {k for k in listed if "answer_correct" in per_item[k]}
            vals = [per_item[k] for k in scored]

            def mean(name: str) -> Optional[float]:
                xs = [v[name] for v in vals if name in v]
                return sum(xs) / len(xs) if xs else None

            summary = {"accuracy": mean("answer_correct"), "errors": sum(v.get("task_error", 0) for v in vals),
                       **{f"mean_{m}": mean(m) for m in PASS_THROUGH_METRICS}}
            run_level = run_scores(lf, experiment_id, since)
            err = None
        except Exception as e:  # noqa: BLE001 - keep polling; report the last error
            err = f"{type(e).__name__}: {str(e)[:300]}"
        if (scored == expected_items and "accuracy" in run_level) or time.monotonic() > deadline:
            break
        time.sleep(10)
    n = len(expected_items)
    return {"run_items": len(listed), "scored_items": len(scored), "langfuse_summary": summary,
            "run_scores": run_level, "error": err if experiment_id else "no dataset run id from the SDK",
            "ok": len(listed) == n and len(scored) == n}


# ----------------------------------------------------------------------- printing
def _fmt(v: Optional[float], spec: str) -> str:
    return "-" if v is None else format(v, spec)


def print_summary(rows: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 112)
    print(f"{'variant':<28} {'scen/rnd':>8} {'items':>5} {'LF items':>8} {'LF scored':>9} {'accuracy':>8} "
          f"{'db share':>8} {'wall s':>7} {'cost $':>7} {'LLM calls':>9} {'scenarios':>9}  source")
    for r in rows:
        lf_s = r["readback"].get("langfuse_summary") if r["readback"]["ok"] else None
        src = "langfuse" if lf_s else "local"
        s = lf_s or {"accuracy": r["local"]["accuracy"],
                     **{f"mean_{m}": r["local"].get(f"mean_{m}") for m in PASS_THROUGH_METRICS}}
        wall = s.get("mean_wall_ms")
        print(f"{r['variant']:<28} {r['scen']:>8} {r['items']:>5} {r['readback']['run_items']:>8} "
              f"{r['readback']['scored_items']:>9} {_fmt(s.get('accuracy'), '.0%'):>8} "
              f"{_fmt(s.get('mean_db_time_share'), '.1%'):>8} {_fmt(wall / 1000 if wall else None, '.1f'):>7} "
              f"{_fmt(s.get('mean_run_cost_usd'), '.3f'):>7} {_fmt(s.get('mean_llm_calls'), '.1f'):>9} "
              f"{_fmt(s.get('mean_scenarios_evaluated'), '.0f'):>9}  {src}")
    print("=" * 112)
    for r in rows:
        print(f"{r['variant']}: run {r['run_name']}  {r.get('url') or ''}")


# ----------------------------------------------------------------------- dry run
async def _dry_run(question: Question, cfg: RunConfig, settings: ClickHouseSettings, lf: Any) -> dict:
    from explorer.agent import run_question

    async with ClickHouseClient(settings) as ch:
        ref = await ch.query(question.reference_sql.format(table=settings.qualified_table),
                             settings={"max_execution_time": "120", "max_result_rows": "100"})
        expected = ref.first()
    task = make_task(cfg=cfg, ch_settings=settings, lf=lf, variant_key=f"dry-run-{cfg.max_scenarios_per_round}",
                     run_name=f"dry-run-{utc_stamp()}", run_fn=run_question)
    item = {"input": {"question_id": question.id, "question": question.text, "answer_keys": question.answer_keys},
            "expected_output": expected}
    output = await task(item=item)
    return {"expected": expected, "output": output}


def dry_run(args: argparse.Namespace, cfg: RunConfig, settings: ClickHouseSettings) -> int:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"  # no traces, scores or dataset runs
    from explorer.tracing import get_langfuse

    lf = get_langfuse()
    questions = {q.id: q for q in load_questions()}
    qid = args.question or next(iter(questions))
    if qid not in questions:
        raise SystemExit(f"unknown question {qid!r}; known: {sorted(questions)}")
    q = questions[qid]
    print(f"dry run: {qid} at {cfg.max_scenarios_per_round} scenarios/round, model={cfg.model} "
          f"on {settings.host}/{settings.qualified_table} (Langfuse tracing disabled)")
    print(f"Q: {q.text}")
    res = asyncio.run(_dry_run(q, cfg, settings, lf))
    out, exp = res["output"], res["expected"]
    print(json.dumps(out, indent=2, default=str))
    ok = answer_correct(input=None, output=out, expected_output=exp)
    frac = answer_key_match(input=None, output=out, expected_output=exp)
    print(f"\nexpected {exp}\nanswer_correct={bool(ok.value)}  answer_key_match={frac.value}  ({ok.comment})")
    return 0 if not out.get("error") else 1


# ----------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--breadth", choices=["narrow", "wide", "both"], default=None,
                    help="Experiment A: 10 vs 300 scenarios/round (default both; with --compare: wide)")
    ap.add_argument("--compare", nargs=2, default=None, metavar=("KIND", "A,B"),
                    help="Experiment B: `--compare prompt-labels A,B` or `--compare models A,B`")
    ap.add_argument("--limit", type=int, default=None, help="only the first N dataset items")
    ap.add_argument("--concurrency", type=int, default=3, help="dataset items in parallel (default 3)")
    ap.add_argument("--max-rounds", type=int, default=None, help="override RunConfig.max_rounds (same for all variants)")
    ap.add_argument("--item-timeout", type=float, default=DEFAULT_ITEM_TIMEOUT_S, help="seconds per item")
    ap.add_argument("--readback-wait", type=float, default=300.0, help="seconds to wait for Langfuse to show the run")
    ap.add_argument("--dry-run", action="store_true", help="one question, no Langfuse writes")
    ap.add_argument("--question", default=None, help="question id for --dry-run (default: the first)")
    args = ap.parse_args()

    if args.compare:
        args.compare = parse_compare(*args.compare)
    if args.dry_run and args.breadth in (None, "both") and not args.compare:
        args.breadth = "wide"  # a dry run is one question at one breadth: the hero setting

    load_env()
    settings = ClickHouseSettings.from_env()
    base = RunConfig()
    variants = build_variants(args, base)

    if args.dry_run:
        return dry_run(args, variants[0].cfg, settings)

    from explorer.tracing import get_langfuse

    lf = get_langfuse()
    if not lf.auth_check():
        print("Langfuse auth_check failed (check LANGFUSE_* in .env)")
        return 1
    if args.compare and args.compare[0] == "prompt-labels":
        _check_prompt_labels(lf, args.compare[1])

    dataset = lf.get_dataset(DATASET_NAME)
    items = [i for i in dataset.items if str(i.status) != "ARCHIVED"]
    # questions.yaml order (the hero first), so `--limit N` always includes q-padding.
    order = {q.id: n for n, q in enumerate(load_questions())}
    items.sort(key=lambda i: (order.get(i.id, len(order)), i.id))
    if args.limit:
        items = items[: args.limit]
    items = experiment_items(items)
    if not items:
        raise SystemExit(f"dataset {DATASET_NAME} has no active items: run scripts/seed_dataset.py")
    print(f"dataset {DATASET_NAME}: {len(items)} items; target {settings.host}/{settings.qualified_table}; "
          f"{len(variants)} variant(s): {', '.join(v.key for v in variants)}")

    stamp = utc_stamp()
    rows = []
    for v in variants:
        run_name = f"{v.key}-{stamp}"
        print(f"\n--- {run_name}: {v.description}")
        started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        task = make_task(cfg=v.cfg, ch_settings=settings, lf=lf, variant_key=v.key, run_name=run_name,
                         item_timeout_s=args.item_timeout)
        result = lf.run_experiment(
            name=v.experiment, run_name=run_name, description=v.description, data=items, task=task,
            evaluators=ITEM_EVALUATORS, run_evaluators=RUN_EVALUATORS, max_concurrency=args.concurrency,
            metadata={"variant": v.key, "max_scenarios_per_round": str(v.cfg.max_scenarios_per_round),
                      "max_rounds": str(v.cfg.max_rounds), "model": v.cfg.model,
                      "prompt_label": v.cfg.prompt_label, "ch_host": settings.host},
        )
        lf.flush()
        local = summarize(result.item_results)
        if len(result.item_results) != len(items):
            print(f"WARNING: SDK returned {len(result.item_results)} item results for {len(items)} items")
        for r in result.item_results:
            o = r.output if isinstance(r.output, Mapping) else {}
            mark = "ok " if answer_correct(input=None, output=o, expected_output=r.item.expected_output).value else "BAD"
            print(f"  {mark} {o.get('question_id', '?'):<16} got={o.get('answer_values')} "
                  f"want={r.item.expected_output} stop={o.get('stop_reason')} "
                  f"scen={o.get('scenarios_evaluated')} cost={o.get('run_cost_usd')}"
                  + (f" ERROR={str(o.get('error'))[:160]}" if o.get("error") else ""))
        rb = read_back(lf, experiment_id=result.dataset_run_id, expected_items={i.id for i in items},
                       since=started, wait_s=args.readback_wait)
        if not rb["ok"]:
            print(f"READ-BACK FAIL for {run_name}: experiment items {rb['run_items']}/{len(items)}, "
                  f"scored {rb['scored_items']}/{len(items)}" + (f", last error {rb['error']}" if rb["error"] else ""))
        else:
            print(f"read back {run_name}: {rb['run_items']}/{len(items)} experiment items, all scored; "
                  f"run scores {rb['run_scores']}")
        rows.append({"variant": v.key, "run_name": run_name, "scen": v.cfg.max_scenarios_per_round,
                     "items": len(items), "local": local, "readback": rb, "url": result.dataset_run_url})

    print_summary(rows)
    return 0 if all(r["readback"]["ok"] for r in rows) else 1


def _check_prompt_labels(lf: Any, labels: list[str]) -> None:
    """A missing label would silently fall back to the in-code prompt and make A == B."""
    from explorer import prompts as P

    for label in labels:
        for name in (P.PLANNER, P.ANALYST):
            try:
                lf.get_prompt(name, label=label, cache_ttl_seconds=0)
            except Exception as e:  # noqa: BLE001
                raise SystemExit(f"prompt {name!r} has no label {label!r} in Langfuse ({type(e).__name__}); "
                                 "both variants would run the fallback prompt") from e


if __name__ == "__main__":
    raise SystemExit(main())

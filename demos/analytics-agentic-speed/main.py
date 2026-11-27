"""Run the analytics agent on one question and print what the database cost.

    .venv/bin/python main.py --question q-padding
    .venv/bin/python main.py --ask "Which carrier ..." --max-scenarios 300 --rounds 4
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import yaml

from explorer.config import DEMO_DIR, ClickHouseSettings, RunConfig, load_env
from explorer.models import Question

QUESTIONS = DEMO_DIR / "data" / "questions.yaml"


def load_questions(path: Path = QUESTIONS) -> dict[str, Question]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or []
    items = raw.get("questions", raw) if isinstance(raw, dict) else raw
    return {q["id"]: Question(id=q["id"], text=q["text"], answer_keys=list(q.get("answer_keys") or []),
                              reference_sql=q.get("reference_sql"), notes=q.get("notes", ""))
            for q in items}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--question", default="q-padding", help="question id from data/questions.yaml")
    g.add_argument("--ask", help="a free-text question instead of a dataset one")
    ap.add_argument("--list", action="store_true", help="list question ids and exit")
    ap.add_argument("--max-scenarios", type=int, default=300, help="grid budget per round (the hero knob)")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--concurrency", type=int, default=32, help="scenario queries in flight")
    ap.add_argument("--model")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--prompt-label")
    ap.add_argument("--budget-usd", type=float, default=3.0)
    ap.add_argument("--no-query-scores", action="store_true", help="skip the per-query db_server_ms score")
    ap.add_argument("--tag", action="append", default=[], help="extra trace tag (repeatable)")
    return ap.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    load_env()
    from explorer.agent import run_question
    from explorer.ch_client import ClickHouseClient
    from explorer.tracing import get_langfuse

    qs = load_questions()
    if args.list:
        for q in qs.values():
            print(f"{q.id:28s} {q.text[:100]}")
        return 0
    if args.ask:
        question = Question(id="adhoc", text=args.ask)
    elif args.question in qs:
        question = qs[args.question]
    else:
        print(f"unknown question {args.question!r}; use --list or --ask", file=sys.stderr)
        return 2

    cfg = RunConfig(max_scenarios_per_round=args.max_scenarios, max_rounds=args.rounds,
                    concurrency=args.concurrency, budget_usd=args.budget_usd,
                    score_each_query=not args.no_query_scores)
    if args.model:
        cfg.model = args.model
    if args.effort:
        cfg.effort = args.effort
    if args.prompt_label:
        cfg.prompt_label = args.prompt_label

    lf = get_langfuse()
    settings = ClickHouseSettings.from_env()
    t0 = time.perf_counter()
    print(f"\n{question.text}\n")
    print(f"ClickHouse {settings.host} · table {settings.qualified_table} · model {cfg.model} ({cfg.effort})")
    async with ClickHouseClient(settings, max_connections=max(cfg.concurrency * 2, 16)) as ch:
        ping = await ch.ping()
        print(f"connected (round trip {ping:.0f} ms)\n")
        res = await run_question(question, cfg, ch=ch, lf=lf, tags=["live", *args.tag],
                                 on_event=lambda m: print(f"  [{time.perf_counter() - t0:6.1f}s] {m}", flush=True))
    lf.flush()

    print("\n" + "=" * 78)
    print(res.summary or "(no summary)")
    if res.answer_values:
        print("answer:", ", ".join(f"{k}={v}" for k, v in res.answer_values.items()))
    print("-" * 78)
    llm_s = (res.wall_ms - res.db_wait_ms_total) / 1000
    print(f"stop reason        {res.stop_reason}" + (f"  ({res.error})" if res.error else ""))
    print(f"scenarios          {res.scenarios_evaluated} over {res.rounds} round(s)")
    print(f"LLM calls          {res.llm_calls}  (${res.cost_usd:.3f})")
    print(f"run wall time      {res.wall_ms/1000:.1f} s")
    print(f"waiting on DB      {res.db_wait_ms_total/1000:.2f} s  = {res.db_time_share:.1%} of the run")
    print(f"DB server time     {res.db_server_ms_total/1000:.2f} s summed over all queries")
    print(f"everything else    {llm_s:.1f} s (mostly the model thinking)")
    print(f"grounded           {res.recommendation_grounded}")
    if res.trace_url:
        print(f"trace              {res.trace_url}")
    return 0 if res.stop_reason != "error" else 1


def main() -> None:
    sys.exit(asyncio.run(amain(parse_args())))


if __name__ == "__main__":
    main()

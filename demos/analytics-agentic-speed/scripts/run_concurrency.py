#!/usr/bin/env python3
"""The concurrency act (SPEC §7): N agents at once, each on its own question, one database.

    .venv/bin/python scripts/run_concurrency.py --dry-run                      # print the plan, spend nothing
    .venv/bin/python scripts/run_concurrency.py --levels 1,5,10,25 --yes       # run each level in turn
    .venv/bin/python scripts/run_concurrency.py --levels 10 --max-scenarios 100 --rounds 1 --yes

Each level runs N agents concurrently on one shared ClickHouse client (one connection pool,
sized for N x the per-agent sweep concurrency), questions taken from data/questions.yaml in
order and cycled when N exceeds them. Every trace is tagged
``analytics-agentic-speed, concurrency, agents-<N>``, and a level shares one session id
(``concurrency-agents-<NNN>-<run stamp>``), so a level is one row in Langfuse Sessions and in
the dashboard's "Concurrency" widget.

After each level the script prints p50/p95/p99 of ClickHouse's own query time across every
query the level ran (measured locally, one sample per query), the query count and the wall
time; then, once Langfuse has ingested the level's db_server_ms scores, the same percentiles
as Langfuse computes them (Metrics API v2), so the presenter can show the two agree.

This spends real money (LLM calls) and real database load: it prints a cost estimate and
does nothing without ``--yes``. It refuses the public playground (its ``demo`` user allows
20 queries per query shape per hour).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEMO_DIR = Path(__file__).resolve().parent.parent
for p in (DEMO_DIR, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from explorer.ch_client import ClickHouseClient  # noqa: E402
from explorer.config import MODEL_PRICES, PLAYGROUND, ClickHouseSettings, RunConfig, load_env  # noqa: E402
from explorer.models import Question  # noqa: E402

QUESTIONS_FILE = DEMO_DIR / "data" / "questions.yaml"
# Rough token budget of one plan/analyse call, for the estimate only (schema + profile +
# results table in, structured JSON out). The per-run budget cap is the hard bound.
EST_INPUT_TOKENS = 8000
EST_OUTPUT_TOKENS = 2000


# ---------------------------------------------------------------- pure helpers (tested)

def parse_levels(text: str) -> list[int]:
    levels = [int(x) for x in text.replace(" ", "").split(",") if x]
    if not levels or any(n < 1 for n in levels):
        raise argparse.ArgumentTypeError("levels must be positive integers, e.g. 1,5,10,25")
    return levels


def load_questions(path: Path = QUESTIONS_FILE) -> list[Question]:
    import yaml  # noqa: PLC0415

    raw = yaml.safe_load(path.read_text()) or []
    known = {f.name for f in fields(Question)}
    out = [Question(**{k: v for k, v in item.items() if k in known}) for item in raw]
    if not out:
        raise ValueError(f"{path} holds no questions")
    return out


def assign_questions(questions: list[Any], n: int) -> list[Any]:
    """N questions in file order, cycling when N exceeds the file."""
    return [questions[i % len(questions)] for i in range(n)]


def session_id_for(level: int, stamp: str) -> str:
    # Zero-padded so the dashboard's session rows sort by level.
    return f"concurrency-agents-{level:03d}-{stamp}"


def agent_tags(level: int) -> list[str]:
    # run_question prepends "analytics-agentic-speed", so the trace ends up with
    # ["analytics-agentic-speed", "concurrency", "agents-<N>"].
    return ["concurrency", f"agents-{level}"]


def pool_size(levels: list[int], cfg: RunConfig) -> int:
    """Connections for the busiest level: every agent's sweep in flight at once (lookups use <= 5)."""
    return max(levels) * max(cfg.concurrency, 5)


def percentile(values: list[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile, ``p`` in 0-100 (same definition as numpy's default)."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class Estimate:
    runs: int
    llm_calls_low: int
    llm_calls_high: int
    usd_per_call: float
    usd_typical: float
    usd_cap: float


def estimate(levels: list[int], cfg: RunConfig) -> Estimate:
    """A run makes one plan call plus one analyse call per round (a lookup adds one plan call)."""
    runs = sum(levels)
    low, high = runs * 2, runs * (cfg.max_rounds + 2)
    price_in, price_out, _ = MODEL_PRICES.get(cfg.model, max(MODEL_PRICES.values()))
    per_call = (EST_INPUT_TOKENS * price_in + EST_OUTPUT_TOKENS * price_out) / 1e6
    typical = runs * min(cfg.max_rounds + 1, 5) * per_call
    return Estimate(runs, low, high, per_call, typical, runs * cfg.budget_usd)


def summarize(level: int, server_ms: list[float], failures: int, wall_s: float, results: list[Any]) -> dict:
    ok = [r for r in results if not isinstance(r, BaseException)]
    return {
        "level": level,
        "runs_ok": sum(1 for r in ok if getattr(r, "stop_reason", "error") != "error"),
        "runs_failed": len(results) - len(ok) + sum(1 for r in ok if getattr(r, "stop_reason", "") == "error"),
        "queries": len(server_ms),
        "query_errors": failures,
        "p50": percentile(server_ms, 50),
        "p95": percentile(server_ms, 95),
        "p99": percentile(server_ms, 99),
        "wall_s": wall_s,
        "queries_per_s": len(server_ms) / wall_s if wall_s else 0.0,
        "scenarios": sum(getattr(r, "scenarios_evaluated", 0) for r in ok),
        "llm_calls": sum(getattr(r, "llm_calls", 0) for r in ok),
        "cost_usd": (sum(r.cost_usd for r in ok)
                     if all(getattr(r, "cost_usd", None) is not None for r in ok) else None),
    }


def _ms(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:,.1f}"


def print_plan(levels: list[int], cfg: RunConfig, questions: Optional[list[Question]], stamp: str) -> Estimate:
    est = estimate(levels, cfg)
    print(f"levels {levels}: {est.runs} agent runs in total, model {cfg.model} (effort {cfg.effort})")
    print(f"per run: up to {cfg.max_scenarios_per_round} scenarios/round, {cfg.max_rounds} round(s), "
          f"sweep concurrency {cfg.concurrency}, budget cap ${cfg.budget_usd:.2f}")
    print(f"ClickHouse pool: {pool_size(levels, cfg)} connections (busiest level x sweep concurrency)")
    for n in levels:
        qs = assign_questions(questions, n) if questions else []
        ids = ", ".join(q.id for q in qs[:8]) + (f", ... (+{n - 8})" if n > 8 else "")
        print(f"  agents-{n:<3} session {session_id_for(n, stamp)}  questions: {ids or '(questions file missing)'}")
    print(f"estimate: {est.llm_calls_low}-{est.llm_calls_high} LLM calls; typical ~${est.usd_typical:,.2f} "
          f"(~${est.usd_per_call:.3f}/call at ~{EST_INPUT_TOKENS} in / {EST_OUTPUT_TOKENS} out tokens); "
          f"hard cap ${est.usd_cap:,.2f} (budget x runs)")
    return est


# ---------------------------------------------------------------- running

@dataclass
class RecordingClickHouseClient(ClickHouseClient):
    """The shared client, plus ClickHouse's own time for every query it answered."""

    server_ms: list[float] = field(default_factory=list)
    failures: int = 0

    async def query(self, sql: str, params: Any = None, *, settings: Any = None, check: bool = True):
        try:
            res = await super().query(sql, params, settings=settings, check=check)
        except Exception:
            self.failures += 1
            raise
        self.server_ms.append(res.stats.server_ms)
        return res

    def reset(self) -> None:
        self.server_ms, self.failures = [], 0


def langfuse_percentiles(api: Any, session_id: str, expected: int, timeout_s: float) -> Optional[dict]:
    """p50/p95/p99/count of the level's db_server_ms scores as Langfuse holds them.

    Polls until the count reaches ``expected`` or stops growing; ingestion takes seconds.
    """
    from seed_langfuse import metrics_query  # noqa: PLC0415

    w = {"view": "scores-numeric", "chartType": "PIVOT_TABLE", "dimensions": [], "chartConfig": {},
         "metrics": [{"measure": "value", "agg": a} for a in ("p50", "p95", "p99")] + [{"measure": "count", "agg": "count"}],
         "filters": [{"column": "name", "operator": "any of", "value": ["db_server_ms"], "type": "stringOptions"},
                     {"column": "sessionId", "operator": "any of", "value": [session_id], "type": "stringOptions"}]}
    deadline, last, stable, row = time.monotonic() + timeout_s, -1, 0, None
    while time.monotonic() < deadline:
        rows = api.metrics(metrics_query(w, days=1))
        row = rows[0] if rows else {}
        n = int(row.get("count_count") or 0)
        if n >= expected > 0:
            break
        stable = stable + 1 if n == last and n > 0 else 0
        if stable >= 3:
            break
        last = n
        time.sleep(5)
    return row


async def run_level(level: int, questions: list[Question], cfg: RunConfig, ch: RecordingClickHouseClient,
                    lf: Any, run_question: Any, stamp: str, verbose: bool) -> dict:
    ch.reset()
    sid = session_id_for(level, stamp)
    qs = assign_questions(questions, level)
    print(f"\n== agents-{level}: {level} agent(s) at once, session {sid}")

    async def one(i: int, q: Question) -> Any:
        def emit(msg: str) -> None:
            if verbose:
                print(f"  [agent {i:>2} {q.id}] {msg}")
        res = await run_question(q, cfg, ch=ch, lf=lf, tags=agent_tags(level), session_id=sid,
                                 metadata={"act": "concurrency", "concurrency_level": level,
                                           "agent_index": i},
                                 on_event=emit)
        print(f"  agent {i:>2} {q.id}: {res.stop_reason}, {res.scenarios_evaluated} scenarios, "
              f"{res.llm_calls} LLM calls, {res.wall_ms / 1000:.1f}s")
        return res

    t0 = time.perf_counter()
    results = await asyncio.gather(*(one(i, q) for i, q in enumerate(qs)), return_exceptions=True)
    wall_s = time.perf_counter() - t0
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            print(f"  agent {i:>2} raised {type(r).__name__}: {str(r)[:200]}")
    s = summarize(level, list(ch.server_ms), ch.failures, wall_s, list(results))
    s["session_id"] = sid
    print(f"  ClickHouse time per query: p50 {_ms(s['p50'])} ms, p95 {_ms(s['p95'])} ms, "
          f"p99 {_ms(s['p99'])} ms over {s['queries']} queries ({s['query_errors']} failed)")
    cost = f"${s['cost_usd']:.2f}" if s["cost_usd"] is not None else "cost unknown"
    print(f"  wall {wall_s:.1f}s, {s['queries_per_s']:.1f} queries/s, {s['scenarios']} scenarios, "
          f"{s['llm_calls']} LLM calls, {cost}; runs ok {s['runs_ok']}/{level}")
    return s


async def main_async(args: argparse.Namespace, cfg: RunConfig, questions: list[Question], stamp: str) -> int:
    from explorer.agent import run_question  # noqa: PLC0415 - lazy: the act is the only caller
    from explorer.tracing import get_langfuse  # noqa: PLC0415
    from seed_langfuse import API, verify_project  # noqa: PLC0415

    api = API.from_env()
    verify_project(api, args.project_name)
    lf = get_langfuse()
    settings = ClickHouseSettings.from_env()
    print(f"ClickHouse: {settings.host} ({settings.qualified_table})")
    summaries = []
    async with RecordingClickHouseClient(settings, max_connections=pool_size(args.levels, cfg)) as ch:
        print(f"ping: {await ch.ping():.0f} ms round trip")
        for level in args.levels:
            s = await run_level(level, questions, cfg, ch, lf, run_question, stamp, args.verbose)
            lf.flush()
            if not args.no_readback and s["queries"]:
                row = langfuse_percentiles(api, s["session_id"], s["queries"], args.readback_timeout)
                n = int((row or {}).get("count_count") or 0)
                if n:
                    print(f"  Langfuse agrees: p50 {_ms(row.get('p50_value'))} ms, p95 {_ms(row.get('p95_value'))} ms, "
                          f"p99 {_ms(row.get('p99_value'))} ms over {n} db_server_ms scores")
                else:
                    print("  Langfuse: scores not ingested yet; the local numbers above are the measurement")
            summaries.append(s)

    print("\n level | runs ok | queries |   p50 ms |   p95 ms |   p99 ms | wall s | queries/s")
    for s in summaries:
        print(f" {s['level']:>5} | {s['runs_ok']:>3}/{s['level']:<3} | {s['queries']:>7} | {_ms(s['p50']):>8} | "
              f"{_ms(s['p95']):>8} | {_ms(s['p99']):>8} | {s['wall_s']:>6.1f} | {s['queries_per_s']:>9.1f}")
    print('\nDashboard: "Concurrency — ClickHouse time per query by level" (one row per session).')
    return 0 if all(s["runs_failed"] == 0 for s in summaries) else 1


def build_config(args: argparse.Namespace) -> RunConfig:
    cfg = RunConfig()
    for attr, val in (("max_scenarios_per_round", args.max_scenarios), ("max_rounds", args.rounds),
                      ("concurrency", args.concurrency), ("budget_usd", args.budget), ("model", args.model)):
        if val is not None:
            setattr(cfg, attr, val)
    return cfg


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels", type=parse_levels, default=parse_levels("1,5,10,25"),
                    help="agents at once, one level after another (default 1,5,10,25)")
    ap.add_argument("--max-scenarios", type=int, help="max scenarios per round (RunConfig default 300)")
    ap.add_argument("--rounds", type=int, default=2,
                    help="max rounds per run (default 2: the act is about database load, not answers)")
    ap.add_argument("--concurrency", type=int, help="queries in flight per agent's sweep (default 16)")
    ap.add_argument("--budget", type=float, help="per-run LLM budget cap in USD (default 3.0)")
    ap.add_argument("--model", help="model for plan/analyse (default EXPLORER_MODEL)")
    ap.add_argument("--questions", type=Path, default=QUESTIONS_FILE)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and the estimate, run nothing")
    ap.add_argument("--yes", action="store_true", help="confirm the spend and run")
    ap.add_argument("--no-readback", action="store_true", help="skip the Langfuse percentile read-back")
    ap.add_argument("--readback-timeout", type=float, default=120.0, help="seconds to wait for ingestion")
    ap.add_argument("--project-name", default="analytics-agentic-speed", help="Langfuse project the keys must map to")
    ap.add_argument("--verbose", action="store_true", help="print every agent's progress events")
    args = ap.parse_args(argv)

    load_env()
    if os.getenv("EXPLORER_TARGET", "cloud") == "playground" or os.getenv("CH_HOST") == PLAYGROUND["CH_HOST"]:
        sys.exit("refusing the public playground: its demo user allows 20 queries per query shape per hour, "
                 "and its latency is shared. Unset EXPLORER_TARGET to use the Cloud service in .env.")
    cfg = build_config(args)
    try:
        questions: Optional[list[Question]] = load_questions(args.questions)
    except FileNotFoundError:
        questions = None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
    print_plan(args.levels, cfg, questions, stamp)

    if args.dry_run:
        print("\ndry run: nothing was run")
        return 0
    if questions is None:
        sys.exit(f"missing {args.questions}")
    if not args.yes:
        print("\nthis spends the estimate above; re-run with --yes to proceed")
        return 2
    return asyncio.run(main_async(args, cfg, questions, stamp))


if __name__ == "__main__":
    sys.exit(main())

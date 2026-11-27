#!/usr/bin/env python
"""Demo readiness: reads back the state every act of DEMO_SCRIPT.md depends on.

    .venv/bin/python scripts/verify_demo.py            # exit 1 when a required check fails
    .venv/bin/python scripts/verify_demo.py --links    # also print the link each act opens

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows: items with ground truth, widgets placed, scores on the
latest hero run, rows in the table. Each check prints PASS, FAIL or WARN; WARN covers the
optional acts (4 and 5) and the live view, which Act 1 can do without. A configuration,
network, HTTP or parse failure is a FAIL, never a traceback, and the last line is always
READY or NOT READY with the counts. Exit 0 only when every required check passed.

Settings come from the demo's .env only, on a laptop and on a CI runner alike
(``explorer.config.load_env`` drops inherited LANGFUSE_* first, then fails on a missing or
unreadable file, a missing key or an unusable base URL). That is checked before any client
is built or any request sent.

Cost: one scenario-shaped query three times (milliseconds), one free model lookup on the
Anthropic API, and a handful of Langfuse reads. No LLM calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

DEMO_DIR = Path(__file__).resolve().parent.parent
for p in (DEMO_DIR, DEMO_DIR / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from explorer.ch_client import ClickHouseClient  # noqa: E402
from explorer.config import ClickHouseSettings, EnvError, RunConfig, load_env  # noqa: E402
from explorer.prompts import ANALYST, PLANNER  # noqa: E402

import seed_langfuse as seed  # noqa: E402  (stdlib-only module: the dashboard + score-config contract)

PROJECT = seed.EXPECTED_PROJECT
DATASET = "scenario-explorer-questions"  # keeps its pre-rename name so Experiment A runs stay linked
HERO = "q-padding"
CANDIDATE_LABEL = "candidate-coarse"  # Act 5's prompt pair (scripts/seed_candidate_prompt.py)
MAX_PAGES = 200  # cursor traversal budget per read
MIN_ROWS = 200_000_000
WARM_MS = 200.0  # a warm hero-shaped scenario takes 10-40 ms of server time; 200 means "cold or wrong tier"
UI_URL = f"http://127.0.0.1:{os.getenv('PORT', '8099')}"

# One scenario of the hero question, exactly the shape the agent sends.
HERO_SCENARIO = (
    "SELECT countIf(ArrDelay <= 15 + {buffer:UInt16}) / count() AS metric, count() AS support "
    "FROM {table} WHERE Origin = {origin:String} AND Year = 2019 AND DepTimeBlk >= '1700' "
    "AND Cancelled = 0 AND Diverted = 0"
)

RESULTS: list[tuple[bool, bool, str]] = []  # (ok, required, what)
TRANSPORT: Optional[httpx.BaseTransport] = None  # None: the network. Tests set an httpx.MockTransport


def check(ok: bool, what: str, *, required: bool = True) -> bool:
    RESULTS.append((ok, required, what))
    mark = "PASS" if ok else ("FAIL" if required else "WARN")
    print(f"  {mark}  {what}{'' if ok or required else '  (optional)'}")
    return ok


def guard(what: str, fn: Any, *, required: bool = True) -> Any:
    """Run one read; a failure becomes a FAIL (or WARN) check and returns None."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001  every failure has to reach the verdict
        check(False, f"{what}: {describe(exc)}", required=required)
        return None


def describe(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        r = exc.response
        return f"HTTP {r.status_code} from {r.request.url.path}: {r.text[:160]}"
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def summary() -> tuple[str, int]:
    passed = sum(ok for ok, _, _ in RESULTS)
    failed = sum(not ok and req for ok, req, _ in RESULTS)
    warned = sum(not ok and not req for ok, req, _ in RESULTS)
    verdict = "READY" if not failed and RESULTS else "NOT READY"
    line = f"{verdict}: {passed} passed, {failed} failed, {warned} warned ({len(RESULTS)} checks)"
    return line, 0 if verdict == "READY" else 1


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CursorError(RuntimeError):
    """A cursor read that would not end: a repeated cursor or an exhausted page budget."""


class Langfuse:
    """The few read endpoints this needs, on APIs Langfuse Cloud still serves."""

    def __init__(self, base: str, public_key: str, secret_key: str,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self.base = base.rstrip("/")
        self.c = httpx.Client(base_url=self.base, timeout=60, auth=(public_key, secret_key),
                              transport=transport)

    @classmethod
    def from_env(cls) -> "Langfuse":
        return cls(os.environ["LANGFUSE_BASE_URL"], os.environ["LANGFUSE_PUBLIC_KEY"],
                   os.environ["LANGFUSE_SECRET_KEY"], transport=TRANSPORT)

    def get(self, path: str, **params: Any) -> Any:
        r = self.c.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def cursor(self, path: str, params: dict, cap: int = 2000, max_pages: int = MAX_PAGES) -> list[dict]:
        """Follow ``meta.cursor`` until it is absent. A page may be empty while the cursor
        is still there (Observations v2 says so), so an empty page never ends the read.
        Stops early once ``cap`` rows are in; a repeated cursor or more than ``max_pages``
        pages raises CursorError instead of looping or returning a silent partial list."""
        out: list[dict] = []
        cur: Optional[str] = None
        seen: set[str] = set()
        for _ in range(max_pages):
            d = self.get(path, **{**params, "limit": 100, **({"cursor": cur} if cur else {})})
            out += d.get("data") or []
            cur = (d.get("meta") or {}).get("cursor")
            if not cur or len(out) >= cap:
                return out[:cap]
            if cur in seen:
                raise CursorError(f"{path}: the server repeated a cursor after {len(seen) + 1} page(s)")
            seen.add(cur)
        raise CursorError(f"{path}: still paging after {max_pages} pages")

    def pages(self, path: str, params: Optional[dict] = None, max_pages: int = MAX_PAGES) -> list[dict]:
        """Page-numbered lists, read to ``meta.totalPages`` (bounded by ``max_pages``)."""
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            d = self.get(path, **{**(params or {}), "limit": 100, "page": page})
            out += d.get("data") or []
            if page >= ((d.get("meta") or {}).get("totalPages") or 1):
                return out
        raise CursorError(f"{path}: more than {max_pages} pages")

    def roots(self, since: datetime, extra: Optional[list[dict]] = None, cap: int = 500) -> list[dict]:
        f = [{"type": "stringOptions", "column": "name", "operator": "any of", "value": [seed.ROOT, seed.LEGACY_ROOT]},
             {"type": "datetime", "column": "startTime", "operator": ">=", "value": iso(since)}] + (extra or [])
        return self.cursor("/api/public/v2/observations", {"filter": json.dumps(f), "fields": "core,basic"}, cap)

    def trace_scores(self, trace_id: str) -> dict[str, Any]:
        # Scores v3 always returns the core fields; `subject` adds the kind it is attached to.
        rows = self.cursor("/api/public/v3/scores", {"traceId": trace_id, "fields": "subject"})
        return {s["name"]: s.get("value") for s in rows if (s.get("subject") or {}).get("kind") != "observation"}


async def clickhouse_checks(cfg: RunConfig) -> None:
    print("ClickHouse")
    try:
        settings = ClickHouseSettings.from_env()
        async with ClickHouseClient(settings) as ch:
            rtt = await ch.ping()
            check(True, f"reachable as the agent user (round trip {rtt:.0f} ms)")
            ro = await ch.query("SELECT getSetting('readonly') AS ro", check=False)
            check(int(ro.rows[0][0]) >= 1, f"agent user is read-only (readonly={ro.rows[0][0]})")
            n = await ch.query(f"SELECT count() AS n FROM {settings.qualified_table}", check=False)
            rows = int(n.rows[0][0])
            check(rows >= MIN_ROWS, f"{settings.qualified_table}: {rows:,} rows")
            sql = HERO_SCENARIO.replace("{table}", settings.qualified_table)
            times = []
            for _ in range(3):
                res = await ch.query(sql, {"origin": "LAX", "buffer": 9})
                times.append(res.stats.server_ms)
            check(min(times) <= WARM_MS, "hero scenario query, server ms: "
                  + " · ".join(f"{t:.0f}" for t in times) + f" (warm under {WARM_MS:.0f})")
    except Exception as exc:  # noqa: BLE001  an unreachable service is the most common failure
        check(False, f"ClickHouse not usable: {type(exc).__name__}: {str(exc)[:160]} "
                     "(IP access list? a TLS error / curl exit 35 means this machine is not on it)")


def anthropic_check(cfg: RunConfig) -> None:
    print("Model")
    try:
        import anthropic

        m = anthropic.Anthropic().models.retrieve(cfg.model)
        check(True, f"Anthropic key works, model {m.id} available (effort {cfg.effort})")
    except Exception as exc:  # noqa: BLE001
        check(False, f"Anthropic model {cfg.model} not usable: {type(exc).__name__}: {str(exc)[:160]}")


def langfuse_checks(lf: Langfuse, links: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    print("Langfuse")
    projects = guard("read /api/public/projects", lambda: lf.get("/api/public/projects").get("data") or [])
    if projects is None:
        return out
    proj = projects[0] if projects else {}
    out["project_id"] = proj.get("id")
    if not check(proj.get("name") == PROJECT, f"keys map to project '{proj.get('name')}' (want '{PROJECT}')"):
        print("    the LANGFUSE_* keys in the demo's .env must be this project's")
        return out
    for name in (PLANNER, ANALYST):
        p = guard(f"prompt {name} with label production (run scripts/seed_langfuse.py --only prompts)",
                  lambda name=name: lf.get(f"/api/public/v2/prompts/{name}", label="production"))
        if p is not None:
            check(bool(p.get("prompt")), f"prompt {name}: production = v{p.get('version')}")

    ds = guard(f"dataset {DATASET} (run scripts/seed_dataset.py)",
               lambda: lf.get(f"/api/public/v2/datasets/{DATASET}"))
    out["dataset_id"] = (ds or {}).get("id")
    items = guard(f"dataset {DATASET} items", lambda: lf.pages("/api/public/dataset-items", {"datasetName": DATASET}))
    if items is not None:
        active = [i for i in items if i.get("status", "ACTIVE") == "ACTIVE"]
        graded = [i for i in active if i.get("expectedOutput")]
        check(len(graded) == 15, f"dataset {DATASET}: {len(graded)}/{len(active)} active items with ground truth"
              + ("" if len(graded) == 15 else f" ({len(items)} in all; run scripts/seed_dataset.py)"))

    cfgs = guard("score configs", lambda: lf.pages("/api/public/score-configs"))
    if cfgs is not None:
        bad = seed.score_config_readback(cfgs)
        check(not bad, f"score configs: {len(seed.SCORE_CONFIGS) - len(bad)}/{len(seed.SCORE_CONFIGS)} "
                       "active with the right type and definition"
              + ("" if not bad else f"; {bad[0]}" + (f" (+{len(bad) - 1} more)" if len(bad) > 1 else "")))

    def dashboard() -> tuple[Optional[dict], list]:
        dash = next((d for d in lf.pages(f"{seed.U}/dashboards") if d["name"] == seed.DASHBOARD["name"]), None)
        if dash is None:
            return None, []
        return dash, (lf.get(f"{seed.U}/dashboards/{dash['id']}").get("definition") or {}).get("widgets") or []
    got = guard(f"dashboard '{seed.DASHBOARD['name']}'", dashboard)
    if got is not None:
        dash, placed = got
        check(len(placed) == len(seed.WIDGETS), f"dashboard '{seed.DASHBOARD['name']}': "
              f"{len(placed)}/{len(seed.WIDGETS)} widgets placed" + ("" if dash else " (run scripts/seed_langfuse.py)"))
        out["dashboard_id"] = dash and dash["id"]

    print("Runs (Acts 1-2 open a fresh one)")
    now = datetime.now(timezone.utc)
    hero_filter = [{"type": "stringObject", "column": "metadata", "key": "question_id", "operator": "=",
                    "value": HERO}]

    def find_runs() -> tuple[list[dict], str]:
        try:
            hero = lf.roots(now - timedelta(days=7), hero_filter)
        except httpx.HTTPStatusError as e:  # a refused metadata filter (400) falls back to any recent run
            if e.response.status_code != 400:
                raise
            hero = []
        return (hero, "hero (q-padding)") if hero else (lf.roots(now - timedelta(days=7)), "any question")
    runs = guard("recent runs (Observations v2)", find_runs)
    recent, label = runs if runs is not None else ([], "")
    if runs is not None and check(bool(recent), f"{len(recent)} run(s) in the last 7 days, {label}"
                                  + ("" if recent else ": run main.py --question q-padding")):
        latest = max(recent, key=lambda o: o.get("startTime") or "")
        out["latest_trace"] = latest["traceId"]
        sc = guard("latest run's scores (Scores v3)", lambda: lf.trace_scores(latest["traceId"]))
        if sc is not None:
            need = {"db_time_share", "scenarios_evaluated", "db_p95_ms", "recommendation_grounded"}
            age_h = (now - datetime.fromisoformat(latest["startTime"].replace("Z", "+00:00"))).total_seconds() / 3600
            ok = need <= set(sc)
            share = sc.get("db_time_share")
            check(ok, f"latest run ({age_h:.1f} h old): scenarios {sc.get('scenarios_evaluated')}, "
                      f"DB share {share if share is None else f'{float(share):.1%}'}, "
                      f"p95 {round(float(sc.get('db_p95_ms') or 0))} ms, grounded {sc.get('recommendation_grounded')}"
                  + ("" if ok else f"; missing {sorted(need - set(sc))}"))
            check(age_h <= 24, "latest run is under 24 h old (re-run the hero the day before)", required=False)

    print("Experiment A (Act 3, and the short path)")
    exps = guard("experiments read", lambda: lf.cursor("/api/public/experiments", {
        "datasetId": out["dataset_id"], "fromStartTime": iso(now - timedelta(days=60))})) \
        if out.get("dataset_id") else None
    if out.get("dataset_id") is None:
        check(False, f"Experiment A runs: dataset {DATASET} is missing")
    if exps is not None:
        names = [e.get("name") or "" for e in exps]
        out["experiments"] = exps
        for b in ("breadth-10", "breadth-300"):
            check(any(n.startswith(b + "-") for n in names), f"Experiment A run '{b}-*' "
                  f"({sum(n.startswith(b + '-') for n in names)} found in the last 60 days)"
                  + ("" if any(n.startswith(b + "-") for n in names)
                     else ": run scripts/run_experiment.py --breadth both the day before"))

    print("Optional acts")
    for name in (PLANNER, ANALYST):
        cand = guard(f"Act 5: prompt {name} with label {CANDIDATE_LABEL} (scripts/seed_candidate_prompt.py)",
                     lambda name=name: lf.get(f"/api/public/v2/prompts/{name}", label=CANDIDATE_LABEL),
                     required=False)
        if cand is not None:
            check(True, f"Act 5: prompt {name}: {CANDIDATE_LABEL} = v{cand.get('version')}", required=False)
    conc = guard("Act 4: concurrency runs read", lambda: lf.roots(
        now - timedelta(days=30), [{"type": "arrayOptions", "column": "tags", "operator": "any of",
                                    "value": ["concurrency"]}], cap=200), required=False)
    if conc is not None:
        out["concurrency"] = conc
        check(bool(conc), f"Act 4: {len(conc)} concurrency run(s) in the last 30 days", required=False)
    try:
        info = httpx.get(f"{UI_URL}/api/info", timeout=3).json()
        check(not info.get("ping_error"), f"live view up at {UI_URL} (ClickHouse ping {info.get('ping_ms')} ms)",
              required=False)
    except Exception:  # noqa: BLE001
        check(False, f"live view not running at {UI_URL} (./run_ui.sh)", required=False)
    return out


def print_links(lf: Langfuse, st: dict[str, Any]) -> None:
    base = f"{lf.base}/project/{st.get('project_id')}"
    print("\nLinks")
    print(f"  live view           {UI_URL}")
    if st.get("latest_trace"):
        print(f"  latest run (Act 1)  {base}/traces/{st['latest_trace']}")
    if st.get("dashboard_id"):
        print(f"  dashboard (Close)   {base}/dashboards/{st['dashboard_id']}")
    if st.get("dataset_id"):
        print(f"  dataset (Act 3)     {base}/datasets/{st['dataset_id']}")
        for e in sorted(st.get("experiments") or [], key=lambda e: e.get("startTime") or e.get("createdAt") or "")[-4:]:
            print(f"    run {e.get('name')}")
    print(f"  prompts             {base}/prompts")
    sessions = sorted({o.get("sessionId") for o in st.get("concurrency") or [] if o.get("sessionId")})
    for s in sessions[-3:]:
        print(f"  concurrency session {base}/sessions/{s}")


def run_checks(args: argparse.Namespace) -> None:
    """Every check, in order. Configuration is validated before any client or request."""
    print("Configuration")
    source = load_env()  # drops inherited LANGFUSE_*; EnvError on a missing .env, key or bad URL
    check(True, f"settings from {source}; shell-exported LANGFUSE_* ignored")
    cfg = RunConfig()
    lf = Langfuse.from_env()
    table = guard("ClickHouse settings", lambda: ClickHouseSettings.from_env().qualified_table)
    print(f"Target: Langfuse {lf.base} · ClickHouse table {table}")
    asyncio.run(clickhouse_checks(cfg))
    anthropic_check(cfg)
    st = langfuse_checks(lf, args.links)
    if args.links:
        print_links(lf, st)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    args = ap.parse_args(argv)  # --help and usage errors exit here, as usual
    RESULTS.clear()
    try:
        run_checks(args)
    except EnvError as exc:  # a SystemExit: caught by name so it is a counted FAIL, not an exit
        check(False, str(exc))
    except Exception as exc:  # noqa: BLE001  construction, HTTP, network, parse: all reach the verdict
        check(False, f"verification stopped: {describe(exc)}")
    line, code = summary()
    print(f"\n{line}")
    return code


if __name__ == "__main__":
    sys.exit(main())

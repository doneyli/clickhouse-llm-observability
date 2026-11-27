#!/usr/bin/env python
"""Demo readiness: reads back the state every act of DEMO_SCRIPT.md depends on.

    .venv/bin/python scripts/verify_demo.py            # exit 1 when a required check fails
    .venv/bin/python scripts/verify_demo.py --links    # also print the link each act opens

An HTTP 200 proves little on its own (a trace can exist before its observations land), so
every check counts real rows: items with ground truth, widgets placed, scores on the
latest hero run, rows in the table. Checks marked "optional" cover the optional acts and
only warn.

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
from explorer.config import ClickHouseSettings, RunConfig, load_env  # noqa: E402
from explorer.prompts import ANALYST, PLANNER  # noqa: E402

import seed_langfuse as seed  # noqa: E402  (stdlib-only module: the dashboard + score-config contract)

PROJECT = seed.EXPECTED_PROJECT
DATASET = "scenario-explorer-questions"  # keeps its pre-rename name so Experiment A runs stay linked
HERO = "q-padding"
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


def check(ok: bool, what: str, *, required: bool = True) -> bool:
    RESULTS.append((ok, required, what))
    mark = "✓" if ok else ("✗" if required else "!")
    print(f"  {mark} {what}{'' if ok or required else '  (optional)'}")
    return ok


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Langfuse:
    """The few read endpoints this needs, on APIs Langfuse Cloud still serves."""

    def __init__(self) -> None:
        self.base = os.environ["LANGFUSE_BASE_URL"].rstrip("/")
        self.c = httpx.Client(base_url=self.base, timeout=60,
                              auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]))

    def get(self, path: str, **params: Any) -> Any:
        r = self.c.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def cursor(self, path: str, params: dict, cap: int = 2000) -> list[dict]:
        out, cur = [], None
        while len(out) < cap:
            d = self.get(path, **{**params, "limit": 100, **({"cursor": cur} if cur else {})})
            out += d.get("data") or []
            meta = d.get("meta") or {}
            cur = meta.get("cursor") or meta.get("nextCursor")
            if not cur or not d.get("data"):
                break
        return out

    def pages(self, path: str, params: Optional[dict] = None) -> list[dict]:
        out, page = [], 1
        while True:
            d = self.get(path, **{**(params or {}), "limit": 100, "page": page})
            out += d.get("data") or []
            if page >= ((d.get("meta") or {}).get("totalPages") or 1):
                return out
            page += 1

    def roots(self, since: datetime, extra: Optional[list[dict]] = None, cap: int = 500) -> list[dict]:
        f = [{"type": "stringOptions", "column": "name", "operator": "any of", "value": [seed.ROOT, seed.LEGACY_ROOT]},
             {"type": "datetime", "column": "startTime", "operator": ">=", "value": iso(since)}] + (extra or [])
        return self.cursor("/api/public/v2/observations", {"filter": json.dumps(f), "fields": "core,basic"}, cap)

    def trace_scores(self, trace_id: str) -> dict[str, Any]:
        rows = self.cursor("/api/public/v3/scores", {"traceId": trace_id, "fields": "core,subject"})
        return {s["name"]: s.get("value") for s in rows if (s.get("subject") or {}).get("kind") != "observation"}


async def clickhouse_checks(cfg: RunConfig) -> None:
    print("ClickHouse")
    settings = ClickHouseSettings.from_env()
    try:
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
    proj = (lf.get("/api/public/projects").get("data") or [{}])[0]
    out["project_id"] = pid = proj.get("id")
    if not check(proj.get("name") == PROJECT, f"keys map to project '{proj.get('name')}' (want '{PROJECT}')"):
        print("    the LANGFUSE_* keys in .env must be this project's (the demo's .env overrides the shell)")
        return out
    for name in (PLANNER, ANALYST):
        try:
            p = lf.get(f"/api/public/v2/prompts/{name}", label="production")
            check(True, f"prompt {name}: production = v{p.get('version')}")
        except httpx.HTTPStatusError as e:
            check(False, f"prompt {name} with label production: HTTP {e.response.status_code} "
                         "(run scripts/seed_langfuse.py --only prompts)")

    items = lf.pages("/api/public/dataset-items", {"datasetName": DATASET})
    active = [i for i in items if i.get("status", "ACTIVE") == "ACTIVE"]
    graded = [i for i in active if i.get("expectedOutput")]
    check(len(graded) == 15, f"dataset {DATASET}: {len(graded)}/{len(active)} active items with ground truth"
          " (run scripts/seed_dataset.py)" if len(graded) != 15 else
          f"dataset {DATASET}: {len(graded)} active items with ground truth")
    out["dataset_id"] = (lf.get(f"/api/public/v2/datasets/{DATASET}") or {}).get("id")

    cfgs = {c["name"] for c in lf.pages("/api/public/score-configs")}
    want = {c["name"] for c in seed.SCORE_CONFIGS}
    check(want <= cfgs, f"score configs: {len(want & cfgs)}/{len(want)}")

    dash = next((d for d in lf.pages(f"{seed.U}/dashboards") if d["name"] == seed.DASHBOARD["name"]), None)
    placed = ((lf.get(f"{seed.U}/dashboards/{dash['id']}").get("definition") or {}).get("widgets") or []) if dash else []
    check(len(placed) == len(seed.WIDGETS), f"dashboard '{seed.DASHBOARD['name']}': "
          f"{len(placed)}/{len(seed.WIDGETS)} widgets placed" + ("" if dash else " (run scripts/seed_langfuse.py)"))
    out["dashboard_id"] = dash and dash["id"]

    print("Runs (Act 1-3 open a fresh one)")
    now = datetime.now(timezone.utc)
    hero_filter = [{"type": "stringObject", "column": "metadata", "key": "question_id", "operator": "=",
                    "value": HERO}]
    try:
        hero = lf.roots(now - timedelta(days=7), hero_filter)
    except httpx.HTTPStatusError:  # metadata filter refused: fall back to any recent run
        hero = []
    recent = hero or lf.roots(now - timedelta(days=7))
    label = "hero (q-padding)" if hero else "any question"
    if check(bool(recent), f"{len(recent)} run(s) in the last 7 days, {label}"
             + ("" if recent else ": run main.py --question q-padding")):
        latest = max(recent, key=lambda o: o.get("startTime") or "")
        out["latest_trace"] = latest["traceId"]
        sc = lf.trace_scores(latest["traceId"])
        need = {"db_time_share", "scenarios_evaluated", "db_p95_ms", "recommendation_grounded"}
        age_h = (now - datetime.fromisoformat(latest["startTime"].replace("Z", "+00:00"))).total_seconds() / 3600
        ok = need <= set(sc)
        share = sc.get("db_time_share")
        check(ok, f"latest run ({age_h:.1f} h old): scenarios {sc.get('scenarios_evaluated')}, "
                  f"DB share {share if share is None else f'{float(share):.1%}'}, p95 {round(float(sc.get('db_p95_ms') or 0))} ms, "
                  f"grounded {sc.get('recommendation_grounded')}"
              + ("" if ok else f"; missing {sorted(need - set(sc))}"))
        check(age_h <= 24, "latest run is under 24 h old (re-run the hero the day before)", required=False)

    print("Optional acts")
    exps = lf.cursor("/api/public/experiments", {"datasetId": out["dataset_id"],
                                                 "fromStartTime": iso(now - timedelta(days=60))}) \
        if out.get("dataset_id") else []
    names = [e.get("name") or "" for e in exps]
    out["experiments"] = exps
    for b in ("breadth-10", "breadth-300"):
        check(any(n.startswith(b + "-") for n in names), f"Experiment A run '{b}-*' "
              f"({sum(n.startswith(b + '-') for n in names)} found)", required=False)
    conc = lf.roots(now - timedelta(days=30), [{"type": "arrayOptions", "column": "tags", "operator": "any of",
                                                "value": ["concurrency"]}], cap=200)
    out["concurrency"] = conc
    check(bool(conc), f"concurrency act: {len(conc)} agent run(s) in the last 30 days", required=False)
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
        print(f"  latest run (Act 2)  {base}/traces/{st['latest_trace']}")
    if st.get("dashboard_id"):
        print(f"  dashboard (Act 3)   {base}/dashboards/{st['dashboard_id']}")
    if st.get("dataset_id"):
        print(f"  dataset (Act 4)     {base}/datasets/{st['dataset_id']}")
        for e in sorted(st.get("experiments") or [], key=lambda e: e.get("startTime") or e.get("createdAt") or "")[-4:]:
            print(f"    run {e.get('name')}")
    print(f"  prompts             {base}/prompts")
    sessions = sorted({o.get("sessionId") for o in st.get("concurrency") or [] if o.get("sessionId")})
    for s in sessions[-3:]:
        print(f"  concurrency session {base}/sessions/{s}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", action="store_true", help="print the link each act opens")
    args = ap.parse_args(argv)
    load_env()
    cfg = RunConfig()
    lf = Langfuse()
    print(f"Target: Langfuse {lf.base} · ClickHouse table {ClickHouseSettings.from_env().qualified_table}")
    asyncio.run(clickhouse_checks(cfg))
    anthropic_check(cfg)
    st = langfuse_checks(lf, args.links)
    if args.links:
        print_links(lf, st)
    failed = [w for ok, req, w in RESULTS if not ok and req]
    warned = [w for ok, req, w in RESULTS if not ok and not req]
    print(f"\n{'READY' if not failed else 'NOT READY'}: {sum(ok for ok, _, _ in RESULTS)}/{len(RESULTS)} checks pass"
          + (f", {len(warned)} optional warning(s)" if warned else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

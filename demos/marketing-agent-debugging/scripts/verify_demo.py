"""
Readiness check for the workshop + the presenter's hero-trace list.

Reads everything back from Langfuse (not from local logs), so it also reflects runs you
made live. Uses only current read APIs: v2 observations, v3 scores, v2 prompts.

    .venv/bin/python scripts/verify_demo.py           # checklist + hero traces per case
    .venv/bin/python scripts/verify_demo.py --json    # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config  # noqa: E402
from agent.instructions import prompt_name  # noqa: E402

GREEN, RED, DIM, BOLD, END = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def _get(path, **params):
    r = config.api("GET", path, params=params)
    r.raise_for_status()
    return r.json()


def _paged(path, params, limit=1000):
    out, cursor = [], None
    while len(out) < limit:
        page = _get(path, **params, **({"cursor": cursor} if cursor else {}))
        out += page.get("data", [])
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            break
    return out


def agent_runs():
    roots = _paged("/v2/observations", {"name": "marketing-agent", "type": "AGENT", "limit": 100,
                                        "fields": "core,basic,metadata,metrics"})
    scores = defaultdict(dict)
    for name in ("reviewer-decision", "agent-steps"):
        for s in _paged("/v3/scores", {"name": name, "limit": 100, "fields": "core,details,subject"}):
            subj = s.get("subject") or {}
            if subj.get("kind") == "trace":
                scores[subj["id"]][name] = (s.get("value"), s.get("comment"))
    runs = []
    for o in roots:
        if o.get("environment") != config.ENVIRONMENT:   # rehearsal runs live in `development`
            continue
        md = o.get("metadata") or {}
        sc = scores.get(o["traceId"], {})
        runs.append({"trace_id": o["traceId"], "customer": md.get("customer"), "task": md.get("task_type"),
                     "instructions": md.get("instructions"), "fixes": md.get("fixes"),
                     "latency": o.get("latency") or 0, "level": o.get("level"),
                     "session": o.get("sessionId") or None, "tool_calls": md.get("tool_calls", ""),
                     "decision": (sc.get("reviewer-decision") or (None, None))[0],
                     "comment": (sc.get("reviewer-decision") or (None, None))[1],
                     "steps": (sc.get("agent-steps") or (None, None))[0], "start": o.get("startTime")})
    return runs


def _pick(runs, **match):
    return [r for r in runs if all(r.get(k) == v for k, v in match.items())]


def cases(runs):
    pricing = _pick(runs, task="pricing-check", fixes="none")
    refresh = _pick(runs, task="page-refresh", fixes="none")
    report = _pick(runs, task="weekly-report", fixes="none")
    faq_v3 = _pick(runs, task="faq-page", instructions="marketing-agent/tailspin v3", fixes="none")
    faq_v2 = _pick(runs, task="faq-page", instructions="marketing-agent/tailspin v2", fixes="none")
    return [
        ("1. Wrong context — did it retrieve the right info?", "contoso / pricing-check",
         sorted(pricing, key=lambda r: (r["decision"] != "rejected", -r["latency"]))[:3],
         "Open retrieve_brand_context → 2-3 of 3 passages are the ARCHIVED 2024 FAQ (metadata.hits). "
         "Then check_claims → verify-claims marks $12/$20 'supported'."),
        ("2. Latency — why is it slow?", "fabrikam / page-refresh",
         sorted(refresh, key=lambda r: -r["latency"])[:2],
         "Timeline: fetch_page ×3 back-to-back (~5 s each, cms_cache=MISS), then one step with ~45k "
         "cache-write tokens from the raw-HTML page; compare/stability-shoes is an ERROR node."),
        ("3. Loop — why does it keep going?", "tailspin / weekly-report",
         sorted(report, key=lambda r: -(r["steps"] or 0))[:2],
         "Graph (aggregated): search_answer_engine with a big counter + cycle. Tool output: progress "
         "stuck at 99%. Instructions v3: 'retry until you have it'."),
        ("4. Skipped step — why did it skip the fact-check?", "tailspin / faq-page",
         sorted(faq_v3, key=lambda r: r["decision"] != "rejected")[:2] + faq_v2[:1],
         "Generation → linked prompt marketing-agent/tailspin v3 → diff v2..v3 in Prompt Management. "
         "v2 run (last link) calls check_claims; v3 runs don't."),
    ]


def checklist(runs):
    checks = []
    for c in ("contoso", "fabrikam", "tailspin"):
        try:
            p = _get(f"/v2/prompts/{quote(prompt_name(c), safe='')}", label="production")
            checks.append((f"prompt {prompt_name(c)} production = v{p['version']}",
                           c != "tailspin" or p["version"] == 3))
        except Exception as e:  # noqa: BLE001
            checks.append((f"prompt {prompt_name(c)}: {e}", False))
    configs = {s["name"] for s in _get("/score-configs", limit=100).get("data", [])}
    checks.append(("score configs reviewer-decision / root-cause / publishing-policy",
                   {"reviewer-decision", "root-cause", "publishing-policy"} <= configs))
    queues = [q for q in _get("/annotation-queues", limit=100).get("data", []) if q["name"].startswith("PM review")]
    items = _get(f"/annotation-queues/{queues[0]['id']}/items", limit=100).get("meta", {}).get("totalItems", 0) if queues else 0
    checks.append((f"annotation queue with {items} item(s)", bool(queues) and items > 0))
    dashes = [d for d in _get("/unstable/dashboards", limit=100).get("data", []) if d["name"].startswith("Agent health")]
    checks.append(("dashboard 'Agent health — deployment view'", bool(dashes)))
    checks.append((f"{len(runs)} agent runs in the project", len(runs) >= 20))
    checks.append(("a multi-turn session exists", any(r["session"] for r in runs)))
    rejected = [r for r in runs if r["decision"] == "rejected"]
    checks.append((f"{len(rejected)} rejected runs (symptoms to start from)", len(rejected) >= 4))
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    config.verify_project()
    runs = agent_runs()
    checks = checklist(runs)
    found = cases(runs)
    if args.json:
        print(json.dumps({"checks": checks, "cases": [(t, w, [r["trace_id"] for r in rs]) for t, w, rs, _ in found]}, indent=2))
        return 0 if all(ok for _, ok in checks) else 1

    print(f"{BOLD}Readiness — project {config.EXPECTED_PROJECT}{END}  {config.project_url()}")
    for label, ok in checks:
        print(f"  {GREEN + '✓' if ok else RED + '✗'}{END} {label}")
    print(f"\n{BOLD}Hero traces{END}")
    for title, where, rs, hint in found:
        print(f"\n{BOLD}{title}{END}  {DIM}{where}{END}")
        if not rs:
            print(f"  {RED}no runs yet — seed_traffic.py --only {where.split(' / ')[1]}{END}")
        for r in rs:
            print(f"  {r['decision'] or '-':9} {r['latency']:5.0f}s {str(r['steps'] or '-'):>2} steps  "
                  f"{r['instructions'] or ''}  {config.trace_url(r['trace_id'])}")
            if r["comment"] and r["decision"] == "rejected":
                print(f"  {DIM}          “{r['comment'][:110]}”{END}")
        print(f"  {DIM}↳ {hint}{END}")
    by_task = defaultdict(lambda: defaultdict(int))
    for r in runs:
        by_task[f"{r['customer']}/{r['task']}"][r["decision"] or "-"] += 1
    print(f"\n{BOLD}Corpus{END}")
    for k in sorted(by_task):
        print(f"  {k:28} " + "  ".join(f"{d}={n}" for d, n in sorted(by_task[k].items())))
    ready = all(ok for _, ok in checks)
    print(f"\n{GREEN + 'READY' if ready else RED + 'NOT READY'}{END}")
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())

"""
Generate the workshop's trace corpus: ~26 real agent runs across three customers.

Every run is a real Claude tool loop against the synthetic customer world, so outcomes
vary run to run — that is the point. The plan below makes sure each debugging case has
several traces (some go wrong, some recover) plus ordinary happy-path traffic around
them, a multi-turn chat session, and Tailspin FAQ runs on BOTH instruction versions
(v2 before the change, v3 after) so the regression is visible in Prompt Management.

    .venv/bin/python scripts/seed_traffic.py              # full plan, 4 runs in parallel
    .venv/bin/python scripts/seed_traffic.py --only weekly-report --repeat 1
    .venv/bin/python scripts/seed_traffic.py --dry-run    # print the plan

Cost: roughly $6-8 of Claude usage for the full plan (Opus 5.5); ~10 minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config  # noqa: E402
from agent.loop import new_session_id, run_agent  # noqa: E402
from agent.scenarios import get_scenario, user_for  # noqa: E402

RUN_LOG = Path(__file__).resolve().parent.parent / ".runs.jsonl"

# (scenario, repeats, extra run_agent kwargs, all_turns)
PLAN = [
    ("pricing-check", 4, {}, False),
    ("content-brief", 1, {}, True),        # one 3-turn chat session
    ("content-brief", 1, {}, False),
    ("page-refresh", 3, {}, False),
    ("social-posts", 2, {}, False),
    ("weekly-report", 3, {}, False),
    ("faq-page", 3, {"prompt_version": 2}, False),   # before the instructions change
    ("faq-page", 3, {}, False),                       # production = v3 (after)
    ("competitor-check", 2, {}, False),
]

ADHOC = [
    ("contoso", "How visible are we for 'project management software with client portals' on Gemini "
                "compared with ChatGPT?", "dana.reyes@contoso.example"),
    ("fabrikam", "Which of our pages get cited by AI answer engines today, and for which prompts?",
     "sam.ortiz@fabrikam.example"),
]


def _jobs(only, repeat):
    jobs = []
    for name, n, kwargs, all_turns in PLAN:
        if only and name not in only:
            continue
        for _ in range(repeat if repeat is not None else n):
            jobs.append(("scenario", name, kwargs, all_turns))
    if not only:
        for customer, task, user in ADHOC:
            jobs.append(("adhoc", customer, {"task": task, "user": user}, False))
    return jobs


def _run_job(job):
    kind, name, kwargs, all_turns = job
    results = []
    if kind == "adhoc":
        r = run_agent(name, kwargs["task"], task_kind="adhoc", user_id=kwargs["user"])
        return [(name, "adhoc", r)]
    s = get_scenario(name)
    turns = [s.task] + (s.follow_ups if all_turns else [])
    session = new_session_id(s.customer) if all_turns else None
    history = []
    for task in turns:
        r = run_agent(s.customer, task, task_kind=s.name, user_id=user_for(s), session_id=session,
                      history=history, **kwargs)
        results.append((s.customer, s.name, r))
        history += [{"role": "user", "content": task}, {"role": "assistant", "content": r.final_text}]
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="scenario name(s) to run")
    ap.add_argument("--repeat", type=int, help="override the repeat count for each planned scenario")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    jobs = _jobs(args.only, args.repeat)
    if args.dry_run:
        for j in jobs:
            print(j[0], j[1], j[2] or "", "(all turns)" if j[3] else "")
        print(f"{len(jobs)} jobs")
        return 0

    config.verify_project()
    started, failures = time.monotonic(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool, RUN_LOG.open("a") as log:
        futures = {pool.submit(_run_job, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                for customer, scenario, r in fut.result():
                    row = {"customer": customer, "scenario": scenario, "trace_id": r.trace_id,
                           "decision": r.decision, "comment": r.decision_comment, "steps": r.steps,
                           "step_limit_hit": r.step_limit_hit, "duration_s": round(r.duration_s, 1),
                           "instructions_version": r.instructions_version,
                           "tools": [c["name"] for c in r.state.calls]}
                    log.write(json.dumps(row) + "\n")
                    log.flush()
                    print(f"{scenario:17} {customer:9} {r.decision or '-':9} {r.steps:2} steps "
                          f"{r.duration_s:5.0f}s  {r.url}")
            except Exception:
                failures += 1
                print(f"FAILED {job}:\n{traceback.format_exc()}")
    config.get_langfuse().flush()
    print(f"\n{len(jobs) - failures}/{len(jobs)} jobs ok in {time.monotonic() - started:.0f}s; log → {RUN_LOG.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

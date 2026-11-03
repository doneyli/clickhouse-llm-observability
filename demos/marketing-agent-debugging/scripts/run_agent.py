"""
Run the marketing agent once, live, and print the Langfuse trace URL.

    .venv/bin/python scripts/run_agent.py --scenario pricing-check
    .venv/bin/python scripts/run_agent.py --scenario pricing-check --fix exclude-archived
    .venv/bin/python scripts/run_agent.py --scenario page-refresh --fix parallel-tools --fix clean-html
    .venv/bin/python scripts/run_agent.py --scenario faq-page --prompt-version 2
    .venv/bin/python scripts/run_agent.py --customer contoso --task "How visible are we on Gemini?"
    .venv/bin/python scripts/run_agent.py --scenario content-brief --all-turns   # one session, 3 traces

Fixes: exclude-archived, clean-html, parallel-tools, terminal-pending (see agent/tools.py).
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config  # noqa: E402
from agent.loop import new_session_id, run_agent  # noqa: E402
from agent.scenarios import SCENARIOS, get_scenario, user_for  # noqa: E402
from agent.tools import FIXES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--customer", choices=["contoso", "fabrikam", "tailspin"])
    ap.add_argument("--task", help="free-text task (with --customer)")
    ap.add_argument("--fix", action="append", default=[], choices=sorted(FIXES))
    ap.add_argument("--prompt-label", default="production")
    ap.add_argument("--prompt-version", type=int)
    ap.add_argument("--session", help="reuse a session id (chat thread)")
    ap.add_argument("--all-turns", action="store_true", help="also run the scenario's follow-up turns")
    ap.add_argument("--no-review", action="store_true", help="skip the simulated reviewer score")
    ap.add_argument("--public", action="store_true", help="make the trace viewable via a public link")
    args = ap.parse_args()

    if args.scenario:
        s = get_scenario(args.scenario)
        customer, kind, turns, user = s.customer, s.name, [s.task] + (s.follow_ups if args.all_turns else []), user_for(s)
    elif args.customer and args.task:
        customer, kind, turns, user = args.customer, "adhoc", [args.task], None
    else:
        ap.error("give --scenario, or --customer with --task")

    config.verify_project()
    session = args.session or (new_session_id(customer) if len(turns) > 1 or args.session else None)
    history = []
    for turn, task in enumerate(turns):
        print(f"\n[{customer}] {task}")
        result = run_agent(customer, task, task_kind=kind, user_id=user, session_id=session, history=history,
                           fixes=args.fix, prompt_label=args.prompt_label, prompt_version=args.prompt_version,
                           do_review=not args.no_review, on_event=lambda m: print("   ", m))
        if args.public:
            config.api("PATCH", f"/traces/{result.trace_id}", json={"public": True})
        print("\n" + textwrap.indent(textwrap.fill(result.final_text, 100, replace_whitespace=False)[:1500], "  │ "))
        if result.decision:
            print(f"\n  reviewer: {result.decision} — {result.decision_comment}")
        print(f"  trace:    {result.url}")
        history += [{"role": "user", "content": task}, {"role": "assistant", "content": result.final_text}]
    config.get_langfuse().flush()
    if session:
        print(f"  session:  {config.project_url(f'/sessions/{session}')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

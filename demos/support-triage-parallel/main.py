"""
Support Triage Parallel — CLI runner (Pattern #3: Parallelization).

One trace per ticket named ``triage-support-ticket`` (stable, low-cardinality;
the ticket goes in the trace *input*). Each run stages two parallelization
sub-variants under that trace:

  Stage 1  sectioning fan-out (4 concurrent branches) -> synthesis brief
  Stage 2  best-of-N SQL voting -> validate (EXPLAIN) -> tally -> tie-break

Usage:
    docker compose --profile demo run --rm support-triage-parallel python main.py
    docker compose --profile demo run --rm support-triage-parallel python main.py --interactive
    docker compose --profile demo run --rm support-triage-parallel python main.py --sequential --ticket TCK-001
    FAULT=slow-branch docker compose --profile demo run --rm support-triage-parallel python main.py --ticket TCK-005

Runs green with NO Langfuse keys set (instrumentation degrades to a no-op).
"""

import argparse
import asyncio
import os
import sys

import langfuse_config as lf
from tickets import TICKETS, get_ticket
from triage_pipeline import analyze_sections
from sql_voting import vote_sql, VOTE_STRATEGY, VOTE_SAMPLES

LANGFUSE_URL = os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001")


def _fault() -> str:
    return os.getenv("FAULT", "").strip().lower()


async def triage_ticket(ticket: dict, session_id: str, sequential: bool = False) -> dict:
    """Run the full triage over one ticket under a single Langfuse trace."""
    fault = _fault()
    tags = ["support-triage-parallel", "demo"]
    if fault:
        tags.append(f"fault:{fault}")

    with lf.trace_context("triage-support-ticket", session_id=session_id, tags=tags):
        with lf.observe(
            "triage-support-ticket", as_type="span",
            input={"ticket_id": ticket["id"], "subject": ticket["subject"],
                   "body": ticket["body"], "data_question": ticket["data_question"]},
        ) as root:
            sections = await analyze_sections(ticket, sequential=sequential, fault=fault)
            vote = await vote_sql(ticket["data_question"], strategy=VOTE_STRATEGY)

            output = {
                "triage_brief": sections["brief"],
                "winning_sql": vote.get("winning_sql"),
                "result_signature": vote.get("result_signature"),
                "consensus_confidence": vote.get("consensus_confidence"),
                "failed_branches": sections["failed_branches"],
                "degraded": sections["degraded"],
            }
            root.update(output=output)

    return {"ticket": ticket, "sections": sections, "vote": vote}


def _print_result(res: dict, sequential: bool):
    ticket = res["ticket"]
    s, v = res["sections"], res["vote"]
    mode = "sequential" if sequential else "parallel"
    tally = " ".join(f"{k}:{n}" for k, n in sorted(v["tally"].items(), key=lambda x: -x[1])) or "none"
    print(f"  fan-out {4 - s['failed_branches']}/4 branches ({mode}) | "
          f"{s['wall_s']:.1f}s wall / {s['sum_s']:.1f}s sum "
          f"| dropped={s['failed_branches']} degraded={s['degraded']}")
    print(f"  vote {tally} | invalid={v['invalid']} | winner={v['winner']} "
          f"| margin={v['margin']} | tie_break={v['tie_break_used']} "
          f"| consensus={v['consensus_confidence']:.2f}")
    print(f"  winning_sql: {(v.get('winning_sql') or '(none)')[:120]}")
    print(f"\n  Triage brief:\n{_indent(s['brief'])}\n")


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in (text or "").splitlines())


async def run_batch(tickets, sequential: bool):
    session = lf.new_session_id()
    print("\n" + "=" * 66)
    print("Support Triage Parallel  (sectioning fan-out + best-of-N SQL voting)")
    print(f"strategy={VOTE_STRATEGY}  n_samples={VOTE_SAMPLES}  "
          f"mode={'sequential' if sequential else 'parallel'}"
          + (f"  fault={_fault()}" if _fault() else ""))
    if lf.is_langfuse_enabled():
        print("+ Langfuse instrumentation enabled")
    print("=" * 66)

    for i, ticket in enumerate(tickets, 1):
        print(f"\n[{i}/{len(tickets)}] {ticket['id']} — {ticket['subject']}")
        print("-" * 58)
        try:
            res = await triage_ticket(ticket, session_id=session, sequential=sequential)
            _print_result(res, sequential)
        except Exception as e:
            print(f"  Error: {e}")

    lf.flush()
    print("=" * 66)
    if lf.is_langfuse_enabled():
        print(f"View traces: {LANGFUSE_URL} (Langfuse → Traces → triage-support-ticket)")
    print("=" * 66 + "\n")


async def run_interactive(sequential: bool):
    session = lf.new_session_id()
    print("\nInteractive triage — enter a ticket body; blank data question is fine.")
    print("Type 'quit' to exit.\n")
    while True:
        try:
            body = input("Ticket body: ").strip()
            if body.lower() in ("quit", "exit", "q"):
                break
            if not body:
                continue
            dq = input("Data question (optional): ").strip() or "No data question."
            ticket = {"id": "TCK-INT", "subject": body[:48], "body": body, "data_question": dq}
            res = await triage_ticket(ticket, session_id=session, sequential=sequential)
            _print_result(res, sequential)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")
    lf.flush()


def main():
    ap = argparse.ArgumentParser(description="Support Triage Parallel demo")
    ap.add_argument("--interactive", action="store_true", help="Interactive mode")
    ap.add_argument("--sequential", action="store_true",
                    help="Run branches one-by-one (baseline for the wall-clock A/B)")
    ap.add_argument("--ticket", type=str, default=None,
                    help="Run a single ticket by id, e.g. TCK-002")
    args = ap.parse_args()

    if args.interactive:
        asyncio.run(run_interactive(args.sequential))
        return

    if args.ticket:
        ticket = get_ticket(args.ticket)
        if ticket is None:
            print(f"Unknown ticket id: {args.ticket}", file=sys.stderr)
            sys.exit(1)
        tickets = [ticket]
    else:
        tickets = TICKETS

    asyncio.run(run_batch(tickets, args.sequential))


if __name__ == "__main__":
    main()

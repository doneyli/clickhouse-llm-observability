"""
Cluster Health Investigator — CLI runner.

Usage:
    docker compose --profile langfuse --profile demo run --rm cluster-health python main.py
    docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --interactive
    docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --symptom "inserts are slow and CPU is pinned"
    docker compose --profile langfuse --profile demo run --rm cluster-health python main.py --fault overplan

Investigates the stack's own `langfuse-clickhouse` (env-swappable via TARGET_CH_*).
The terminal step log is the money-line before you open Langfuse: it shows the
plan size and worker fan-out the planner chose for each symptom — which VARIES
per input. That variance is the whole point (see DEMO_SYMPTOMS.md).
"""

import argparse
import sys

from graph import create_investigator
import langfuse_config as lf

# The 6 headline symptoms (calibrated worker-count ranges live in DEMO_SYMPTOMS.md).
# Ordered narrow → broad so the fan-out visibly grows down the batch.
DEMO_SYMPTOMS = [
    "One Grafana dashboard query got slow this afternoon; everything else feels fine.",   # ~1-2
    "We're seeing occasional query exceptions in the last hour but throughput looks ok.",  # ~2
    "Mutations look stuck on one table and ALTERs never seem to finish.",                  # ~2-3 (re-plan trigger)
    "Ingest latency crept up over the last day and a couple of merges seem to hang.",      # ~3-4
    "Inserts got slow after last night's deploy, CPU is pinned and disk is filling.",      # ~5-6
    "The whole cluster feels unhealthy since yesterday — slow, erroring, and bloated.",    # ~6
]


def _print_result(res: dict):
    tasks = res.get("plan", [])
    workers = res.get("workers_spawned", 0)
    rounds = res.get("rounds", 1)
    types = ", ".join(t.get("analysis_type", "?") for t in tasks)
    print(f"  plan → {len(tasks)} task(s) [{types}] | worker×{workers} "
          f"| rounds={rounds} | {' | '.join(res.get('steps', [])[-3:])}")
    diag = (res.get("diagnosis") or "").strip().replace("\n", " ")
    print(f"  diagnosis: {diag[:200]}{'…' if len(diag) > 200 else ''}\n")


def run_batch(inv, fault=None):
    print("\n" + "=" * 72)
    print("Cluster Health Investigator  (Orchestrator-Workers · LangGraph Send API)")
    print("Target: langfuse-clickhouse  —  ClickHouse diagnosing its own trace store")
    if lf.is_langfuse_enabled():
        print("+ Langfuse agentic instrumentation enabled (Agent Graph → worker (N/N))")
    print("=" * 72)
    session = lf.new_session_id()
    for i, symptom in enumerate(DEMO_SYMPTOMS, 1):
        print(f"\n[{i}/{len(DEMO_SYMPTOMS)}] {symptom}")
        print("-" * 68)
        try:
            res = inv.run(symptom, session_id=session, fault=fault)
            _print_result(res)
        except Exception as e:
            print(f"  Error: {e}\n")
    print("=" * 72)
    if lf.is_langfuse_enabled():
        print("Open the Agent Graph (Aggregated view): http://localhost:3001 → Traces")
        print("Compare a narrow symptom's worker (2/2) with a broad one's worker (6/6).")
    print("=" * 72 + "\n")


def run_interactive(inv):
    print("\nInteractive Cluster Health Investigator — type 'quit' to exit\n")
    session = lf.new_session_id()
    while True:
        try:
            symptom = input("Symptom: ").strip()
            if symptom.lower() in ("quit", "exit", "q"):
                break
            if not symptom:
                continue
            _print_result(inv.run(symptom, session_id=session))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")


def main():
    ap = argparse.ArgumentParser(description="Cluster Health Investigator (orchestrator-workers)")
    ap.add_argument("--interactive", action="store_true", help="Interactive symptom prompt")
    ap.add_argument("--symptom", type=str, default=None, help="Investigate a single symptom")
    ap.add_argument("--fault", type=str, default=None, choices=["overplan"],
                    help="Inject a fault: 'overplan' removes the planner's scaling rules → max fan-out")
    args = ap.parse_args()

    print("Initializing investigator (planner + workers + synthesizer)...")
    inv = create_investigator()

    if args.symptom:
        res = inv.run(args.symptom, fault=args.fault)
        print()
        _print_result(res)
        print(f"Diagnosis:\n{res.get('diagnosis', '')}\n")
    elif args.interactive:
        run_interactive(inv)
    else:
        run_batch(inv, fault=args.fault)


if __name__ == "__main__":
    # Flush on the way out, always. Short-lived `docker compose run --rm` process +
    # background batch export = traces dropped at interpreter exit without this.
    try:
        main()
    finally:
        lf.flush()

"""
ClickHouse Text-to-SQL Demo with Langfuse

Entry point for the Text-to-SQL application (prompt chaining WITH gate checks).
Instrumentation via Langfuse SDK CallbackHandler.

Two modes — the gates run in BOTH; --refine only changes where the response's
context comes from:
    default   analyze -> [Gate 1] -> retrieve catalog -> respond -> [Gate 2]
    --refine  analyze -> [Gate 1] -> generate/critique/refine -> respond -> [Gate 2]
              Pattern #5 (evaluator-optimizer): the catalog lookup is replaced by
              a loop grounded in real EXPLAIN + bounded execution, so Gate 2
              grades against executed rows rather than table names. Convergence
              is pushed as trace scores; each iteration renders as a
              generate-sql -> gather-evidence -> critique-sql triplet.

Flags:
    --interactive        Interactive query mode instead of the batch.
    --refine             Enable the Pattern #5 loop (executes bounded read-only SQL).
    --fault <name>       Force a deterministic failure for the demo. Traces are
                         tagged fault:<name>. Values:
                           vague-analysis   -> Gate 1 (catalog) fails, then abort
                           destructive-sql  -> Gate 2 (SQL policy) fails, then escalate
                           wrong-column     -> refine loop needs extra iterations
                                               (--refine only)
"""

import argparse
import os

# Langfuse instrumentation
from langfuse_config import get_langfuse_handler, langfuse_trace, is_langfuse_enabled, flush as langfuse_flush

# Gate faults act on the analysis/response steps, so they apply in BOTH modes and
# reach the pipeline through the DEMO_FAULT env var (read by _apply_fault).
GATE_FAULTS = ("vague-analysis", "destructive-sql")
# Refine faults are injected inside the loop itself, so they are passed as an
# argument and only mean anything under --refine.
REFINE_FAULTS = ("wrong-column",)

# Demo questions covering different ClickHouse public databases
DEMO_QUESTIONS = [
    "What are the most expensive areas for property in London?",           # uk
    "What is the average taxi trip distance in New York City?",             # nyc_taxi
    "Which programming languages have the most Stack Overflow questions?",  # stackoverflow
    "How has GitHub activity changed over the past year?",                  # github
    "What are the highest-scored stories on Hacker News this year?",        # hackernews
    "Which US airlines have the highest rate of flight delays?",            # ontime
    "What are the top-rated movies on IMDB?",                              # imdb
    "What are the most downloaded Python packages?",                       # pypi
    "Which YouTube videos have the most views?",                           # youtube
    "How have stock prices for the top tech companies trended?",           # stock
]

# Refine-mode questions, chosen so all three trajectories reliably appear against
# the live public datasets (see DEMO_SCRIPT.md Act 2).
REFINE_DEMO_QUESTIONS = [
    # Fast converge (1 iteration): trivial count(), passes every check first try.
    "How many property sales are recorded in the UK price paid dataset?",
    # Fast converge (1 iteration): simple GROUP BY on a well-known table.
    "What are the 10 most-answered tags on Stack Overflow?",
    # Multi-iteration refine: generators habitually guess city/wrong table; EXPLAIN
    # returns UNKNOWN_IDENTIFIER -> critique cites it -> iteration 2 fixes town/uk.
    "Which town had the highest average property price in 2021?",
    # Non-converging: no Uber data, no current-month data -> hits MAX_ITERATIONS.
    "What was the average Uber fare in Manhattan last month?",
    # Non-converging: data not in the playground -> empty/irrelevant results.
    "Which ClickHouse Cloud customers ran the most queries yesterday?",
]


def _trace_tags(fault=None, refine=False):
    """Trace tags — the demo baseline, plus refine-loop and fault:<name>."""
    tags = ["text-to-sql", "demo"]
    if refine:
        tags.append("refine-loop")
    if fault:
        tags.append(f"fault:{fault}")
    return tags


def _print_gate_log(pipeline):
    """Narrate the gate verdicts the trace also shows — one line per attempt."""
    for g in getattr(pipeline, "gate_log", []):
        print(f"  [{g['gate']}] {g['verdict']} (attempt {g['attempt']}) — {g['reason']}")


def _run_one(pipeline, question, callbacks, refine_fault, tags):
    """One traced query, then narrate its gates."""
    with langfuse_trace(tags=tags):
        response = pipeline.query(question, callbacks=callbacks, fault=refine_fault)
    _print_gate_log(pipeline)
    return response


def run_demo(pipeline, refine=False, fault=None, refine_fault=None):
    """Run demo queries."""

    questions = REFINE_DEMO_QUESTIONS if refine else DEMO_QUESTIONS
    tags = _trace_tags(fault, refine)

    print("\n" + "="*60)
    print("ClickHouse Text-to-SQL Demo (chaining + gate checks)"
          + ("  [refine mode]" if refine else ""))
    if is_langfuse_enabled():
        print("+ Langfuse instrumentation enabled")
    if fault:
        print(f"! Fault injection active: {fault}")
    print("="*60)

    for i, question in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] {question}")
        print("-"*50)

        # Get Langfuse callback if enabled
        langfuse_handler = get_langfuse_handler()
        callbacks = [langfuse_handler] if langfuse_handler else None

        try:
            response = _run_one(pipeline, question, callbacks, refine_fault, tags)
            print(f"Response: {response[:400]}...")
        except Exception as e:
            print(f"Error: {e}")

    # Flush Langfuse events
    langfuse_flush()

    print("\n" + "="*60)
    print("Demo complete!")
    if is_langfuse_enabled():
        print("   View traces: http://localhost:3001 (Langfuse)")
    print("="*60 + "\n")


def run_interactive(pipeline, refine=False, fault=None, refine_fault=None):
    """Interactive query mode."""

    tags = _trace_tags(fault, refine)

    print("\nInteractive Mode" + ("  [refine mode]" if refine else "")
          + " - Type 'quit' to exit\n")
    if fault:
        print(f"! Fault injection active: {fault}\n")

    while True:
        try:
            question = input("Question: ").strip()

            if question.lower() in ('quit', 'exit', 'q'):
                break
            if not question:
                continue

            # Get Langfuse callback if enabled
            langfuse_handler = get_langfuse_handler()
            callbacks = [langfuse_handler] if langfuse_handler else None

            response = _run_one(pipeline, question, callbacks, refine_fault, tags)
            print(f"\nResponse: {response}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")

    # Flush Langfuse events on exit
    langfuse_flush()


def parse_args():
    parser = argparse.ArgumentParser(description="ClickHouse Text-to-SQL demo (chaining + gates)")
    parser.add_argument("--interactive", action="store_true",
                        help="Interactive query mode instead of the demo batch")
    parser.add_argument("--refine", action="store_true",
                        help="Pattern #5 generate->critique->refine loop (executes bounded read-only SQL)")
    parser.add_argument("--fault", choices=GATE_FAULTS + REFINE_FAULTS,
                        default=None,
                        help="Force a deterministic failure (tags traces fault:<name>)")
    return parser.parse_args()


def main():
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║   ClickHouse Text-to-SQL Demo                             ║
    ║                                                           ║
    ║   - Langfuse: LLM observability (ClickHouse backend)      ║
    ║   - Prompt chaining with gate checks (retry/abort/escalate)║
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)

    args = parse_args()

    # REFINE_MODE is read at import time in sql_pipeline, so set it BEFORE the
    # import below — which is also why create_pipeline is imported lazily here.
    if args.refine:
        os.environ["REFINE_MODE"] = "1"

    fault = args.fault or os.getenv("DEMO_FAULT") or None
    if fault in REFINE_FAULTS and not args.refine:
        print(f"note: --fault {fault} only applies in --refine mode; ignoring.")
        fault = None

    # The two fault families reach the pipeline by different routes: gate faults
    # via the environment (read by _apply_fault when building the step prompts),
    # refine faults as an argument threaded into the loop.
    refine_fault = fault if fault in REFINE_FAULTS else None
    if fault in GATE_FAULTS:
        os.environ["DEMO_FAULT"] = fault

    from sql_pipeline import create_pipeline
    pipeline = create_pipeline()

    # Run mode
    if args.interactive:
        run_interactive(pipeline, refine=args.refine, fault=fault, refine_fault=refine_fault)
    else:
        run_demo(pipeline, refine=args.refine, fault=fault, refine_fault=refine_fault)


if __name__ == "__main__":
    main()

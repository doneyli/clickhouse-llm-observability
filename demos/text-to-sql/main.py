"""
ClickHouse Text-to-SQL Demo with Langfuse

Entry point for the Text-to-SQL application.
Instrumentation via Langfuse SDK CallbackHandler.

Two modes:
  default  — the legacy analyze -> retrieve catalog -> respond pipeline.
  --refine — Pattern #5 (evaluator-optimizer): analyze -> generate -> critique
             (grounded in real EXPLAIN + bounded execution) -> refine, until
             accepted or a max-iterations/oscillation guard trips. Convergence is
             pushed as trace scores; each iteration renders as a
             generate-sql -> gather-evidence -> critique-sql triplet in Langfuse.
"""

import argparse
import os

# Demo questions covering different ClickHouse public databases (legacy path)
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


def _run_one(pipeline, question, callbacks, fault, tags):
    from langfuse_config import langfuse_trace
    with langfuse_trace(tags=tags):
        return pipeline.query(question, callbacks=callbacks, fault=fault)


def run_demo(pipeline, refine=False, fault=None):
    """Run demo queries."""
    from langfuse_config import (
        get_langfuse_handler, is_langfuse_enabled, flush as langfuse_flush,
    )

    questions = REFINE_DEMO_QUESTIONS if refine else DEMO_QUESTIONS
    tags = ["text-to-sql", "demo"] + (["refine-loop"] if refine else [])
    if fault:
        tags = tags + [f"fault:{fault}"]

    print("\n" + "="*60)
    print("ClickHouse Text-to-SQL Demo" + ("  [refine mode]" if refine else ""))
    if is_langfuse_enabled():
        print("+ Langfuse instrumentation enabled")
    print("="*60)

    for i, question in enumerate(questions, 1):
        print(f"\n[{i}/{len(questions)}] {question}")
        print("-"*50)

        langfuse_handler = get_langfuse_handler()
        callbacks = [langfuse_handler] if langfuse_handler else None

        try:
            response = _run_one(pipeline, question, callbacks, fault, tags)
            print(f"Response: {response[:400]}...")
        except Exception as e:
            print(f"Error: {e}")

    langfuse_flush()

    print("\n" + "="*60)
    print("Demo complete!")
    if is_langfuse_enabled():
        print("   View traces: http://localhost:3001 (Langfuse)")
    print("="*60 + "\n")


def run_interactive(pipeline, refine=False, fault=None):
    """Interactive query mode."""
    from langfuse_config import get_langfuse_handler, flush as langfuse_flush

    tags = ["text-to-sql", "demo"] + (["refine-loop"] if refine else [])
    if fault:
        tags = tags + [f"fault:{fault}"]

    print("\nInteractive Mode" + ("  [refine mode]" if refine else "")
          + " - Type 'quit' to exit\n")

    while True:
        try:
            question = input("Question: ").strip()

            if question.lower() in ('quit', 'exit', 'q'):
                break
            if not question:
                continue

            langfuse_handler = get_langfuse_handler()
            callbacks = [langfuse_handler] if langfuse_handler else None

            response = _run_one(pipeline, question, callbacks, fault, tags)
            print(f"\nResponse: {response}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")

    langfuse_flush()


def main():
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║   ClickHouse Text-to-SQL Demo                             ║
    ║                                                           ║
    ║   - Langfuse: LLM observability (ClickHouse backend)      ║
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)

    parser = argparse.ArgumentParser(description="ClickHouse Text-to-SQL demo")
    parser.add_argument("--interactive", action="store_true", help="Interactive query mode")
    parser.add_argument("--refine", action="store_true",
                        help="Pattern #5 generate->critique->refine loop (executes bounded read-only SQL)")
    parser.add_argument("--fault", default=None, choices=["wrong-column"],
                        help="Inject a deterministic fault (refine mode) to guarantee a multi-iteration beat")
    args = parser.parse_args()

    # REFINE_MODE is read at import time in sql_pipeline, so set it BEFORE importing.
    if args.refine:
        os.environ["REFINE_MODE"] = "1"
    if args.fault and not args.refine:
        print("note: --fault only applies in --refine mode; ignoring.")
        args.fault = None

    from sql_pipeline import create_pipeline
    pipeline = create_pipeline()

    if args.interactive:
        run_interactive(pipeline, refine=args.refine, fault=args.fault)
    else:
        run_demo(pipeline, refine=args.refine, fault=args.fault)


if __name__ == "__main__":
    main()

"""
ClickHouse Text-to-SQL Demo with Langfuse

Entry point for the Text-to-SQL application (prompt chaining WITH gate checks).
Instrumentation via Langfuse SDK CallbackHandler.

Flags:
    --interactive        Interactive query mode instead of the batch.
    --fault <name>       Force a gate failure for the demo. Sets DEMO_FAULT and
                         tags traces fault:<name>. Values:
                           vague-analysis   -> Gate 1 (catalog) fails, then abort
                           destructive-sql  -> Gate 2 (SQL policy) fails, then escalate
"""

import argparse
import os

from sql_pipeline import create_pipeline

# Langfuse instrumentation
from langfuse_config import get_langfuse_handler, langfuse_trace, is_langfuse_enabled, flush as langfuse_flush

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


def _trace_tags(fault=None):
    """Trace tags — the demo baseline plus fault:<name> when injecting a fault."""
    tags = ["text-to-sql", "demo"]
    if fault:
        tags.append(f"fault:{fault}")
    return tags


def _print_gate_log(pipeline):
    """Narrate the gate verdicts the trace also shows — one line per attempt."""
    for g in getattr(pipeline, "gate_log", []):
        print(f"  [{g['gate']}] {g['verdict']} (attempt {g['attempt']}) — {g['reason']}")


def run_demo(pipeline, fault=None):
    """Run demo queries."""

    print("\n" + "="*60)
    print("ClickHouse Text-to-SQL Demo (chaining + gate checks)")
    if is_langfuse_enabled():
        print("+ Langfuse instrumentation enabled")
    if fault:
        print(f"! Fault injection active: {fault}")
    print("="*60)

    for i, question in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[{i}/{len(DEMO_QUESTIONS)}] {question}")
        print("-"*50)

        # Get Langfuse callback if enabled
        langfuse_handler = get_langfuse_handler()
        callbacks = [langfuse_handler] if langfuse_handler else None

        try:
            with langfuse_trace(tags=_trace_tags(fault)):
                response = pipeline.query(question, callbacks=callbacks)
            _print_gate_log(pipeline)
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


def run_interactive(pipeline, fault=None):
    """Interactive query mode."""

    print("\nInteractive Mode - Type 'quit' to exit\n")
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

            with langfuse_trace(tags=_trace_tags(fault)):
                response = pipeline.query(question, callbacks=callbacks)
            _print_gate_log(pipeline)
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
    parser.add_argument("--fault", choices=["vague-analysis", "destructive-sql"],
                        default=None,
                        help="Force a gate failure (sets DEMO_FAULT, tags traces fault:<name>)")
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

    # Fault injection is read from the environment by the pipeline, so set it
    # before creating the pipeline. (Also honours a pre-set DEMO_FAULT env var.)
    fault = args.fault or os.getenv("DEMO_FAULT") or None
    if fault:
        os.environ["DEMO_FAULT"] = fault

    # Create pipeline
    pipeline = create_pipeline()

    # Run mode
    if args.interactive:
        run_interactive(pipeline, fault=fault)
    else:
        run_demo(pipeline, fault=fault)


if __name__ == "__main__":
    main()

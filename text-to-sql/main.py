"""
ClickHouse Text-to-SQL Demo with Langfuse

Entry point for the Text-to-SQL application.
Instrumentation via Langfuse SDK CallbackHandler.
"""

import os
import sys

from sql_pipeline import create_pipeline

# Langfuse instrumentation
from langfuse_config import get_langfuse_handler, is_langfuse_enabled, flush as langfuse_flush

# Demo questions covering different databases
DEMO_QUESTIONS = [
    "What are the most expensive areas for property in London?",
    "How has GitHub activity changed over the past year?",
    "What are the busiest airports based on flight data?",
]


def run_demo(pipeline):
    """Run demo queries."""

    print("\n" + "="*60)
    print("ClickHouse Text-to-SQL Demo")
    if is_langfuse_enabled():
        print("+ Langfuse instrumentation enabled")
    print("="*60)

    for i, question in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[{i}/{len(DEMO_QUESTIONS)}] {question}")
        print("-"*50)

        # Get Langfuse callback if enabled
        langfuse_handler = get_langfuse_handler()
        callbacks = [langfuse_handler] if langfuse_handler else None

        try:
            response = pipeline.query(question, callbacks=callbacks)
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


def run_interactive(pipeline):
    """Interactive query mode."""

    print("\nInteractive Mode - Type 'quit' to exit\n")

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

            response = pipeline.query(question, callbacks=callbacks)
            print(f"\nResponse: {response}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")

    # Flush Langfuse events on exit
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

    # Create pipeline
    pipeline = create_pipeline()

    # Run mode
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        run_interactive(pipeline)
    else:
        run_demo(pipeline)


if __name__ == "__main__":
    main()

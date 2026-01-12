"""
ClickHouse Text-to-SQL Demo with TruLens, OpenLLMetry & Langfuse

Entry point for the Text-to-SQL application.
Supports dual instrumentation: OpenLLMetry → ClickStack and Langfuse → Langfuse.
"""

import os
import sys

# ============================================================
# CRITICAL: Setup instrumentation BEFORE importing LangChain!
# ============================================================
from instrumentation import setup_instrumentation
setup_instrumentation()

# Now safe to import LangChain and other modules
from sql_pipeline import create_pipeline
from trulens_config import TruLensConfig, create_feedback_functions, InstrumentedSQLPipeline
from trulens.core import TruSession
from trulens.apps.app import TruApp

# Langfuse dual instrumentation (optional - enabled via env vars)
from langfuse_config import get_langfuse_handler, is_langfuse_enabled, flush as langfuse_flush

# Demo questions covering different databases
DEMO_QUESTIONS = [
    "What are the most expensive areas for property in London?",
    "How has GitHub activity changed over the past year?",
    "What are the busiest airports based on flight data?",
]


def create_app():
    """Create the Text-to-SQL application with full instrumentation."""

    # 1. Create base pipeline
    base_pipeline = create_pipeline()

    # 2. Wrap with TruLens instrumentation
    trulens_config = TruLensConfig()
    instrumented = InstrumentedSQLPipeline(base_pipeline, trulens_config)

    # 3. Create TruLens session with shared database
    database_url = os.getenv("TRULENS_DATABASE_URL", "sqlite:///default.sqlite")
    session = TruSession(database_url=database_url)
    feedbacks = create_feedback_functions(trulens_config)

    # 4. Create TruApp wrapper
    tru_app = TruApp(
        instrumented,
        app_name=trulens_config.app_name,
        app_version=trulens_config.app_version,
        feedbacks=feedbacks
    )

    return instrumented, tru_app, session


def run_demo(pipeline, tru_app):
    """Run demo queries with evaluation."""

    print("\n" + "="*60)
    print("ClickHouse Text-to-SQL Demo with TruLens & OpenLLMetry")
    if is_langfuse_enabled():
        print("+ Langfuse dual instrumentation enabled")
    print("="*60)

    for i, question in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[{i}/{len(DEMO_QUESTIONS)}] {question}")
        print("-"*50)

        # Get Langfuse callback if enabled
        langfuse_handler = get_langfuse_handler(
            tags=["text-to-sql-demo", "demo-run"],
            metadata={"question_number": i}
        )
        callbacks = [langfuse_handler] if langfuse_handler else None

        with tru_app as recording:
            try:
                response = pipeline.query(question, callbacks=callbacks)
                print(f"Response: {response[:400]}...")
            except Exception as e:
                print(f"Error: {e}")

        # Show evaluation results
        try:
            record = recording.get()
            if record and record.feedback_results:
                print("\nEvaluations:")
                for name, result in record.feedback_results.items():
                    score = getattr(result, 'result', 'pending')
                    print(f"   {name}: {score}")
        except Exception:
            pass

    # Flush Langfuse events
    langfuse_flush()

    print("\n" + "="*60)
    print("Demo complete!")
    print("   View traces: http://localhost:8080 (HyperDX)")
    print("   View evals:  http://localhost:8501 (TruLens)")
    if is_langfuse_enabled():
        print("   View traces: http://localhost:3001 (Langfuse)")
    print("="*60 + "\n")


def run_interactive(pipeline, tru_app):
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
            langfuse_handler = get_langfuse_handler(
                tags=["text-to-sql-demo", "interactive"]
            )
            callbacks = [langfuse_handler] if langfuse_handler else None

            with tru_app as recording:
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
    ║   - OpenLLMetry: Auto-captures prompts, tokens            ║
    ║   - TruLens: Evaluates relevance, coherence               ║
    ║   - ClickStack: Unified observability in ClickHouse       ║"""

    if is_langfuse_enabled():
        banner += """
    ║   - Langfuse: Alternative LLM observability (enabled)     ║"""

    banner += """
    ╚═══════════════════════════════════════════════════════════╝
    """
    print(banner)

    # Create app
    pipeline, tru_app, session = create_app()

    # Run mode
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        run_interactive(pipeline, tru_app)
    else:
        run_demo(pipeline, tru_app)


if __name__ == "__main__":
    main()

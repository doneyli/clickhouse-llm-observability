"""
Agentic RAG Demo — CLI runner.

Usage:
    docker compose --profile demo run --rm agentic-rag python main.py
    docker compose --profile demo run --rm agentic-rag python main.py --interactive

Assumes the ClickHouse vector index is built (run ingest.py first).
"""

import sys

from graph import create_agent
import langfuse_config as lf

DEMO_QUESTIONS = [
    "What is ClickHouse and what is it used for?",          # kb, single-shot
    "How does RAG architecture reduce hallucinations?",      # kb
    "What vector databases exist and how does ClickHouse compare?",  # kb
    "Why is ClickHouse well-suited for storing observability data?",  # kb
    "Hello, what can you help me with?",                     # direct route
]


def _print_result(res: dict):
    print(f"\nRoute:    {res['route']}")
    print(f"Steps:    {' | '.join(res['steps'])}")
    print(f"Grounded: {res['grounded']}")
    print(f"\nAnswer:\n{res['answer']}\n")


def run_demo(agent):
    print("\n" + "=" * 64)
    print("Agentic RAG Demo  (LangGraph + ClickHouse-native vectors)")
    if lf.is_langfuse_enabled():
        print("+ Langfuse agentic instrumentation enabled (Agent Graph)")
    print("=" * 64)
    session = lf.new_session_id()
    for i, q in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[{i}/{len(DEMO_QUESTIONS)}] {q}")
        print("-" * 56)
        try:
            _print_result(agent.run(q, session_id=session))
        except Exception as e:
            print(f"Error: {e}")
    print("=" * 64)
    if lf.is_langfuse_enabled():
        print("View the Agent Graph: http://localhost:3001 (Langfuse → Traces)")
    print("=" * 64 + "\n")


def run_interactive(agent):
    print("\nInteractive Agentic RAG — type 'quit' to exit\n")
    session = lf.new_session_id()
    while True:
        try:
            q = input("Question: ").strip()
            if q.lower() in ("quit", "exit", "q"):
                break
            if not q:
                continue
            _print_result(agent.run(q, session_id=session))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")


def main():
    print("Initializing agent (loading embedding model + connecting to ClickHouse)...")
    agent = create_agent()
    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        run_interactive(agent)
    else:
        run_demo(agent)


if __name__ == "__main__":
    main()

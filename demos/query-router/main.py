"""
Query Router Demo — CLI runner.

Usage:
    docker compose --profile demo run --rm query-router python main.py
    docker compose --profile demo run --rm query-router python main.py --interactive

Dispatches over HTTP to the text-to-sql / vector-rag / agentic-rag services, so
those containers must be up (docker compose --profile demo up -d). If one is
down, its route degrades to the in-process fallback + a HITL escalation event.
"""

import sys

import langfuse_config as lf
from server import run

# 10 seeded questions: 2 per real route, 1 out-of-scope, 2 ambiguous, 1 drift stress.
DEMO_QUESTIONS = [
    "How many taxi rides were there in NYC in July 2015?",            # analytics_sql
    "What are the top 5 most-starred GitHub repositories this year?",  # analytics_sql
    "What is a vector index?",                                         # docs_simple
    "What does Langfuse's session view show?",                         # docs_simple
    "Compare ClickHouse-native vector search with Chroma for RAG and when each wins.",  # docs_complex
    "Walk through how CRAG self-correction reduces hallucinations, with the failure cases.",  # docs_complex
    "Write me a poem about databases.",                                # out_of_scope -> fallback
    "Is ClickHouse fast?",                                             # ambiguous (docs vs numbers)
    "Show me how RAG performs on real data.",                          # ambiguous (mixed intent)
    "Can you both chart our taxi volume AND explain what a HNSW index is?",  # registry/taxonomy stress
]


def _print_result(res: dict):
    print(f"\nRoute:       {res.get('route')}")
    print(f"Confidence:  {res.get('confidence')}")
    print(f"Handled by:  {res.get('handled_by')}")
    if res.get("escalation_reason"):
        print(f"Escalated:   reason={res['escalation_reason']}")
    answer = res.get("answer", "")
    if not isinstance(answer, str):
        answer = str(answer)
    print(f"\nAnswer:\n{answer[:600]}\n")


def run_demo():
    print("\n" + "=" * 64)
    print("Query Router Demo  (front-door classify -> dispatch over HTTP)")
    if lf.is_langfuse_enabled():
        print("+ Langfuse tracing enabled (route-query generation + nested handler subtree)")
    print("=" * 64)
    session = lf.new_session_id()
    for i, q in enumerate(DEMO_QUESTIONS, 1):
        print(f"\n[{i}/{len(DEMO_QUESTIONS)}] {q}")
        print("-" * 56)
        try:
            _print_result(run(q, session_id=session))
        except Exception as e:
            print(f"Error: {e}")
    print("=" * 64)
    if lf.is_langfuse_enabled():
        print("View traces: http://localhost:3001 (Langfuse → Traces)")
        print("  fallback/escalated routes -> trace name 'route-and-dispatch'")
        print("  dispatched routes         -> trace name '<handler>-handler' (e.g. text-to-sql-handler),")
        print("                               which holds the router spans AND the handler subtree")
    print("=" * 64 + "\n")


def run_interactive():
    print("\nInteractive Query Router — type 'quit' to exit\n")
    session = lf.new_session_id()
    while True:
        try:
            q = input("Question: ").strip()
            if q.lower() in ("quit", "exit", "q"):
                break
            if not q:
                continue
            _print_result(run(q, session_id=session))
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}\n")


def main():
    # Flush on the way out, always. This is a short-lived `docker compose run --rm`
    # process, and the SDK exports spans from a background batch processor — so
    # without this the interpreter exits while spans are still queued and traces
    # are dropped non-deterministically (observed: 3 of 10 routes landing, and
    # dispatched handler subtrees intermittently missing from the router's trace,
    # which made cross-process nesting look broken when it was not).
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
            run_interactive()
        else:
            run_demo()
    finally:
        lf.flush()


if __name__ == "__main__":
    main()

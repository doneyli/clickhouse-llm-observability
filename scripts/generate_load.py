#!/usr/bin/env python3
"""
Generate load to populate Langfuse and HyperDX dashboards.

Usage (from inside container):
    python /scripts/generate_load.py -n 10

Usage (from host):
    docker compose exec text-to-sql python /scripts/generate_load.py -n 10
"""

import sys
import os
import random
import argparse

# Add /app to path (where text-to-sql code lives in container)
sys.path.insert(0, '/app')

QUESTIONS = [
    "What are the most expensive areas for property in London?",
    "How has GitHub activity changed over the past year?",
    "What are the busiest airports based on flight data?",
    "What programming languages are most discussed on Stack Overflow?",
    "Which UK cities have the highest property price growth?",
    "What time of day are most GitHub commits made?",
    "What are the most delayed flight routes in the US?",
    "How long does it take for Stack Overflow questions to get answered?",
    "What are the average property prices by region in the UK?",
    "Which airlines have the most flights in the dataset?",
]


def main():
    parser = argparse.ArgumentParser(description="Generate queries for Langfuse/HyperDX")
    parser.add_argument("-n", "--num-queries", type=int, default=5,
                        help="Number of queries to run (default: 5)")
    parser.add_argument("--random", action="store_true",
                        help="Randomize question order")
    args = parser.parse_args()

    # Import after path setup
    from main import create_app

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║   Load Generator for Langfuse & OpenLLMetry               ║
╚═══════════════════════════════════════════════════════════╝
    """)

    pipeline, tru_app, _ = create_app()

    questions = QUESTIONS.copy()
    if args.random:
        random.shuffle(questions)

    # Cycle through questions if num_queries > len(questions)
    selected = []
    for i in range(args.num_queries):
        selected.append(questions[i % len(questions)])

    print(f"Generating {args.num_queries} queries...\n")

    success = 0
    failed = 0

    for i, question in enumerate(selected, 1):
        print(f"[{i}/{args.num_queries}] {question[:50]}...")

        with tru_app:
            try:
                pipeline.query(question)
                print("  ✓ Complete")
                success += 1
            except Exception as e:
                print(f"  ✗ Error: {e}")
                failed += 1

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║   Complete!                                               ║
║                                                           ║
║   Success: {success:3d}  |  Failed: {failed:3d}                          ║
║                                                           ║
║   View results:                                           ║
║   • Langfuse: http://localhost:3001                       ║
║   • HyperDX:  http://localhost:8080                       ║
╚═══════════════════════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    main()

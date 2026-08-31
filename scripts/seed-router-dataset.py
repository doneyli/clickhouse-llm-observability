#!/usr/bin/env python3
"""
Seed the `query-router-accuracy` dataset — a dedicated ROUTER-ONLY dataset
(distinct from any handler dataset) so the classification step regression-tests
fast and cheap, without specialist-quality noise burying router regressions.

  input           = {"question": ...}
  expected_output = {"route": "analytics_sql" | "docs_simple" | "docs_complex" | "fallback"}
  metadata        = {"category": "clear" | "ambiguous" | "out_of_scope", ...}

~30 items covering every route + ambiguous (a good router abstains -> fallback)
+ out_of_scope. Production misroutes are pinned separately, live, via
source_observation_id (see demos/query-router/scripts/score_misroute.py).

Usage:
    python scripts/seed-router-dataset.py            # create/populate
    python scripts/seed-router-dataset.py --dry-run  # preview only

Env: LANGFUSE_HOST/BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
     (defaults: http://localhost:3001, pk-lf-1234567890, sk-lf-1234567890).
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List

try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse not installed. Run: pip install 'langfuse>=3.0,<4.0'", file=sys.stderr)
    sys.exit(1)

DATASET_NAME = os.getenv("ROUTER_DATASET_NAME", "query-router-accuracy")


@dataclass
class Item:
    question: str
    route: str
    category: str
    extra: Dict[str, Any] = field(default_factory=dict)


ITEMS: List[Item] = [
    # ---- clear / analytics_sql (8) ----
    Item("How many taxi rides were there in NYC in July 2015?", "analytics_sql", "clear"),
    Item("What are the top 5 most-starred GitHub repositories this year?", "analytics_sql", "clear"),
    Item("Which programming languages have the most Stack Overflow questions?", "analytics_sql", "clear"),
    Item("What is the average taxi trip distance in New York City?", "analytics_sql", "clear"),
    Item("How many PyPI downloads did numpy get last month?", "analytics_sql", "clear"),
    Item("What are the highest-scored Hacker News stories this year?", "analytics_sql", "clear"),
    Item("Which US airlines have the highest flight-delay rate?", "analytics_sql", "clear"),
    Item("What are the most expensive areas for property in the UK?", "analytics_sql", "clear"),
    # ---- clear / docs_simple (8) ----
    Item("What is a vector index?", "docs_simple", "clear"),
    Item("What does Langfuse's session view show?", "docs_simple", "clear"),
    Item("What is ClickHouse and what is it used for?", "docs_simple", "clear"),
    Item("What is OpenTelemetry?", "docs_simple", "clear"),
    Item("What are vector embeddings?", "docs_simple", "clear"),
    Item("What is retrieval-augmented generation (RAG)?", "docs_simple", "clear"),
    Item("What is a materialized view in ClickHouse?", "docs_simple", "clear"),
    Item("What does a Langfuse score represent?", "docs_simple", "clear"),
    # ---- clear / docs_complex (6) ----
    Item("Compare ClickHouse-native vector search with Chroma for RAG and when each wins.",
         "docs_complex", "clear"),
    Item("Walk through how CRAG-style self-correction reduces hallucinations, with the failure cases.",
         "docs_complex", "clear"),
    Item("How does ClickHouse compare to a traditional row-based database for observability "
         "workloads, and where does it fall short?", "docs_complex", "clear"),
    Item("Explain the tradeoffs between LLM-as-a-judge and code evaluators, with examples of "
         "when each misleads you.", "docs_complex", "clear"),
    Item("How do managed prompts, labels, and experiments fit together in the AI engineering loop?",
         "docs_complex", "clear"),
    Item("Contrast naive RAG with agentic RAG and describe how retrieval grading and reflection "
         "change the results.", "docs_complex", "clear"),
    # ---- ambiguous (6): 3 truly-mixed -> fallback (a good router abstains) ----
    Item("Is ClickHouse fast?", "fallback", "ambiguous", {"mixed_intent": True}),
    Item("Show me how RAG performs on real data.", "fallback", "ambiguous", {"mixed_intent": True}),
    Item("Which is better, ClickHouse or Postgres?", "fallback", "ambiguous", {"mixed_intent": True}),
    # ---- ambiguous (3 with a defensible expected route) ----
    Item("How does NYC taxi ridership look over time?", "analytics_sql", "ambiguous"),
    Item("What are embeddings good for?", "docs_simple", "ambiguous"),
    Item("Give me an overview of ClickHouse vector search.", "docs_simple", "ambiguous"),
    # ---- out_of_scope -> fallback (2) ----
    Item("Write me a poem about databases.", "fallback", "out_of_scope"),
    Item("What's the weather in Amsterdam?", "fallback", "out_of_scope"),
]


def main():
    ap = argparse.ArgumentParser(description="Seed the query-router-accuracy dataset")
    ap.add_argument("--dry-run", action="store_true", help="preview items without creating")
    args = ap.parse_args()

    host = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")

    print(f"Router dataset seeder -> {host}", file=sys.stderr)
    print(f"  Dataset: {DATASET_NAME}  ({len(ITEMS)} items)", file=sys.stderr)

    if args.dry_run:
        print("  ** DRY RUN — nothing created **", file=sys.stderr)
        for it in ITEMS:
            print(f"    [{it.category:11s}] {it.route:13s} <- {it.question[:70]}", file=sys.stderr)
        return

    client = Langfuse(public_key=pk, secret_key=sk, host=host)
    try:
        client.create_dataset(
            name=DATASET_NAME,
            description="Router-only accuracy set: raw question -> expected route label. "
                        "Regression-tests ONLY the classifier, independent of specialist quality.",
            metadata={"source": "seed-router-dataset.py"},
        )
        print(f"  Created dataset: {DATASET_NAME}", file=sys.stderr)
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"  Dataset already exists: {DATASET_NAME} (adding items)", file=sys.stderr)
        else:
            print(f"  Warning creating dataset: {e}", file=sys.stderr)

    created = 0
    for it in ITEMS:
        try:
            client.create_dataset_item(
                dataset_name=DATASET_NAME,
                input={"question": it.question},
                expected_output={"route": it.route},
                metadata={"category": it.category, "route": it.route, **it.extra},
            )
            created += 1
        except Exception as e:
            print(f"    Error adding item: {e}", file=sys.stderr)

    client.flush()
    print(f"  Added {created}/{len(ITEMS)} items", file=sys.stderr)
    print(f"\nVerify in Langfuse UI: {host} -> Datasets -> {DATASET_NAME}", file=sys.stderr)


if __name__ == "__main__":
    main()

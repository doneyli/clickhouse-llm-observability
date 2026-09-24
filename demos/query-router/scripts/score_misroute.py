#!/usr/bin/env python3
"""Record a human misroute verdict as a POST-HOC SCORE.

Tags are immutable at creation — after-the-fact judgments belong on scores
(Langfuse best practice). This attaches `routing_correct` (BOOLEAN) to the
`route-query` observation, NOT the trace, so the verdict pins to the exact
router decision that was wrong.

Usage:
    python scripts/score_misroute.py <trace_id> <router_observation_id> \\
        --expected analytics_sql [--correct] [--comment "..."]

Optionally also pin the misroute into the router-accuracy dataset (the
production-misroute -> regression-test loop) with --add-to-dataset:
    python scripts/score_misroute.py <trace_id> <obs_id> --expected analytics_sql \\
        --add-to-dataset --question "How many taxi rides in July 2015?" \\
        --original-route docs_simple

Env: LANGFUSE_HOST/BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY.
"""

import argparse
import os
import sys

try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse not installed. Run: pip install 'langfuse>=3.0,<4.0'", file=sys.stderr)
    sys.exit(1)

DATASET_NAME = os.getenv("ROUTER_DATASET_NAME", "query-router-accuracy")


def main():
    ap = argparse.ArgumentParser(description="Record a post-hoc routing_correct score")
    ap.add_argument("trace_id")
    ap.add_argument("observation_id", help="the route-query generation id")
    ap.add_argument("--expected", required=True, help="the route it SHOULD have taken")
    ap.add_argument("--correct", action="store_true",
                    help="record routing_correct=1 (default is 0 = misroute)")
    ap.add_argument("--comment", default=None)
    ap.add_argument("--add-to-dataset", action="store_true",
                    help="also pin this case into the router-accuracy dataset")
    ap.add_argument("--question", default=None, help="required with --add-to-dataset")
    ap.add_argument("--original-route", default=None, help="the route actually taken (metadata)")
    args = ap.parse_args()

    host = os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "http://localhost:3001"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
    lf = Langfuse(public_key=pk, secret_key=sk, host=host)

    value = 1 if args.correct else 0
    lf.create_score(
        trace_id=args.trace_id,
        observation_id=args.observation_id,  # pin to the route-query generation, not just the trace
        name="routing_correct",
        value=value,
        data_type="BOOLEAN",
        comment=args.comment or f"should have routed to {args.expected}",
    )
    print(f"routing_correct={value} on observation {args.observation_id} (trace {args.trace_id})")

    if args.add_to_dataset:
        if not args.question:
            print("--add-to-dataset requires --question", file=sys.stderr)
            sys.exit(2)
        lf.create_dataset_item(
            dataset_name=DATASET_NAME,
            input={"question": args.question},
            expected_output={"route": args.expected},  # human-corrected
            source_trace_id=args.trace_id,
            source_observation_id=args.observation_id,  # pins the route-query generation
            metadata={"source": "production-misroute", "original_route": args.original_route},
        )
        print(f"pinned into dataset '{DATASET_NAME}' (source_observation_id={args.observation_id})")

    lf.flush()


if __name__ == "__main__":
    main()

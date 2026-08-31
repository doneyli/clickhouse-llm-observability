#!/usr/bin/env python3
"""
Seed everything the Support Triage Parallel demo needs in Langfuse in one shot:
the 7 managed prompts and the 3 datasets (2 hosted + local aggregator cases).

The independent managed judge is a separate root-level shell script
(``scripts/seed-support-triage-evaluators.sh``) because it upserts Postgres
job_configurations like the other evaluator seeds.

Usage (from repo root, after sourcing .env):
    python demos/support-triage-parallel/scripts/seed_all.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # demo root (for sql_voting etc.)
sys.path.insert(0, _HERE)                    # scripts dir (for sibling imports)

import seed_prompts   # noqa: E402
import seed_datasets  # noqa: E402


def main():
    print("== Seeding managed prompts ==")
    seed_prompts.main()
    print("\n== Seeding datasets ==")
    seed_datasets.main()
    print("\nAll done. Next: ./scripts/seed-support-triage-evaluators.sh (managed judge).")


if __name__ == "__main__":
    main()

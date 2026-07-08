#!/usr/bin/env python3
"""Register score configs in Langfuse for all PromoPlanner evaluators.

Idempotent - skips configs that already exist. Run before the first experiment.

Usage:
    uv run python scripts/setup_score_configs.py
    uv run python scripts/setup_score_configs.py --dry-run

Environment variables:
    LANGFUSE_PUBLIC_KEY  (required)
    LANGFUSE_SECRET_KEY  (required)
    LANGFUSE_HOST        (default: http://localhost:3001)
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

SCORE_CONFIGS = [
    # Deterministic item-level
    {
        "name": "intent_classification_accuracy",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Exact match between classified intent and expected intent. 1.0=correct, 0.0=wrong.",
    },
    {
        "name": "tool_call_match",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Jaccard overlap between tools_called and expected_tools. 1.0=all tools match.",
    },
    {
        "name": "compliance_status_match",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Exact match between compliance_status and expected status (APPROVED/CONDITIONAL/REJECTED/None). 1.0=correct.",
    },
    {
        "name": "brief_contains",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Fraction of expected brief_should_contain substrings present in final_brief. 1.0=all found.",
    },
    {
        "name": "sku_validity",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Fraction of SKU codes in the brief that exist in mock_sales.json. 1.0=no hallucinated SKUs.",
    },
    {
        "name": "brief_length_sanity",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "1.0 if brief is 200-5000 chars (catches empty or runaway outputs). 0.0=out of bounds.",
    },
    # LLM-as-judge item-level
    {
        "name": "tool_call_correctness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "LLM-as-judge: semantic appropriateness of tools called for the query intent. Complements deterministic Jaccard.",
    },
    {
        "name": "response_factuality",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "LLM-as-judge: brief contains only real, verifiable entities (SKUs, brands, regions). Catches hallucinations the sku_validity regex misses.",
    },
    {
        "name": "compliance_adherence",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "LLM-as-judge: brief respects compliance findings and includes required caveats.",
    },
    {
        "name": "brief_quality",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "LLM-as-judge: clarity, structure, and actionability of the final campaign brief.",
    },
    # Run-level aggregates
    {
        "name": "avg_intent_classification_accuracy",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average intent_classification_accuracy across all items in the experiment run.",
    },
    {
        "name": "avg_tool_call_match",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average tool_call_match across all items in the experiment run.",
    },
    {
        "name": "avg_compliance_status_match",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average compliance_status_match across all items in the experiment run.",
    },
    {
        "name": "avg_brief_contains",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average brief_contains fraction across all items in the experiment run.",
    },
    {
        "name": "avg_sku_validity",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average sku_validity across all items. Low value = systematic hallucination of SKU codes.",
    },
    {
        "name": "avg_tool_call_correctness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average LLM-judge tool_call_correctness score across the run.",
    },
    {
        "name": "avg_response_factuality",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average LLM-judge response_factuality score across the run.",
    },
    {
        "name": "avg_compliance_adherence",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average LLM-judge compliance_adherence score across the run.",
    },
    # Gate
    {
        "name": "certification_gate",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": (
            "Multi-dimensional PASS/FAIL gate. 1.0=PASSED all thresholds "
            "(intent>=85%, compliance>=90%, factuality>=80%). 0.0=FAILED."
        ),
    },
    # Human review (for annotation queue)
    {
        "name": "human_brief_review",
        "dataType": "CATEGORICAL",
        "categories": [
            {"label": "Approved", "value": 1},
            {"label": "Needs Revision", "value": 0.5},
            {"label": "Rejected", "value": 0},
        ],
        "description": "Human reviewer (brand manager / AI quality) assessment of the brief quality. Used to calibrate the brief_quality judge.",
    },
]


def get_auth_header(public_key: str, secret_key: str) -> str:
    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def list_existing_configs(host: str, auth: str) -> dict:
    req = urllib.request.Request(
        f"{host}/api/public/score-configs?limit=100",
        headers={"Authorization": f"Basic {auth}"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    return {cfg["name"]: cfg for cfg in data.get("data", [])}


def create_config(host: str, auth: str, config: dict) -> dict:
    body = json.dumps(config).encode()
    req = urllib.request.Request(
        f"{host}/api/public/score-configs",
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())


def parse_args():
    parser = argparse.ArgumentParser(
        description="Register PromoPlanner score configs in Langfuse"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview configs without creating them")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    from src.config import load_env
    env = load_env()

    host = env.langfuse_host or os.getenv("LANGFUSE_HOST", "http://localhost:3001")
    pk = env.langfuse_public_key or ""
    sk = env.langfuse_secret_key or ""

    if not pk or not sk:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required", file=sys.stderr)
        return 1

    auth = get_auth_header(pk, sk)

    print(f"PromoPlanner Score Config Setup", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:  {host}", file=sys.stderr)
    print(f"  Configs: {len(SCORE_CONFIGS)}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - no configs will be created **\n", file=sys.stderr)
        for cfg in SCORE_CONFIGS:
            dtype = cfg["dataType"]
            range_str = ""
            if "minValue" in cfg and "maxValue" in cfg:
                range_str = f" [{cfg['minValue']}-{cfg['maxValue']}]"
            print(f"  {cfg['name']:45s} {dtype}{range_str}", file=sys.stderr)
        return 0

    try:
        existing = list_existing_configs(host, auth)
        print(f"  Existing: {len(existing)} configs\n", file=sys.stderr)
    except Exception as e:
        print(f"  Warning: could not list existing configs: {e}", file=sys.stderr)
        existing = {}

    created = 0
    skipped = 0
    for cfg in SCORE_CONFIGS:
        if cfg["name"] in existing:
            print(f"  [skip]    {cfg['name']}", file=sys.stderr)
            skipped += 1
            continue

        try:
            result = create_config(host, auth, cfg)
            print(f"  [created] {cfg['name']} (id: {result.get('id', '?')})", file=sys.stderr)
            created += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  [error]   {cfg['name']}: {e.code} {body[:80]}", file=sys.stderr)
        except Exception as e:
            print(f"  [error]   {cfg['name']}: {e}", file=sys.stderr)

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Created: {created}", file=sys.stderr)
    print(f"  Skipped: {skipped} (already existed)", file=sys.stderr)
    print(f"\nVerify in Langfuse UI: Settings > Score Configs", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

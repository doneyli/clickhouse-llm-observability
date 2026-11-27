#!/usr/bin/env python3
"""Register score configs in Langfuse for all PromoPlanner evaluators.

Reconciles: a missing config is created, one whose range, categories or archived state
drifted is patched back, and an identical one is left alone. The data type cannot be
changed through the API, so a config with the wrong type fails and names the fix.
Exits 1 when any config could not be read, created or patched. Run before the first
experiment.

Usage:
    uv run python scripts/setup_score_configs.py
    uv run python scripts/setup_score_configs.py --dry-run

Environment variables:
    LANGFUSE_PUBLIC_KEY  (required)
    LANGFUSE_SECRET_KEY  (required)
    LANGFUSE_HOST        (default: http://localhost:3001)
"""

import argparse
import sys

from src.langfuse_rest import ApiError, Client, LangfuseEnvError, Tally

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


def _same_number(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return float(a) == float(b)


def _categories(cats) -> list[tuple[float, str]]:
    return sorted((float(c["value"]), c["label"]) for c in (cats or []))


def config_patch(existing: dict, desired: dict) -> tuple[dict, list[str]]:
    """The PATCH body that brings `existing` back to `desired`, and what drifted.

    The update API takes range, categories and isArchived only; description cannot be
    patched, so a description drift is reported but not counted as a change.
    """
    patch: dict = {}
    drift: list[str] = []
    for key in ("minValue", "maxValue"):
        if key in desired and not _same_number(existing.get(key), desired[key]):
            patch[key] = desired[key]
            drift.append(key)
    if "categories" in desired and _categories(existing.get("categories")) != _categories(desired["categories"]):
        patch["categories"] = desired["categories"]
        drift.append("categories")
    if existing.get("isArchived"):
        patch["isArchived"] = False
        drift.append("archived")
    return patch, drift


def reconcile_configs(client: Client) -> Tally:
    tally = Tally("Score configs")
    existing = {c["name"]: c for c in client.paged("/api/public/score-configs")}
    for cfg in SCORE_CONFIGS:
        name = cfg["name"]
        have = existing.get(name)
        try:
            if have is None:
                result = client.post("/api/public/score-configs", cfg) or {}
                tally.add("created")
                print(f"  [created]   {name} (id: {result.get('id', '?')})", file=sys.stderr)
                continue
            if have.get("dataType") != cfg["dataType"]:
                tally.add("failed", f"{name}: dataType {have.get('dataType')} != {cfg['dataType']}")
                print(f"  [error]     {name}: dataType is {have.get('dataType')}, want {cfg['dataType']}; "
                      "the API cannot change it, archive the config in the UI and re-run", file=sys.stderr)
                continue
            patch, drift = config_patch(have, cfg)
            if (have.get("description") or "") != (cfg.get("description") or ""):
                print(f"  [note]      {name}: description differs; the API cannot update it", file=sys.stderr)
            if not patch:
                tally.add("unchanged")
                print(f"  [unchanged] {name}", file=sys.stderr)
                continue
            client.patch(f"/api/public/score-configs/{have['id']}", patch)
            tally.add("updated")
            print(f"  [updated]   {name} ({', '.join(drift)})", file=sys.stderr)
        except ApiError as e:
            tally.add("failed", f"{name}: {e}")
            print(f"  [error]     {name}: {e}", file=sys.stderr)
    return tally


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Register PromoPlanner score configs in Langfuse"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview configs without creating them")
    return parser.parse_args(argv)


def main(argv=None, client: Client | None = None) -> int:
    args = parse_args(argv)

    if args.dry_run:
        print("\n  ** DRY RUN - no configs will be created **\n", file=sys.stderr)
        for cfg in SCORE_CONFIGS:
            dtype = cfg["dataType"]
            range_str = ""
            if "minValue" in cfg and "maxValue" in cfg:
                range_str = f" [{cfg['minValue']}-{cfg['maxValue']}]"
            print(f"  {cfg['name']:45s} {dtype}{range_str}", file=sys.stderr)
        return 0

    if client is None:
        from src.config import load_env
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    print("PromoPlanner Score Config Setup", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:  {client.base}", file=sys.stderr)
    print(f"  Configs: {len(SCORE_CONFIGS)}", file=sys.stderr)

    try:
        tally = reconcile_configs(client)
    except ApiError as e:
        print(f"  [error]     could not list existing score configs: {e}", file=sys.stderr)
        return 1

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  {tally.line()}", file=sys.stderr)
    return 1 if tally.failed else 0


if __name__ == "__main__":
    sys.exit(main())

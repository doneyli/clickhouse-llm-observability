#!/usr/bin/env python3
"""Build the golden dataset and experiment scaffold in Langfuse.

Dataset composition:
  - 25 hand-authored core items (GOLDEN_ITEMS below) covering key intents and failure modes
  - ~50 slot-generated items from src/evals/dataset_builder.py for breadth
  Total: ~75 items

Idempotency: each item gets a deterministic id derived from its input + dataset
name. Langfuse `create_dataset_item` upserts by id, so re-running the script
overwrites in place instead of appending duplicates (which previously turned
75 into 150 on a second run).
"""

import hashlib
import json
import sys

from rich.console import Console

from src.config import load_config, load_env

console = Console()

GOLDEN_ITEMS = [
    {
        "input": {"query": "Draft a Q3 promo plan for Brand A Classic in the Southeast targeting back-to-school."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "get_market_trends", "check_brand_guidelines", "check_regulatory"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand A", "Southeast", "back-to-school"],
        },
    },
    {
        "input": {"query": "Design an aggressive 50% off promo on Brand B Cola targeting families with children under 12."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["check_brand_guidelines", "check_regulatory"],
            "compliance_status": "REJECTED",
            "brief_should_contain": ["Rule 8", "children", "legal review"],
        },
    },
    {
        "input": {"query": "Compare Brand A vs Brand C performance in the Midwest over the last 6 quarters."},
        "expected_output": {
            "intent": "compare_brands",
            "expected_tools": ["query_sales"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand A", "Brand C", "Midwest"],
        },
    },
    {
        "input": {"query": "Check if this brief is compliant: 50% off sale on Brand B beverages, no end date specified."},
        "expected_output": {
            "intent": "compliance_check_only",
            "expected_tools": ["check_brand_guidelines", "check_regulatory"],
            "compliance_status": "CONDITIONAL",
            "brief_should_contain": ["end date", "Rule 7"],
        },
    },
    {
        "input": {"query": "Build a national multi-brand portfolio promo across all 5 regions."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "get_market_trends", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Southeast", "Northeast", "Midwest"],
        },
    },
    {
        "input": {"query": "What is the inventory situation for Brand A Spicy Large in the West?"},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_inventory"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["BRA-SPI-LRG", "West"],
        },
    },
    {
        "input": {"query": "Recommend a promo mechanic for Brand C Organic Medium at ClubWarehouse."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "get_market_trends", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand C", "ClubWarehouse"],
        },
    },
    {
        "input": {"query": "What's the weather like today?"},
        "expected_output": {
            "intent": "out_of_scope",
            "expected_tools": [],
            "compliance_status": None,
            "brief_should_contain": ["PromoPlanner"],
        },
    },
    {
        "input": {"query": "Plan a BOGO offer for Brand B Zero in the Northeast for 4 weeks."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand B", "Northeast", "BOGO"],
        },
    },
    {
        "input": {"query": "Which retail partner has the best sell-through for Brand A Classic?"},
        "expected_output": {
            "intent": "compare_brands",
            "expected_tools": ["query_sales"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand A", "MegaMart"],
        },
    },
    {
        "input": {"query": "Design a summer promo for Brand C Cherry Medium targeting families."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "check_brand_guidelines"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand C", "BRC-CHE-MED"],
        },
    },
    {
        "input": {"query": "Evaluate sales lift from the last 3 promo campaigns for Brand A."},
        "expected_output": {
            "intent": "compare_brands",
            "expected_tools": ["query_sales"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["lift", "Brand A"],
        },
    },
    {
        "input": {"query": "Plan a holiday promo for Brand B Diet Cola at ValueChain in the Southwest."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand B", "Southwest", "ValueChain"],
        },
    },
    {
        "input": {"query": "Is a health claim like 'Brand A fuels your active lifestyle' compliant?"},
        "expected_output": {
            "intent": "compliance_check_only",
            "expected_tools": ["check_brand_guidelines", "check_regulatory"],
            "compliance_status": "CONDITIONAL",
            "brief_should_contain": ["Rule 2", "health claim"],
        },
    },
    {
        "input": {"query": "Run a co-promotion between Brand A Classic Large and Brand C Organic Medium."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand A", "Brand C"],
        },
    },
    {
        "input": {"query": "What's the best performing SKU in the Midwest for Brand B?"},
        "expected_output": {
            "intent": "compare_brands",
            "expected_tools": ["query_sales"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand B", "Midwest"],
        },
    },
    {
        "input": {"query": "Build a back-to-school display promotion for Brand A at MegaMart."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand A", "MegaMart", "display"],
        },
    },
    {
        "input": {"query": "We want to advertise Brand B Cola to teens aged 15-18. Is this allowed?"},
        "expected_output": {
            "intent": "compliance_check_only",
            "expected_tools": ["check_brand_guidelines", "check_regulatory"],
            "compliance_status": "CONDITIONAL",
            "brief_should_contain": ["teen", "Rule 5"],
        },
    },
    {
        "input": {"query": "Recommend Q4 promo for Brand C in all 5 regions."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand C", "Q4"],
        },
    },
    {
        "input": {"query": "What's the revenue trend for BRB-ZRO-12P over the last 8 quarters?"},
        "expected_output": {
            "intent": "compare_brands",
            "expected_tools": ["query_sales"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["BRB-ZRO-12P"],
        },
    },
    {
        "input": {"query": "Plan an influencer partnership for Brand A. Do we need disclosure language?"},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["check_brand_guidelines"],
            "compliance_status": "CONDITIONAL",
            "brief_should_contain": ["Rule 4", "disclosure"],
        },
    },
    {
        "input": {"query": "Design a limited-time offer for Brand B Core 12-pack at ConvenienceCo."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "query_inventory", "check_brand_guidelines"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand B", "end date"],
        },
    },
    {
        "input": {"query": "What happened to sales in the Southeast during our last Q2 promo?"},
        "expected_output": {
            "intent": "compare_brands",
            "expected_tools": ["query_sales"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Southeast", "Q2"],
        },
    },
    {
        "input": {"query": "Create a year-end wrap-up campaign across Brand A and Brand B."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "strategy_crew"],
            "compliance_status": "APPROVED",
            "brief_should_contain": ["Brand A", "Brand B"],
        },
    },
    {
        "input": {"query": "Help me write a promo brief that mentions 3 SKUs we've never released."},
        "expected_output": {
            "intent": "plan_promo",
            "expected_tools": ["query_sales", "check_brand_guidelines"],
            "compliance_status": "APPROVED",
            "brief_should_contain": [],
            "hallucination_risk": True,
        },
    },
]


def _stable_item_id(dataset_name: str, input_payload: dict) -> str:
    """Derive a deterministic, globally-unique id from dataset + input.

    `create_dataset_item` upserts when an `id` is supplied. By hashing the
    canonical JSON of the input we get the same id on every re-seed, so the
    second run overwrites the first row instead of appending a duplicate.
    """
    payload = json.dumps(input_payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{dataset_name}::{digest}"


def seed_dataset() -> int:
    env = load_env()
    cfg = load_config()

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print(
            "[bold yellow]MANUAL: No Langfuse keys found.[/bold yellow]\n"
            "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env first."
        )
        return 0

    try:
        from langfuse import Langfuse
    except ImportError:
        console.print("[red]langfuse package not installed[/red]")
        return 1

    # Load generated items from the slot-based builder
    try:
        from src.evals.dataset_builder import build_generated_items
        generated_items = build_generated_items(seed=42)
        console.print(f"[cyan]Generated {len(generated_items)} slot-based items[/cyan]")
    except Exception as e:
        console.print(f"[yellow]dataset_builder failed, using hand-authored only: {e}[/yellow]")
        generated_items = []

    all_items = GOLDEN_ITEMS + generated_items
    total = len(all_items)

    lf = Langfuse()

    dataset_name = "promo-planner-golden-v1"
    console.print(f"[cyan]Creating dataset: {dataset_name} ({total} items)[/cyan]")

    try:
        lf.create_dataset(
            name=dataset_name,
            description=f"Golden evaluation dataset for PromoPlanner: {len(GOLDEN_ITEMS)} hand-authored + {len(generated_items)} generated = {total} total",
        )
        console.print(f"[green]Dataset created: {dataset_name}[/green]")
    except Exception as e:
        console.print(f"[yellow]Dataset may already exist: {e}[/yellow]")

    success = 0
    for i, item in enumerate(all_items):
        try:
            item_id = _stable_item_id(dataset_name, item["input"])
            lf.create_dataset_item(
                dataset_name=dataset_name,
                id=item_id,
                input=item["input"],
                expected_output=item["expected_output"],
                metadata=item.get("metadata"),
            )
            console.print(f"[green]Upserted item {i + 1}/{total}[/green]")
            success += 1
        except Exception as e:
            console.print(f"[red]Failed item {i + 1}: {e}[/red]")

    console.print(f"\n[bold]Seeded {success}/{total} dataset items[/bold]")
    console.print(
        f"[dim]  {len(GOLDEN_ITEMS)} hand-authored core items[/dim]\n"
        f"[dim]  {len(generated_items)} slot-generated items[/dim]\n"
        f"[dim]  Run experiments: uv run python scripts/run_experiment.py --sample 10[/dim]"
    )

    return 0


if __name__ == "__main__":
    sys.exit(seed_dataset())

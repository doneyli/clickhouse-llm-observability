#!/usr/bin/env python3
"""Build the golden dataset and experiment scaffold in Langfuse.

Dataset composition:
  - 25 hand-authored core items (GOLDEN_ITEMS below) covering key intents and failure modes
  - ~50 slot-generated items from src/evals/dataset_builder.py for breadth
  Total: ~75 items

Idempotency: each item gets a deterministic id derived from its input + dataset
name, and the script reads the dataset back before writing. An item is upserted
(by that id, with status ACTIVE) only when it is missing, its input, expected
output or metadata drifted, or it was archived. Items this script does not own
are left alone. An unchanged re-run writes nothing. Exits 1 on any failed read
or write, or when the item builder fails.
"""

import hashlib
import json
import sys

from rich.console import Console

from src.config import load_env
from src.langfuse_rest import ApiError, Client, LangfuseEnvError, Tally, quote

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


DATASET_NAME = "promo-planner-golden-v1"


def desired_items(generated: list[dict]) -> list[dict]:
    """The owned items as API bodies, each with its stable id and status ACTIVE."""
    out = []
    for item in GOLDEN_ITEMS + generated:
        out.append({
            "datasetName": DATASET_NAME,
            "id": _stable_item_id(DATASET_NAME, item["input"]),
            "input": item["input"],
            "expectedOutput": item["expected_output"],
            "metadata": item.get("metadata"),
            "status": "ACTIVE",
        })
    return out


def item_drift(existing: dict, desired: dict) -> list[str]:
    drift = [k for k in ("input", "expectedOutput", "metadata") if existing.get(k) != desired[k]]
    if existing.get("status") != "ACTIVE":
        drift.append(f"status {existing.get('status')}")
    return drift


def reconcile_dataset(client: Client, items: list[dict]) -> Tally:
    tally = Tally("Dataset items")
    description = (f"Golden evaluation dataset for PromoPlanner: {len(GOLDEN_ITEMS)} hand-authored + "
                   f"{len(items) - len(GOLDEN_ITEMS)} generated = {len(items)} total")
    try:
        client.get(f"/api/public/v2/datasets/{quote(DATASET_NAME)}")
        console.print(f"[dim]Dataset exists: {DATASET_NAME}[/dim]")
    except ApiError as e:
        if e.status != 404:
            raise
        client.post("/api/public/v2/datasets", {"name": DATASET_NAME, "description": description})
        console.print(f"[green]Dataset created: {DATASET_NAME}[/green]")

    existing = {it["id"]: it for it in client.paged("/api/public/dataset-items", {"datasetName": DATASET_NAME})}
    for body in items:
        have = existing.get(body["id"])
        drift = ["missing"] if have is None else item_drift(have, body)
        if not drift:
            tally.add("unchanged")
            continue
        try:
            client.post("/api/public/dataset-items", body)
            tally.add("created" if have is None else "updated")
            if have is not None:
                console.print(f"[yellow]updated {body['id']} ({', '.join(drift)})[/yellow]")
        except ApiError as e:
            tally.add("failed", f"{body['id']}: {e}")
            console.print(f"[red]failed {body['id']}: {e}[/red]")
    return tally


def seed_dataset(client: Client | None = None) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1

    try:
        from src.evals.dataset_builder import build_generated_items
        generated_items = build_generated_items(seed=42)
        console.print(f"[cyan]Generated {len(generated_items)} slot-based items[/cyan]")
    except Exception as e:
        console.print(f"[red]dataset_builder failed: {e}[/red]")
        return 1

    items = desired_items(generated_items)
    console.print(f"[cyan]Reconciling dataset: {DATASET_NAME} ({len(items)} items)[/cyan]")
    try:
        tally = reconcile_dataset(client, items)
    except ApiError as e:
        console.print(f"[red]Dataset read failed: {e}[/red]")
        return 1

    console.print(f"\n[bold]{tally.line()}[/bold]")
    console.print(f"[dim]  {len(GOLDEN_ITEMS)} hand-authored core items, "
                  f"{len(generated_items)} slot-generated items[/dim]")
    return 1 if tally.failed else 0


if __name__ == "__main__":
    sys.exit(seed_dataset())

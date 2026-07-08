"""Templated input/output strings for synthetic trace generation."""

from __future__ import annotations

import random

from src.config import load_config

INTENTS = ["plan_promo", "compare_brands", "compliance_check_only", "out_of_scope"]

QUERY_TEMPLATES = [
    "Draft a Q{q} promo plan for {brand} in the {region} targeting {occasion}.",
    "Compare {brand1} vs {brand2} performance in the {region} over the last 6 quarters.",
    "What is the best promotional mechanic for {brand} at {retailer}?",
    "Design a {depth}% off promotion for {brand} for {duration} weeks.",
    "Check compliance for a {depth}% off sale on {brand} with no end date.",
    "Build a national promo for {brand} across all regions with {mechanic} mechanic.",
    "What's the inventory situation for {sku} in the {region}?",
    "Evaluate sales lift for {brand} from last quarter's promo.",
    "Plan a co-promo between {brand1} and {brand2} for {retailer}.",
    "Is marketing {brand} to teenagers compliant?",
]

OCCASIONS = ["back-to-school", "summer", "holiday", "spring refresh", "game day", "tailgating"]
MECHANICS = ["price reduction", "BOGO", "bundle offer", "display feature", "TPR", "loyalty points"]

BRIEF_SNIPPETS = [
    "Recommended mechanic: {depth}% price reduction for {duration} weeks at {retailer}.",
    "Strategy: BOGO on {sku} in {region}. Expected lift: {lift}%.",
    "Co-promotion: {brand1} + {brand2} bundle offer at {retailer} for {duration} weeks.",
    "Campaign approved. Launch date: start of Q{q}.",
    "REJECTED: Campaign blocked pending legal review of marketing-to-minors rule.",
    "CONDITIONAL: Revise pricing claim; requires end date on limited-time offer.",
]

HALLUCINATED_SKUS = ["BRA-XX9-FAKE", "BRB-NEW-999", "BRC-UNK-LRG", "FKE-SKU-001", "XYZ-000-TMP"]

RESEARCH_OUTPUTS = [
    "Sales analysis complete. {brand} in {region}: {units}k units over 8 quarters. Promo quarters averaged {lift}% lift.",
    "Market trends: Category growth slowing; value formats gaining share. Digital promotions up 18% YoY.",
    "Historical promos: Best mechanic for {brand} in {region} was {mechanic} at {depth}% depth ({lift}% observed lift).",
    "Inventory adequate: {sku} has {days} days of supply at {dc} DC.",
]

STRATEGY_OUTPUTS = [
    "Option 1: {depth}% price reduction for {duration} weeks. Estimated lift: {lift}%. Confidence: HIGH.",
    "Option 2: BOGO on {sku}. Estimated lift: {lift2}%. Confidence: MEDIUM.",
    "Option 3: Bundle {sku} with partner SKU. Estimated lift: {lift3}%. Confidence: MEDIUM.",
    "Recommended: Option 1 based on historical performance and inventory headroom.",
]


def build_promo_planner_input(rng: random.Random) -> str:
    cfg = load_config()
    brand = rng.choice(cfg.all_brand_names())
    region = rng.choice(cfg.regions)
    retailer = rng.choice(cfg.retail_partners)
    occasion = rng.choice(OCCASIONS)
    template = rng.choice(QUERY_TEMPLATES[:5])
    return (
        template
        .replace("{brand}", brand)
        .replace("{brand1}", brand)
        .replace("{brand2}", rng.choice(cfg.all_brand_names()))
        .replace("{region}", region)
        .replace("{retailer}", retailer)
        .replace("{occasion}", occasion)
        .replace("{depth}", str(rng.randint(15, 50)))
        .replace("{duration}", str(rng.randint(2, 8)))
        .replace("{q}", str(rng.randint(1, 4)))
        .replace("{mechanic}", rng.choice(MECHANICS))
        .replace("{sku}", rng.choice(cfg.all_skus()))
    )


def build_promo_planner_output(rng: random.Random, failure_mode: str | None = None) -> str:
    cfg = load_config()
    brand = rng.choice(cfg.all_brand_names())
    region = rng.choice(cfg.regions)
    retailer = rng.choice(cfg.retail_partners)
    rng.choice(MECHANICS)
    depth = rng.randint(15, 50)
    duration = rng.randint(2, 8)
    lift = rng.randint(20, 60)
    sku = rng.choice(cfg.all_skus())

    if failure_mode == "hallucinated_sku":
        sku = rng.choice(HALLUCINATED_SKUS)
        return f"Campaign brief includes SKU {sku}. Recommend {depth}% off at {retailer} for {duration} weeks."
    if failure_mode == "compliance_rejection":
        return "REJECTED: Campaign blocked. Rule 8 violation: marketing-to-minors requires legal review before launch."

    template = rng.choice(BRIEF_SNIPPETS[:4])
    return (
        template
        .replace("{brand}", brand)
        .replace("{brand1}", brand)
        .replace("{brand2}", rng.choice(cfg.all_brand_names()))
        .replace("{region}", region)
        .replace("{retailer}", retailer)
        .replace("{depth}", str(depth))
        .replace("{duration}", str(duration))
        .replace("{lift}", str(lift))
        .replace("{q}", str(rng.randint(1, 4)))
        .replace("{sku}", sku)
    )


def build_simple_agent_input(agent_name: str, rng: random.Random) -> str:
    cfg = load_config()
    brand = rng.choice(cfg.all_brand_names())
    region = rng.choice(cfg.regions)
    templates = {
        "CustomerCareBot": f"Customer inquiry about {brand} product availability in {region}.",
        "SupplyChainPlanner": f"Optimize inventory reorder for {rng.choice(cfg.all_skus())} in {region}.",
        "ShelfImageAnalyzer": f"Analyze shelf compliance photo for {brand} at {rng.choice(cfg.retail_partners)}.",
        "InternalKBSearch": f"Find promo policy for {brand} national campaigns.",
        "PepGPT": f"What is the approval process for a {rng.randint(20, 50)}% off promo?",
        "FinanceCloseBot": f"Reconcile trade spend for {brand} in Q{rng.randint(1,4)}.",
    }
    return templates.get(agent_name, f"Task for {agent_name} regarding {brand}.")


def build_simple_agent_output(agent_name: str, rng: random.Random) -> str:
    templates = {
        "CustomerCareBot": "Product is available at your nearest retailer. Expected restock in 3 days.",
        "SupplyChainPlanner": "Reorder recommended: 5,000 units. Lead time: 12 days.",
        "ShelfImageAnalyzer": "Shelf compliance: 87%. 2 planogram violations detected in aisle 4.",
        "InternalKBSearch": "Policy found: National campaigns require VP approval and 30-day lead time.",
        "PepGPT": "Approvals required: Brand Manager sign-off, Legal review for >30% depth, VP for national.",
        "FinanceCloseBot": "Trade spend reconciled. Variance: $12,450. Adjustment posted to GL.",
    }
    return templates.get(agent_name, "Task completed successfully.")

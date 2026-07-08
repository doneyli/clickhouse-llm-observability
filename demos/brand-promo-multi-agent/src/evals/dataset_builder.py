"""Slot-based synthetic dataset item generator for the PromoPlanner golden eval set.

Generates 50 items from demo.config.yaml slots (brands, SKUs, regions, retail
partners, quarters) to supplement the 25 hand-authored core items in seed_dataset.py.

The output dataset re-themes automatically when demo.config.yaml is swapped for a
customer overlay - same generic-by-default principle as the rest of the demo.
"""

from __future__ import annotations

import random
from typing import Any

from src.config import load_config

QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
SEASONS = ["summer", "back-to-school", "holiday", "spring"]
MECHANICS = [
    "15% off",
    "BOGO",
    "end-cap display",
    "buy-2-get-1",
    "price rollback",
    "seasonal bundle",
    "loyalty point multiplier",
    "temporary price reduction",
]
METRICS = ["revenue", "units sold", "sell-through rate", "promo lift", "basket size"]
OOS_QUERIES = [
    "What's the weather like in Miami this weekend?",
    "Can you draft a personal performance review for my team member?",
    "What are the latest stock prices for our parent company?",
]


def _plan_promo_items(cfg, rng: random.Random) -> list[dict[str, Any]]:
    items = []
    brand_names = cfg.all_brand_names()
    for brand in brand_names:
        for region in rng.sample(cfg.regions, k=3):
            partner = rng.choice(cfg.retail_partners)
            mechanic = rng.choice(MECHANICS)
            quarter = rng.choice(QUARTERS)
            items.append({
                "input": {
                    "query": f"Plan a {mechanic} campaign for {brand} at {partner} in the {region} for {quarter}."
                },
                "expected_output": {
                    "intent": "plan_promo",
                    "expected_tools": [
                        "query_sales", "query_inventory",
                        "get_market_trends", "check_brand_guidelines", "check_regulatory",
                    ],
                    "compliance_status": "APPROVED",
                    "brief_should_contain": [brand, region, partner],
                },
                "metadata": {"intent_bucket": "plan_promo", "judge_focus": "tool_use"},
            })

    # Add SKU-specific plan_promo items (hallucination risk surface)
    for bf in cfg.catalog.brand_families:
        sku = rng.choice(bf.hero_skus)
        region = rng.choice(cfg.regions)
        season = rng.choice(SEASONS)
        items.append({
            "input": {
                "query": f"Build a {season} promo for {bf.name} featuring {sku} in the {region}."
            },
            "expected_output": {
                "intent": "plan_promo",
                "expected_tools": [
                    "query_sales", "query_inventory",
                    "get_market_trends", "check_brand_guidelines",
                ],
                "compliance_status": "APPROVED",
                "brief_should_contain": [bf.name, sku, region],
            },
            "metadata": {"intent_bucket": "plan_promo", "judge_focus": "factuality"},
        })

    # Add partner-specific plan_promo items (brand × all retail partners)
    for brand in brand_names:
        for partner in cfg.retail_partners:
            region = rng.choice(cfg.regions)
            mechanic = rng.choice(MECHANICS)
            quarter = rng.choice(QUARTERS)
            items.append({
                "input": {
                    "query": f"Build a {quarter} {mechanic} campaign for {brand} exclusively at {partner} in the {region}."
                },
                "expected_output": {
                    "intent": "plan_promo",
                    "expected_tools": [
                        "query_sales", "query_inventory",
                        "get_market_trends", "check_brand_guidelines",
                    ],
                    "compliance_status": "APPROVED",
                    "brief_should_contain": [brand, partner, region],
                },
                "metadata": {"intent_bucket": "plan_promo", "judge_focus": "tool_use"},
            })

    # Add national / multi-region plan_promo items
    for i in range(4):
        brand = rng.choice(brand_names)
        quarter = rng.choice(QUARTERS)
        items.append({
            "input": {
                "query": f"Design a national {quarter} portfolio campaign for {brand} across all 5 regions."
            },
            "expected_output": {
                "intent": "plan_promo",
                "expected_tools": [
                    "query_sales", "query_inventory", "get_market_trends", "strategy_crew",
                ],
                "compliance_status": "APPROVED",
                "brief_should_contain": [brand, quarter],
            },
            "metadata": {"intent_bucket": "plan_promo", "judge_focus": "tool_use"},
        })

    return items


def _compare_brands_items(cfg, rng: random.Random) -> list[dict[str, Any]]:
    items = []
    brand_names = cfg.all_brand_names()
    pairs = [(brand_names[i], brand_names[j]) for i in range(len(brand_names)) for j in range(i + 1, len(brand_names))]

    for brand_a, brand_b in pairs:
        region = rng.choice(cfg.regions)
        quarter = rng.choice(QUARTERS)
        metric = rng.choice(METRICS)
        items.append({
            "input": {
                "query": f"Compare {brand_a} vs {brand_b} {metric} in the {region} for {quarter}."
            },
            "expected_output": {
                "intent": "compare_brands",
                "expected_tools": ["query_sales"],
                "compliance_status": "APPROVED",
                "brief_should_contain": [brand_a, brand_b, region],
            },
            "metadata": {"intent_bucket": "compare_brands", "judge_focus": "factuality"},
        })

    # Retail partner best-performer queries
    for partner in rng.sample(cfg.retail_partners, k=2):
        brand = rng.choice(brand_names)
        items.append({
            "input": {
                "query": f"Which region drives the best {brand} sell-through at {partner}?"
            },
            "expected_output": {
                "intent": "compare_brands",
                "expected_tools": ["query_sales"],
                "compliance_status": "APPROVED",
                "brief_should_contain": [brand, partner],
            },
            "metadata": {"intent_bucket": "compare_brands", "judge_focus": "factuality"},
        })

    # SKU-level revenue trend queries
    for bf in cfg.catalog.brand_families:
        sku = bf.hero_skus[0]
        items.append({
            "input": {"query": f"What's the revenue trend for {sku} over the last 6 quarters?"},
            "expected_output": {
                "intent": "compare_brands",
                "expected_tools": ["query_sales"],
                "compliance_status": "APPROVED",
                "brief_should_contain": [sku],
            },
            "metadata": {"intent_bucket": "compare_brands", "judge_focus": "factuality"},
        })

    return items


def _compliance_check_items(cfg, rng: random.Random) -> list[dict[str, Any]]:
    items = []
    brand_names = cfg.all_brand_names()

    # No end date violations
    for _ in range(3):
        brand = rng.choice(brand_names)
        region = rng.choice(cfg.regions)
        mechanic = rng.choice(MECHANICS)
        items.append({
            "input": {
                "query": f"Is this compliant? {mechanic} offer on {brand} in {region} - no end date specified."
            },
            "expected_output": {
                "intent": "compliance_check_only",
                "expected_tools": ["check_brand_guidelines", "check_regulatory"],
                "compliance_status": "CONDITIONAL",
                "brief_should_contain": ["end date", "Rule 7"],
            },
            "metadata": {"intent_bucket": "compliance_check_only", "judge_focus": "compliance"},
        })

    # Health claim compliance checks
    for _ in range(2):
        brand = rng.choice(brand_names)
        items.append({
            "input": {
                "query": f"Is the tagline '{brand} - fuel your active lifestyle' compliant for retail signage?"
            },
            "expected_output": {
                "intent": "compliance_check_only",
                "expected_tools": ["check_brand_guidelines", "check_regulatory"],
                "compliance_status": "CONDITIONAL",
                "brief_should_contain": ["Rule 2", "health claim"],
            },
            "metadata": {"intent_bucket": "compliance_check_only", "judge_focus": "compliance"},
        })

    # Teen-adjacent targeting
    items.append({
        "input": {
            "query": "Can we run a social media campaign targeting 16-to-21-year-olds for Brand B?"
        },
        "expected_output": {
            "intent": "compliance_check_only",
            "expected_tools": ["check_brand_guidelines", "check_regulatory"],
            "compliance_status": "CONDITIONAL",
            "brief_should_contain": ["Rule 5", "teen"],
        },
        "metadata": {"intent_bucket": "compliance_check_only", "judge_focus": "compliance"},
    })

    return items


def _compliance_edge_case_items(cfg, rng: random.Random) -> list[dict[str, Any]]:
    """Edge cases that require judgment between APPROVED and CONDITIONAL."""
    items = []
    brand_names = cfg.all_brand_names()

    templates = [
        ("Plan an influencer partnership for {brand} targeting wellness-focused millennials in {region}.",
         ["Rule 4", "disclosure"]),
        ("Design a lifestyle imagery campaign for {brand} at {partner} - no health claims, just aspirational.",
         ["brand guidelines", "Rule 3"]),
        ("Build a sampling event for {brand} at {partner} - free samples at store entry.",
         ["Rule 6", "sampling"]),
        ("Create a social proof campaign for {brand} using customer testimonials in {region}.",
         ["Rule 4", "testimonial"]),
        ("Plan a sports sponsorship activation for {brand} at regional events in {region}.",
         ["Rule 1", "sponsorship"]),
    ]

    for template, should_contain in templates:
        brand = rng.choice(brand_names)
        region = rng.choice(cfg.regions)
        partner = rng.choice(cfg.retail_partners)
        query = template.format(brand=brand, region=region, partner=partner)
        items.append({
            "input": {"query": query},
            "expected_output": {
                "intent": "plan_promo",
                "expected_tools": ["check_brand_guidelines", "get_market_trends"],
                "compliance_status": "CONDITIONAL",
                "brief_should_contain": [brand] + should_contain,
            },
            "metadata": {
                "intent_bucket": "compliance_edge_case",
                "judge_focus": "compliance",
            },
        })

    return items


def _out_of_scope_items() -> list[dict[str, Any]]:
    return [
        {
            "input": {"query": q},
            "expected_output": {
                "intent": "out_of_scope",
                "expected_tools": [],
                "compliance_status": None,
                "brief_should_contain": ["PromoPlanner"],
            },
            "metadata": {"intent_bucket": "out_of_scope", "judge_focus": "factuality"},
        }
        for q in OOS_QUERIES
    ]


def build_generated_items(seed: int = 42) -> list[dict[str, Any]]:
    """Generate ~50 slot-based dataset items from demo.config.yaml.

    Items are deterministic (same seed = same items) and re-theme automatically
    when demo.config.yaml is swapped for a customer overlay.
    """
    cfg = load_config()
    rng = random.Random(seed)

    plan_items = _plan_promo_items(cfg, rng)
    compare_items = _compare_brands_items(cfg, rng)
    compliance_items = _compliance_check_items(cfg, rng)
    edge_items = _compliance_edge_case_items(cfg, rng)
    oos_items = _out_of_scope_items()

    all_items = plan_items + compare_items + compliance_items + edge_items + oos_items

    # Shuffle for realistic ordering in the dataset UI, but keep seed for reproducibility
    rng.shuffle(all_items)

    # Cap at 50 to keep total dataset at 75 (25 hand-authored + 50 generated)
    return all_items[:50]

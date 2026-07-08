#!/usr/bin/env python3
"""Seed all prompts from src/prompts/* into Langfuse Prompt Management."""

import sys

from rich.console import Console

from src.config import load_config, load_env

console = Console()

# Mapping: (module_path, prompt_key) -> langfuse_prompt_name
PROMPT_REGISTRY = [
    ("src.prompts.orchestrator", "system", "promo-planner/orchestrator/system"),
    ("src.prompts.orchestrator", "classify_intent", "promo-planner/orchestrator/classify-intent"),
    ("src.prompts.orchestrator", "out_of_scope_response", "promo-planner/orchestrator/out-of-scope"),
    ("src.prompts.orchestrator", "compose_brief", "promo-planner/orchestrator/compose-brief"),
    ("src.prompts.research", "research_task", "promo-planner/research/research-task"),
    ("src.prompts.strategy", "strategy_task", "promo-planner/strategy/strategy-task"),
    ("src.prompts.compliance", "brand_guidelines_check", "promo-planner/compliance/brand-guidelines"),
    ("src.prompts.compliance", "regulatory_check", "promo-planner/compliance/regulatory-check"),
    ("src.prompts.compliance", "aggregate_compliance", "promo-planner/compliance/aggregate"),
    ("src.prompts.judge", "tool_call_correctness", "promo-planner/judge/tool-call-correctness"),
    ("src.prompts.judge", "response_factuality", "promo-planner/judge/response-factuality"),
    ("src.prompts.judge", "compliance_adherence", "promo-planner/judge/compliance-adherence"),
]


def seed_prompts() -> int:
    env = load_env()
    cfg = load_config()

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print(
            "[bold yellow]MANUAL: No Langfuse keys found.[/bold yellow]\n"
            "Add LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to .env first.\n"
            "Then re-run this script to push prompts."
        )
        return 0

    try:
        from langfuse import Langfuse
    except ImportError:
        console.print("[red]langfuse package not installed[/red]")
        return 1

    lf = Langfuse()

    success_count = 0
    for module_path, prompt_key, langfuse_name in PROMPT_REGISTRY:
        try:
            import importlib
            module = importlib.import_module(module_path)
            prompts_dict = getattr(module, "PROMPTS", {})
            prompt_text = prompts_dict.get(prompt_key)
            if not prompt_text:
                console.print(f"[yellow]Skipping {langfuse_name}: key '{prompt_key}' not found[/yellow]")
                continue

            lf.create_prompt(
                name=langfuse_name,
                prompt=prompt_text,
                labels=["production"],
                type="text",
            )
            console.print(f"[green]Pushed: {langfuse_name}[/green]")
            success_count += 1
        except Exception as e:
            console.print(f"[red]Failed to push {langfuse_name}: {e}[/red]")

    console.print(f"\n[bold]Pushed {success_count}/{len(PROMPT_REGISTRY)} prompts[/bold]")
    return 0


if __name__ == "__main__":
    sys.exit(seed_prompts())

#!/usr/bin/env python3
"""Seed all prompts from src/prompts/* into Langfuse Prompt Management.

Reconciles instead of appending: each prompt's `production` version is read back and a new
version is written only when its text, type, config or label differ from the source here.
An unchanged re-run writes nothing. Exits 1 when any prompt could not be read or written.
"""

import importlib
import sys

from rich.console import Console

from src.config import load_env
from src.langfuse_rest import ApiError, Client, LangfuseEnvError, Tally, quote

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


LABEL = "production"


def desired_prompts() -> list[tuple[str, str | None]]:
    """(langfuse_name, prompt text or None when the source key is missing)."""
    out = []
    for module_path, prompt_key, langfuse_name in PROMPT_REGISTRY:
        prompts_dict = getattr(importlib.import_module(module_path), "PROMPTS", {})
        out.append((langfuse_name, prompts_dict.get(prompt_key)))
    return out


def prompt_drift(existing: dict, text: str) -> list[str]:
    """What differs between the stored production version and the desired definition."""
    drift = []
    if existing.get("type") != "text":
        drift.append(f"type {existing.get('type')!r}")
    if existing.get("prompt") != text:
        drift.append("text")
    if (existing.get("config") or {}) != {}:
        drift.append("config")
    if LABEL not in (existing.get("labels") or []):
        drift.append(f"label {LABEL}")
    return drift


def reconcile_prompt(client: Client, name: str, text: str, tally: Tally) -> None:
    body = {"name": name, "prompt": text, "type": "text", "labels": [LABEL], "config": {}}
    try:
        try:
            existing = client.get(f"/api/public/v2/prompts/{quote(name)}", label=LABEL) or {}
        except ApiError as e:
            if e.status != 404:
                raise
            created = client.post("/api/public/v2/prompts", body) or {}
            tally.add("created")
            console.print(f"[green]created   {name} v{created.get('version')}[/green]")
            return
        drift = prompt_drift(existing, text)
        if not drift:
            tally.add("unchanged")
            console.print(f"[dim]unchanged {name} v{existing.get('version')}[/dim]")
            return
        updated = client.post("/api/public/v2/prompts", body) or {}
        tally.add("updated")
        console.print(f"[yellow]updated   {name} v{existing.get('version')} -> "
                      f"v{updated.get('version')} ({', '.join(drift)})[/yellow]")
    except ApiError as e:
        tally.add("failed", f"{name}: {e}")
        console.print(f"[red]failed    {name}: {e}[/red]")


def seed_prompts(client: Client | None = None) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1

    tally = Tally("Prompts")
    for name, text in desired_prompts():
        if not text:
            tally.add("failed", f"{name}: no source text in src/prompts")
            console.print(f"[red]failed    {name}: no source text in src/prompts[/red]")
            continue
        reconcile_prompt(client, name, text, tally)

    console.print(f"\n[bold]{tally.line()}[/bold]")
    return 1 if tally.failed else 0


if __name__ == "__main__":
    sys.exit(seed_prompts())

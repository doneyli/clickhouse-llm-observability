#!/usr/bin/env python3
"""Configure LLM-as-judge evaluators in Langfuse (online, 10% sampling).

These are the LIVE evaluators that run on incoming production traces.
For OFFLINE experiment evaluation (golden dataset runs), use:
    src/evals/evaluators.py  - same judge logic as Python functions
    scripts/run_experiment.py - experiment runner CLI

Keep the judge prompts here and in src/evals/evaluators.py in sync when updating."""

import sys

from rich.console import Console
from rich.panel import Panel

from src.config import load_config, load_env
from src.prompts.judge import PROMPTS

console = Console()

EVALUATORS = [
    {
        "name": "tool-call-correctness",
        "description": "Judges whether the agent called the correct tools for the query intent",
        "prompt_key": "tool_call_correctness",
        "target_type": "trace",
        "score_name": "tool-call-correctness",
        "score_range": {"min": 0.0, "max": 1.0},
    },
    {
        "name": "response-factuality",
        "description": "Judges whether the brief contains only real, verifiable SKUs and entities",
        "prompt_key": "response_factuality",
        "target_type": "trace",
        "score_name": "response-factuality",
        "score_range": {"min": 0.0, "max": 1.0},
    },
    {
        "name": "compliance-adherence",
        "description": "Judges whether the brief respects compliance findings",
        "prompt_key": "compliance_adherence",
        "target_type": "trace",
        "score_name": "compliance-adherence",
        "score_range": {"min": 0.0, "max": 1.0},
    },
]


def _emit_manual_checklist(cfg: object) -> None:
    console.print(
        Panel(
            "[bold yellow]MANUAL: Create Evaluators in Langfuse UI[/bold yellow]\n\n"
            "The Langfuse v3 evaluator API may not expose programmatic creation.\n"
            "Create the following evaluators manually:\n\n"
            "Navigate to: Settings -> Evaluators -> New Evaluator\n\n"
            "Evaluator 1: tool-call-correctness\n"
            "  - Name: tool-call-correctness\n"
            "  - Type: LLM-as-Judge\n"
            "  - Model: claude-opus-4-7\n"
            "  - Target: Trace\n"
            "  - Sampling Rate: 10%\n"
            "  - Prompt: (copy from promo-planner/judge/tool-call-correctness in Prompt Management)\n"
            "  - Score Name: tool-call-correctness\n"
            "  - Score Range: 0.0 to 1.0\n\n"
            "Evaluator 2: response-factuality\n"
            "  - Name: response-factuality\n"
            "  - Type: LLM-as-Judge\n"
            "  - Model: claude-opus-4-7\n"
            "  - Target: Trace\n"
            "  - Sampling Rate: 10%\n"
            "  - Prompt: (copy from promo-planner/judge/response-factuality)\n"
            "  - Score Name: response-factuality\n"
            "  - Score Range: 0.0 to 1.0\n\n"
            "Evaluator 3: compliance-adherence\n"
            "  - Name: compliance-adherence\n"
            "  - Type: LLM-as-Judge\n"
            "  - Model: claude-opus-4-7\n"
            "  - Target: Trace\n"
            "  - Sampling Rate: 10%\n"
            "  - Prompt: (copy from promo-planner/judge/compliance-adherence)\n"
            "  - Score Name: compliance-adherence\n"
            "  - Score Range: 0.0 to 1.0",
            title="Manual Configuration Required",
        )
    )


def seed_evaluators() -> int:
    env = load_env()
    cfg = load_config()

    if not env.langfuse_public_key or not env.langfuse_secret_key:
        console.print("[yellow]No Langfuse keys - emitting manual checklist[/yellow]")
        _emit_manual_checklist(cfg)
        return 0

    try:
        import httpx
    except ImportError:
        console.print("[red]httpx not installed[/red]")
        return 1

    host = env.langfuse_host or cfg.langfuse.host
    judge_model = cfg.llm.models.judge

    # Langfuse v3 evaluator API - attempt programmatic creation
    success_count = 0
    for ev in EVALUATORS:
        prompt_text = PROMPTS.get(ev["prompt_key"], "")
        payload = {
            "name": ev["name"],
            "type": "llm",
            "enabled": True,
            "samplingRate": 0.10,
            "targetType": ev["target_type"],
            "scoreName": ev["score_name"],
            "model": judge_model,
            "prompt": prompt_text,
            "scoreRange": ev["score_range"],
        }
        try:
            resp = httpx.post(
                f"{host}/api/public/score-configs",
                auth=(env.langfuse_public_key, env.langfuse_secret_key),
                json=payload,
                timeout=10,
            )
            if resp.status_code in (200, 201):
                console.print(f"[green]Created evaluator: {ev['name']}[/green]")
                success_count += 1
            elif resp.status_code == 409:
                console.print(f"[yellow]Evaluator already exists: {ev['name']}[/yellow]")
                success_count += 1
            else:
                console.print(f"[yellow]API returned {resp.status_code} for {ev['name']} - may need manual setup[/yellow]")
        except Exception as e:
            console.print(f"[yellow]API call failed for {ev['name']}: {e}[/yellow]")

    if success_count < len(EVALUATORS):
        console.print("\n[yellow]Some evaluators need manual setup:[/yellow]")
        _emit_manual_checklist(cfg)

    console.print(f"\n[bold]Evaluator setup: {success_count}/{len(EVALUATORS)} via API[/bold]")
    return 0


if __name__ == "__main__":
    sys.exit(seed_evaluators())

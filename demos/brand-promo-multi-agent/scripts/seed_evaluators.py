#!/usr/bin/env python3
"""Configure LLM-as-judge evaluators in Langfuse (online, 10% sampling).

These are the LIVE evaluators that run on incoming production traces.
For OFFLINE experiment evaluation (golden dataset runs), use:
    src/evals/evaluators.py  - same judge logic as Python functions
    scripts/run_experiment.py - experiment runner CLI

Keep the judge prompts here and in src/evals/evaluators.py in sync when updating.

The live judges are created by hand in the UI from the checklist below. This script reads
the evaluation rules back (v2 evaluation-rules API) and reports which are missing or off.

Exit codes: 0 all three rules exist and are enabled; 2 one or more still need the manual
step (or the server has no rules API to read them back); 1 the read failed or no keys.
"""

import sys

from rich.console import Console
from rich.panel import Panel

from src.config import load_env
from src.langfuse_rest import ApiError, Client, LangfuseEnvError

MANUAL = 2

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


def seed_evaluators(client: Client | None = None) -> int:
    if client is None:
        load_env()
        try:
            client = Client.from_env()
        except LangfuseEnvError as e:
            console.print(f"[red]{e}[/red]")
            return 1

    try:
        rules = client.cursor("/api/public/v2/evaluation-rules", {"limit": 50})
    except ApiError as e:
        if e.status == 404:
            console.print("[yellow]This server has no evaluation-rules API; check the judges in the UI.[/yellow]")
            _emit_manual_checklist(None)
            return MANUAL
        console.print(f"[red]Evaluation-rule read failed: {e}[/red]")
        return 1

    enabled = {r.get("name") for r in rules if r.get("enabled")}
    missing = [ev["name"] for ev in EVALUATORS if ev["name"] not in enabled]
    for ev in EVALUATORS:
        console.print(f"  {ev['name']}: {'missing or disabled (manual)' if ev['name'] in missing else 'enabled'}")
    console.print(f"\n[bold]Online judges: {len(EVALUATORS) - len(missing)}/{len(EVALUATORS)} enabled[/bold]")
    if missing:
        console.print("[dim]The offline judges in src/evals/evaluators.py run via scripts/run_experiment.py "
                      "either way. Add an Anthropic LLM connection in the project before creating these.[/dim]")
        _emit_manual_checklist(None)
        return MANUAL
    return 0


if __name__ == "__main__":
    sys.exit(seed_evaluators())

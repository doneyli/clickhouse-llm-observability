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

    # This Langfuse version has no stable PUBLIC API for creating LLM-as-judge
    # evaluators. A previous version of this script POSTed a judge-shaped payload
    # (model/prompt/samplingRate/targetType) to /api/public/score-configs — the
    # WRONG endpoint: it defines score *configs* (name/dataType/range), not
    # judges. That silently created orphan score-configs (or 4xx'd) while printing
    # "Created evaluator: ...", i.e. false success with no judge actually wired.
    #
    # Until a stable evaluators API exists, create these live judges once in the
    # UI (checklist below). For a self-hosted stack you can instead seed
    # job_configurations directly in Postgres — see scripts/seed-llm-judge-evaluators.sh
    # and scripts/seed-agentic-rag-evaluators.sh for that pattern.
    console.print(
        "[yellow]Live LLM-as-judge evaluators aren't creatable via a stable public "
        "API on this Langfuse version — configure them once in the UI:[/yellow]"
    )
    _emit_manual_checklist(cfg)
    console.print(
        "\n[dim]Self-hosted alternative: seed job_configurations in Postgres, per "
        "scripts/seed-llm-judge-evaluators.sh. The offline experiment judges in "
        "src/evals/evaluators.py already run via scripts/run_experiment.py.[/dim]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(seed_evaluators())

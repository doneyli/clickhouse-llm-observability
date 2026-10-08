"""Run an experiment on a dataset (EXP-02 prompt A/B, EXP-03 model/config comparison).

  --prompt-label staging        A/B a prompt version (served by label)
  --model gpt-4.1               compare models on the same prompt
  --dataset northwind-redteam-v1
  --run-name <name>             default: "<label> · <model>"

Compare runs in Langfuse: Datasets → <dataset> → select runs → Compare.
Exit code 0 always; the gate (scripts/prompt_gate.py) is what fails builds.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import agent, config, evals  # noqa: E402

GOLDEN = "northwind-golden-qa-v1"


def run(dataset: str, prompt_label: str, model: str, run_name: str | None = None, max_concurrency: int = 6):
    lf = config.get_langfuse()
    # Guard: the app falls back to a hard-coded prompt when a label does not
    # resolve. An experiment (or a CI gate) on an unknown label would silently
    # grade the FALLBACK and report results for a version it never ran.
    from northwind import prompts
    _, resolved = prompts.get_system_prompt(lf, prompt_label)
    if resolved is None:
        raise SystemExit(f"prompt label '{prompt_label}' does not resolve to a version of "
                         f"{prompts.PROMPT_NAME} — refusing to evaluate the fallback prompt")
    print(f"prompt {prompts.PROMPT_NAME} label '{prompt_label}' → v{resolved.version}", flush=True)
    ds = lf.get_dataset(dataset)
    redteam = "redteam" in dataset
    run_name = run_name or f"{prompt_label} · {model}"

    async def task(*, item, **_):
        q = item.input["question"]
        r = await agent.run_turn(q, customer_id=item.input.get("customer_id", "C-1001"), channel="experiment",
                                 model=model, prompt_label=prompt_label,
                                 trace_name="northwind-assistant-experiment",
                                 tags=["experiment", f"prompt:{prompt_label}"])
        return {k: r[k] for k in ("answer", "sources", "retrieved", "tools_used", "blocked", "prompt_version", "model")}

    result = ds.run_experiment(
        name=f"{dataset} experiment", run_name=run_name,
        description=f"prompt label={prompt_label}, model={model}",
        task=task,
        evaluators=evals.REDTEAM_EVALUATORS if redteam else evals.GOLDEN_EVALUATORS,
        run_evaluators=[evals.averages],
        max_concurrency=max_concurrency,
        metadata={"prompt_label": prompt_label, "model": model, "release": config.RELEASE})
    config.flush()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=GOLDEN)
    ap.add_argument("--prompt-label", default="production")
    ap.add_argument("--model", default=config.AGENT_MODEL)
    ap.add_argument("--run-name", default=None)
    a = ap.parse_args()
    result = run(a.dataset, a.prompt_label, a.model, a.run_name)
    print(result.format())
    for e in result.run_evaluations:
        print(f"  {e.name:<28} {e.value}")
    print(f"\nCompare runs: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/datasets")
    if getattr(result, "dataset_run_url", None):
        print(f"This run: {result.dataset_run_url}")


if __name__ == "__main__":
    main()

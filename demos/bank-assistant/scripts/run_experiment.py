"""Run an experiment on a dataset (EXP-02 prompt A/B, EXP-03 model/config comparison).

  --prompt-label staging        A/B a prompt version (served by label)
  --model gpt-4.1               compare models on the same prompt
  --dataset northwind-redteam-v1
  --dataset northwind-disputes-v1   release comparison (story arc 5): run it once per
                                release env, e.g.
      NORTHWIND_RELEASE=assistant-1.5.0 NORTHWIND_TX_DEFAULT_DAYS=30 \\
        .venv/bin/python scripts/run_experiment.py --dataset northwind-disputes-v1 --prompt-label baseline \\
        --run-name "release 1.5.0 · lookback 30d · {version} · customer confirms"
                                Items with a `confirm_reply` (disputes) are a 2-turn conversation:
                                when the agent shows the expected charge and asks "shall I open
                                it?" instead of opening it, the simulated customer says yes once.
  --no-confirm                  single-turn only: score the first answer as it stands
  --run-name <name>             default: "<label> · <model>"; "{version}" → the prompt version

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


def run(dataset: str, prompt_label: str, model: str, run_name: str | None = None, max_concurrency: int = 6,
        confirm: bool = True):
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
    # The prompt VERSION is part of the run name: a label moves between versions, and
    # re-using a run name would append a new version's items to an old run.
    run_name = (run_name or f"{prompt_label} {{version}} · {model}").replace("{version}", f"v{resolved.version}")

    async def task(*, item, **_):
        q = item.input["question"]
        kw = dict(customer_id=item.input.get("customer_id", "C-1001"), channel="experiment",
                  model=model, prompt_label=prompt_label, trace_name="northwind-assistant-experiment",
                  tags=["experiment", f"prompt:{prompt_label}"])
        r = await agent.run_turn(q, **kw)
        out = {k: r[k] for k in ("answer", "sources", "retrieved", "tools_used", "blocked", "prompt_version", "model")}
        md = item.metadata or {}
        # Simulated customer (disputes): the agent found the expected charge and asked to
        # confirm instead of opening it → the customer says yes, once, in the same trace.
        # It never confirms a different charge (see evals.shows_transaction).
        if (confirm and md.get("confirm_reply") and not r["blocked"] and "open_dispute" not in r["tools_used"]
                and evals.shows_transaction(r["answer"], md.get("accepted_transactions") or
                                            [md.get("expected_transaction")], md.get("expected_amount"))):
            r2 = await agent.run_turn(md["confirm_reply"], history=[{"role": "user", "content": q},
                                                                    {"role": "assistant", "content": r["answer"]}], **kw)
            out.update({k: r2[k] for k in ("answer", "sources", "retrieved", "blocked")},
                       tools_used=r["tools_used"] + r2["tools_used"], first_answer=r["answer"], turns=2)
        return out

    evaluators = (evals.REDTEAM_EVALUATORS if redteam else
                  evals.DISPUTE_EVALUATORS if "disputes" in dataset else evals.GOLDEN_EVALUATORS)
    result = ds.run_experiment(
        name=f"{dataset} experiment", run_name=run_name,
        description=f"prompt label={prompt_label}, model={model}, release={config.RELEASE}, "
                    f"transaction lookback={agent.TX_DEFAULT_DAYS}d"
                    + ("" if "disputes" not in dataset else
                       ", customer confirms when asked" if confirm else ", single turn"),
        task=task,
        evaluators=evaluators,
        run_evaluators=[evals.averages],
        max_concurrency=max_concurrency,
        metadata={"prompt_label": prompt_label, "model": model, "release": config.RELEASE,
                  "tx_default_days": str(agent.TX_DEFAULT_DAYS), "customer_confirms": str(confirm)})
    config.flush()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=GOLDEN)
    ap.add_argument("--prompt-label", default="production")
    ap.add_argument("--model", default=config.AGENT_MODEL)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--no-confirm", action="store_true", help="single-turn: never send the confirmation reply")
    a = ap.parse_args()
    result = run(a.dataset, a.prompt_label, a.model, a.run_name, confirm=not a.no_confirm)
    print(result.format())
    for e in result.run_evaluations:
        print(f"  {e.name:<28} {e.value}")
    print(f"\nCompare runs: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/datasets")
    if getattr(result, "dataset_run_url", None):
        print(f"This run: {result.dataset_run_url}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Experiment: vary ONE step of the chain, gates fixed (prompt-chaining pattern).

Runs the FULL gated text-to-sql chain over the per-step dataset
`text-to-sql-analysis-step`, but the ONLY thing that differs between runs is the
analysis prompt fetched by label (`production` vs `candidate`). The response
prompt and both gates are byte-identical across variants — so the comparison
isolates the analysis step.

The success metric is `gate_pass_analysis`: did the analysis name a database the
item expects? That is exactly Gate 1's contract — the punchline of the demo is
that the gate doubles as the experiment's metric, so prompt iteration is measured
against the same contract production enforces.

Usage (from repo root, after sourcing .env; needs the stack + ANTHROPIC_API_KEY):
    python demos/text-to-sql/scripts/run_experiment.py
    python demos/text-to-sql/scripts/run_experiment.py --sample 4 --label candidate
"""

import argparse
import re
import sys
from pathlib import Path

# Make the demo package importable (parent of this scripts/ dir).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langfuse import Langfuse, Evaluation  # noqa: E402

from sql_pipeline import create_pipeline, _managed_or_fallback, ANALYSIS_FALLBACK  # noqa: E402
from gates import CATALOG_DATABASES  # noqa: E402

DATASET_NAME = "text-to-sql-analysis-step"
VARIANTS = ["production", "candidate"]


def make_task(variant: str):
    """Full gated chain, with ONLY the analysis prompt swapped to `variant`."""
    def task(*, item, **kwargs):
        pipeline = create_pipeline()
        # The only varied element: fetch the analysis prompt by the variant label.
        pipeline.analysis_prompt = _managed_or_fallback(
            "text-to-sql-analysis", ANALYSIS_FALLBACK, label=variant)
        pipeline._rebuild_analysis_chain()
        q = item.input["question"] if isinstance(item.input, dict) else str(item.input)
        return pipeline.query(q)
    return task


def gate_pass_analysis(*, item, output, **kwargs):
    """Gate 1's contract as an item-level metric: did the chain's answer surface
    at least one of the databases the item expects? (Same catalog check the gate
    enforces in-pipeline.)"""
    expected = set((item.expected_output or {}).get("databases", []))
    text = output if isinstance(output, str) else str(output)
    named = {db for db in CATALOG_DATABASES if re.search(rf"\b{re.escape(db)}\b", text, re.I)}
    ok = bool(expected & named) if expected else bool(named)
    return Evaluation(name="gate_pass_analysis",
                      value=1.0 if ok else 0.0,
                      comment=f"expected any of {sorted(expected)}, named {sorted(named)}")


def parse_args():
    ap = argparse.ArgumentParser(description="Vary the analysis prompt; gates fixed")
    ap.add_argument("--label", choices=VARIANTS, default=None,
                    help="Run only this variant (default: run both for a side-by-side compare)")
    ap.add_argument("--sample", type=int, default=None,
                    help="Limit to the first N dataset items (smoke runs)")
    return ap.parse_args()


def main():
    args = parse_args()
    variants = [args.label] if args.label else VARIANTS

    langfuse = Langfuse()
    try:
        dataset = langfuse.get_dataset(DATASET_NAME)
    except Exception as e:
        print(f"ERROR loading dataset '{DATASET_NAME}': {e}\n"
              f"Run: python demos/text-to-sql/scripts/seed_step_dataset.py")
        sys.exit(1)

    run_kwargs = {}
    if args.sample:
        # dataset.run_experiment doesn't take a limit; slice items when supported.
        try:
            dataset.items = dataset.items[: args.sample]
        except Exception:
            pass

    for variant in variants:
        result = dataset.run_experiment(
            name=f"analysis-prompt: {variant}",
            task=make_task(variant),
            evaluators=[gate_pass_analysis],
            metadata={"varied_step": "analysis", "prompt_variant": variant,
                      "gates": "fixed"},
            **run_kwargs,
        )
        try:
            print(result.format())
        except Exception:
            print(f"Experiment '{variant}' complete.")

    langfuse.flush()
    print(f"\n✓ View: Langfuse UI > Datasets > {DATASET_NAME} > Runs "
          f"(compare {' vs '.join(variants)} on gate_pass_analysis)")


if __name__ == "__main__":
    main()

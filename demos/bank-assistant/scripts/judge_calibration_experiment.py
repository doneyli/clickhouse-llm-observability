"""Calibrate the `faithfulness` judge and pick the judge model (EVA-06) — as Langfuse experiments.

Follows the judge-calibration recipe:
  * dataset  `judge-calibration/faithfulness` — what the judge sees in production
    (customer message, retrieved context, answer) with the EXPECTED VERDICT in the
    judge's own vocabulary (1.0 / 0.5 / 0.0). Labels are known BY CONSTRUCTION:
    faithful answers copied from the policy text (1.0), answers with a minor
    imprecision (0.5), and answers with a seeded material error — a wrong fee,
    deadline or limit (0.0). Complement with SME labels from the annotation queue.
  * prompt   `judge-calibration/faithfulness` — the same prompt as the live
    evaluator (scripts/seed_evals.py), versioned in Prompt Management
  * one experiment run per candidate judge model; Boolean evaluator
    `faithfulness-judge-output-correct` (exact match), run-level accuracy overall
    and per expected verdict. ~85% overall is about human-level — but only if
    every category is acceptable.

Compare the runs in Datasets → judge-calibration/faithfulness: accuracy, cost and
latency per candidate = the evidence for choosing the bank-approved judge model.

Run: .venv/bin/python scripts/judge_calibration_experiment.py [--models m1 m2 ...]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from langfuse import Evaluation  # noqa: E402

from northwind import config, knowledge  # noqa: E402
from seed_evals import FAITHFULNESS  # noqa: E402

DATASET = "judge-calibration/faithfulness"
PROMPT = "judge-calibration/faithfulness"
CANDIDATES = ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-sonnet-5-5", "gpt-4.1-mini"]
JSON_SUFFIX = '\n\nReply with JSON only: {"score": <1.0|0.5|0.0>, "reason": "<one sentence>"}'


def ctx(*ids):
    return "\n\n".join(f"[{d.id}] {d.title} ({d.url}): {d.text}" for d in map(knowledge.get, ids))


TX = ('[tool:open_dispute] {"case_id": "DSP-481220", "status": "opened", '
      '"provisional_credit_within_business_days": 10, "investigation_max_days": 45}')

# (id, query, context, answer, expected verdict, defect)
ITEMS = [
    # ── 1.0: every claim supported ──────────────────────────────────────────
    ("f01", "How much is an international wire and what's the cut-off?", ctx("KB-202"),
     "An outgoing international wire costs USD 35 (free for Premier accounts) and incoming wires cost USD 10. "
     "The cut-off is 3:00 PM local time on business days; later wires go out the next business day. [KB-202]", "1.0", "none"),
    ("f02", "What's the late fee on the Classic card?", ctx("KB-103"),
     "The late payment fee is USD 29. Paying the full statement balance by the due date avoids interest. [KB-103]", "1.0", "none"),
    ("f03", "Please dispute the Lagos charge.", ctx("KB-102") + "\n\n" + TX,
     "Done — dispute DSP-481220 is open. You'll get a provisional credit within 10 business days, and the "
     "investigation finishes within 45 days.", "1.0", "none"),
    ("f04", "What does overdraft protection cost?", ctx("KB-302"),
     "It's opt-in: it covers up to USD 500 for USD 15 per day overdrawn, with at most 3 fees a month. "
     "You can switch it off in the app at any time. [KB-302]", "1.0", "none"),
    ("f05", "Should I buy bitcoin with my savings?", ctx("KB-701"),
     "I can't give personalised investment advice or recommend crypto. A licensed Northwind advisor can help — "
     "would you like me to book a session?", "1.0", "none"),
    # ── 0.5: mostly supported, one minor imprecision ────────────────────────
    ("m01", "How much is an international wire and what's the cut-off?", ctx("KB-202"),
     "An outgoing international wire costs USD 35 and the cut-off is mid-afternoon on business days. [KB-202]",
     "0.5", "vague cut-off (3:00 PM in policy)"),
    ("m02", "How long does a replacement card take?", ctx("KB-101"),
     "A replacement card usually arrives within about a week; express delivery takes 2 business days for USD 15. [KB-101]",
     "0.5", "'about a week' vs 5-7 business days"),
    ("m03", "What are the term deposit rates?", ctx("KB-303"),
     "Term deposits pay fixed rates: 3.60% for 6 months and 3.85% for 12 months, with a USD 1,000 minimum. "
     "Withdrawing early costs you some interest. [KB-303]", "0.5", "omits the 90-day penalty size and 24m rate"),
    ("m04", "What's the daily limit for transfers to other banks?", ctx("KB-201"),
     "Transfers to other banks are free up to USD 5,000 a day on Everyday accounts, and higher on Premier. [KB-201]",
     "0.5", "'higher' instead of USD 20,000"),
    ("m05", "Do personal loans have a prepayment penalty?", ctx("KB-401"),
     "No prepayment penalty. Rates are fixed, from 8.9% to about 22% depending on your profile. [KB-401]",
     "0.5", "'about 22%' vs 21.9%"),
    # ── 0.0: a material claim is wrong or unsupported ───────────────────────
    ("e01", "How much is an international wire and what's the cut-off?", ctx("KB-202"),
     "An outgoing international wire costs USD 25 and the cut-off is 5:00 PM local time. [KB-202]",
     "0.0", "wrong fee and wrong cut-off"),
    ("e02", "How long do I have to dispute a transaction?", ctx("KB-102"),
     "You have 120 days from the statement date to dispute a transaction. [KB-102]", "0.0", "120 vs 60 days"),
    ("e03", "Does the Platinum card charge foreign transaction fees?", ctx("KB-103"),
     "Yes, the Platinum card charges a 3% foreign transaction fee on every purchase abroad. [KB-103]",
     "0.0", "Platinum is 0%"),
    ("e04", "Please dispute the Lagos charge.", ctx("KB-102") + "\n\n" + TX,
     "Your dispute DSP-481220 is open and the USD 412 has already been refunded to your account.", "0.0",
     "claims a refund the tool never made"),
    ("e05", "What's the maximum loan-to-value on a mortgage?", ctx("KB-402"),
     "We lend up to 95% of the property value with no mortgage insurance required. [KB-402]", "0.0",
     "95% / no insurance vs 80% (90% with insurance)"),
]


def seed(lf):
    try:
        lf.get_dataset(DATASET)
    except Exception:  # noqa: BLE001
        lf.create_dataset(name=DATASET, description="Labelled examples for the faithfulness judge "
                          "(labels known by construction; extend with SME labels)",
                          metadata={"judge": "faithfulness", "labels": ["1.0", "0.5", "0.0"]})
    for iid, q, c, a, label, defect in ITEMS:
        lf.create_dataset_item(dataset_name=DATASET, id=f"jc-faithfulness-{iid}",
                               input={"query": q, "context": c, "generation": a},
                               expected_output=label, metadata={"category": label, "defect": defect})
    text = FAITHFULNESS + JSON_SUFFIX
    try:
        current = lf.get_prompt(PROMPT, label="production", cache_ttl_seconds=0)
        if current.prompt == text:
            return current
    except Exception:  # noqa: BLE001
        pass
    return lf.create_prompt(name=PROMPT, prompt=text, labels=["production"], type="text",
                            config={"judge": "faithfulness", "outputs": ["1.0", "0.5", "0.0"]},
                            commit_message="same prompt as the live faithfulness evaluator")


def normalise(text: str) -> str:
    """Map the judge's reply to 1.0 / 0.5 / 0.0. Tolerant of prose or code fences
    around the JSON (a parsing failure would wrongly count against the model)."""
    m = re.search(r'"score"\s*:\s*"?([01](?:\.\d+)?)', text or "")
    if not m:
        return "unparseable"
    v = float(m.group(1))
    return "1.0" if v >= 0.75 else ("0.5" if v >= 0.25 else "0.0")


def call(model: str, prompt: str):
    if model.startswith("gpt"):
        import openai
        r = openai.OpenAI(api_key=config.OPENAI_API_KEY).chat.completions.create(
            model=model, max_tokens=500, messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content, {"input": r.usage.prompt_tokens, "output": r.usage.completion_tokens}
    import anthropic
    r = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY).messages.create(
        model=model, max_tokens=500, messages=[{"role": "user", "content": prompt}])
    return ("".join(b.text for b in r.content if getattr(b, "type", "") == "text"),
            {"input": r.usage.input_tokens, "output": r.usage.output_tokens})


def label(value) -> str:
    """Canonical verdict string. Langfuse may hand back the stored label as a number."""
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


def correct(*, output, expected_output, **_):
    ok = label(output["verdict"]) == label(expected_output)
    return Evaluation(name="faithfulness-judge-output-correct", value=ok, data_type="BOOLEAN",
                      comment=f"judge={output['verdict']} expected={label(expected_output)}")


def accuracy(*, item_results, **_):
    out, cats = [], {}
    for r in item_results:
        ok = bool(r.output) and label(r.output.get("verdict")) == label(r.item.expected_output)
        cats.setdefault(label(r.item.expected_output), []).append(1.0 if ok else 0.0)
    allv = [v for vs in cats.values() for v in vs]
    out.append(Evaluation(name="judge-accuracy", value=round(sum(allv) / len(allv), 3), comment=f"n={len(allv)}"))
    for cat, vs in sorted(cats.items()):
        out.append(Evaluation(name=f"judge-accuracy-{cat}", value=round(sum(vs) / len(vs), 3), comment=f"n={len(vs)}"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=CANDIDATES)
    a = ap.parse_args()
    lf = config.get_langfuse()
    prompt = seed(lf)
    config.flush()
    ds = lf.get_dataset(DATASET)
    print(f"Dataset {DATASET}: {len(ds.items)} items · prompt {PROMPT} v{prompt.version}\n")
    for model in a.models:
        def task(*, item, **_):
            text = prompt.compile(**item.input)
            t = time.perf_counter()
            with lf.start_as_current_observation(as_type="generation", name="faithfulness-judge", model=model,
                                                 input=text, prompt=prompt) as g:
                raw, usage = call(model, text)
                g.update(output=raw, usage_details=usage)
            return {"verdict": normalise(raw), "raw": raw, "latency_s": round(time.perf_counter() - t, 2)}

        res = ds.run_experiment(name="faithfulness judge calibration", run_name=f"judge · {model} · prompt v{prompt.version}",
                                description=f"faithfulness judge prompt v{prompt.version} on {model}",
                                task=task, evaluators=[correct], run_evaluators=[accuracy], max_concurrency=5,
                                metadata={"judge_model": model, "prompt_version": prompt.version})
        scores = {e.name: e.value for e in res.run_evaluations}
        wrong = [(r.item.metadata.get("defect"), label(r.item.expected_output), r.output.get("verdict"))
                 for r in res.item_results if r.output and label(r.output.get("verdict")) != label(r.item.expected_output)]
        print(f"{model:<20} accuracy={scores.get('judge-accuracy')}  "
              f"[1.0]={scores.get('judge-accuracy-1.0')} [0.5]={scores.get('judge-accuracy-0.5')} "
              f"[0.0]={scores.get('judge-accuracy-0.0')}")
        for d, exp, got in wrong:
            print(f"    miss: expected {exp} got {got} — {d}")
        if getattr(res, "dataset_run_url", None):
            print(f"    {res.dataset_run_url}")
    config.flush()
    print(f"\nCompare: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/datasets")


if __name__ == "__main__":
    main()

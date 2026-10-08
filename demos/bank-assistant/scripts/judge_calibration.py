"""Judge calibration + judge-model bake-off (EVA-06, "which judge model does the bank approve?").

1. CALIBRATION — production `faithfulness` judge vs SME labels (`sme-faithfulness`)
   on the same observations (the SME review queue). Reports agreement on the
   pass/fail decision, Cohen's kappa and mean absolute error, and lists the
   disagreements to read. Same numbers as Langfuse → Scores → Analytics.
2. BAKE-OFF (--bakeoff) — re-grade the SME-labelled answers with candidate judge
   models using the SAME prompt, and compare agreement with the SMEs, latency and
   tokens. Every judge call is traced (trace `judge-bakeoff`, environment
   `evaluation`), so Langfuse prices each candidate from its own model table.

Selection criteria for the bank (decide in this order):
  a. agreement with SMEs (kappa ≥ 0.6 on the pass/fail decision) — non-negotiable
  b. approved for use: data residency, reachable inside the VPC (Bedrock / gateway),
     vendor risk sign-off
  c. cost per 1,000 evaluations at the sampling rate you will run
  d. latency (matters only for synchronous guardrails, not for async judges)
  e. stability: re-run the same items — a judge that disagrees with itself
     cannot be calibrated

Run: .venv/bin/python scripts/judge_calibration.py [--bakeoff]
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_evals import FAITHFULNESS  # noqa: E402

CANDIDATES = ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-sonnet-5-5", "gpt-4.1-mini"]
PASS = 0.75


def all_scores() -> list:
    out, cursor = [], None
    while True:
        d = config.api("GET", "/api/public/v3/scores",
                       params={"limit": 100, "fields": "core,subject", **({"cursor": cursor} if cursor else {})})
        out += d.get("data") or []
        cursor = (d.get("meta") or {}).get("cursor") or (d.get("meta") or {}).get("nextCursor")
        if not cursor or not d.get("data"):
            return out


def by_obs(scores, name) -> dict:
    res = {}
    for s in scores:
        subj = s.get("subject") or {}
        oid = (subj.get("id") if subj.get("kind") in ("observation", "OBSERVATION") else None) \
            or subj.get("observationId") or s.get("observationId")
        if s["name"] == name and oid and isinstance(s.get("value"), (int, float)):
            res[oid] = (float(s["value"]), s.get("traceId") or (s.get("subject") or {}).get("traceId"))
    return res


def kappa(a: list, b: list) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def report(title, pairs):
    judge = [j >= PASS for j, _ in pairs]
    human = [h >= PASS for _, h in pairs]
    agree = sum(x == y for x, y in zip(judge, human)) / len(pairs)
    mae = sum(abs(j - h) for j, h in pairs) / len(pairs)
    print(f"{title:<22} n={len(pairs):<3} agreement={agree:.0%}  kappa={kappa(judge, human):.2f}  MAE={mae:.2f}")


def judge_once(lf, model, item):
    prompt = (FAITHFULNESS.replace("{{query}}", item["query"]).replace("{{context}}", item["context"])
              .replace("{{generation}}", item["answer"])
              + '\n\nReply with JSON only: {"score": <0|0.5|1>, "reason": "<one sentence>"}')
    t = time.perf_counter()
    with lf.start_as_current_observation(as_type="generation", name="faithfulness-judge", model=model,
                                         input=prompt) as g:
        if model.startswith("gpt"):
            import openai
            r = openai.OpenAI(api_key=config.OPENAI_API_KEY).chat.completions.create(
                model=model, max_tokens=200, messages=[{"role": "user", "content": prompt}])
            text, usage = r.choices[0].message.content, {"input": r.usage.prompt_tokens, "output": r.usage.completion_tokens}
        else:
            import anthropic
            r = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY).messages.create(
                model=model, max_tokens=200, messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            usage = {"input": r.usage.input_tokens, "output": r.usage.output_tokens}
        g.update(output=text, usage_details=usage)
    try:
        score = float(json.loads(re.search(r"\{.*\}", text, re.S).group(0))["score"])
    except Exception:  # noqa: BLE001
        score = 0.0
    return score, time.perf_counter() - t, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bakeoff", action="store_true")
    ap.add_argument("--models", nargs="*", default=CANDIDATES)
    a = ap.parse_args()
    scores = all_scores()
    judge, sme = by_obs(scores, "faithfulness"), by_obs(scores, "sme-faithfulness")
    common = [o for o in sme if o in judge]
    print(f"SME labels: {len(sme)} · judge scores: {len(judge)} · matched pairs: {len(common)}\n")
    if len(common) < 5:
        print("Label at least 5-10 items in the 'SME review — assistant answers' queue first "
              "(score sme-faithfulness), then re-run.")
        return
    report("faithfulness (prod)", [(judge[o][0], sme[o][0]) for o in common])
    dis = [o for o in common if (judge[o][0] >= PASS) != (sme[o][0] >= PASS)]
    for o in dis:
        print(f"   disagreement: judge={judge[o][0]} sme={sme[o][0]}  {config.trace_url(sme[o][1])}")
    if not a.bakeoff:
        return

    items = []
    for o in common:
        tid = sme[o][1]
        obs = config.api("GET", "/api/public/v2/observations",
                         params={"traceId": tid, "fields": "core,basic,io,metadata", "limit": 100})["data"]
        root = next((x for x in obs if x["id"] == o), None)
        if not root:
            continue
        md = root.get("metadata") or {}
        items.append({"obs": o, "query": str(root.get("input")), "answer": str(root.get("output")),
                      "context": str(md.get("context", "")), "human": sme[o][0]})
    lf = config.get_langfuse()
    print(f"\nBake-off on {len(items)} SME-labelled answers (same prompt, different judge models):")
    from langfuse import propagate_attributes
    for model in a.models:
        pairs, lat, toks = [], [], 0
        with propagate_attributes(trace_name="judge-bakeoff", tags=["judge-bakeoff", f"judge:{model}"],
                                  environment="evaluation"):
            with lf.start_as_current_observation(as_type="evaluator", name=f"bakeoff {model}") as root:
                for it in items:
                    s, dt, u = judge_once(lf, model, it)
                    pairs.append((s, it["human"]))
                    lat.append(dt)
                    toks += u["input"] + u["output"]
                root.update(output={"pairs": len(pairs)})
        report(model, pairs)
        print(f"{'':<22} latency p50={sorted(lat)[len(lat) // 2]:.1f}s  tokens/eval={toks // max(1, len(items))}")
    config.flush()
    print(f"\nCost per candidate: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/traces?search=judge-bakeoff")


if __name__ == "__main__":
    main()

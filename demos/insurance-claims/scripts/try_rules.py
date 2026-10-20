"""
Fast local loop for the SOP-rules step (no Langfuse prompt, no graph): perfect intake → retriever → one LLM
call per claim → recall / precision / critical recall against the oracle. For prompt iteration only; the
real comparison is an experiment (scripts/run_experiment.py).

    .venv/bin/python scripts/try_rules.py --n 20 --model claude-sonnet-4-6 [--prompt v1|v2|v3]
"""
import argparse, asyncio, json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from claims import claimsdb, evals, prompts, sops  # noqa: E402
from claims.graph import RuleExtraction, make_llm  # noqa: E402

TEXTS = {"v1": prompts.SOP_RULES_V1, "v2": prompts.SOP_RULES_V2_CONCISE, "v3": prompts.SOP_RULES_V3_CONSERVATIVE}


async def one(c, system, model, sem):
    async with sem:
        cf, obs = claimsdb.ground_truth_inputs(c)
        docs = sops.retrieve(cf["product_line"], cf["governing_state"])
        sop_text = "\n\n".join(f"===== {d['sop_id']} (version {d['version']}) =====\n{d['text']}" for d in docs)
        llm = make_llm(model, 4000).with_structured_output(RuleExtraction, method="json_schema")
        r = await llm.ainvoke([SystemMessage(content=system), HumanMessage(
            content=f"CLAIM FILE:\n{json.dumps(cf, indent=1)}\n\nRETRIEVED SOPs:\n{sop_text}")])
        got = evals.cited_ids([x.model_dump() for x in r.rules])
        want = set(c["expected"]["rule_ids"]); crit = set(c["expected"]["critical_rule_ids"])
        return c, got, want, crit


async def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=20); ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--prompt", default="v1"); ap.add_argument("--seed", type=int, default=7); a = ap.parse_args()
    claims = [c for c in claimsdb.load().values() if c["_set"] == "golden"]
    random.Random(a.seed).shuffle(claims)
    system = TEXTS[a.prompt].replace("{{company}}", prompts.COMPANY)
    sem = asyncio.Semaphore(8)
    res = await asyncio.gather(*[one(c, system, a.model, sem) for c in claims[:a.n]])
    R = P = C = 0.0; nc = 0
    for c, got, want, crit in res:
        rec = len(got & want) / len(want) if want else 1; pre = len(got & want) / len(got) if got else 1
        R += rec; P += pre
        if crit: C += len(crit & got) / len(crit); nc += 1
        if got != want:
            print(f"{c['claim_id']} {c['scenario'][:55]:<55} missed={sorted(want-got)} extra={sorted(got-want)}")
    n = len(res)
    print(f"\n{a.model} prompt {a.prompt}: recall {R/n:.3f}  precision {P/n:.3f}  critical-recall {C/max(nc,1):.3f} (n={n}, critical n={nc})")

asyncio.run(main())

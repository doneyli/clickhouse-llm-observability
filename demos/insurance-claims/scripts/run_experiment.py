"""
Run an experiment on a dataset.

  --suite rules   (default) the SOP-rules step on claims-golden-v1 with a perfect intake — prompt A/B and the
                  CI gate. Isolates the step under change: same claim files, only the prompt/model differ.
  --suite e2e     the whole graph from the claim id: MCP, A2A intake (vision), policy agent, rules,
                  adjudication — field accuracy, tool trajectory and every rule metric together
  --suite intake  the doc-intake service on claims-intake-v1 (scanned documents as media)
  --suite adversarial   e2e on claims-adversarial-v1 (injection-resisted)

  --prompt-label staging        A/B a claims-sop-rules version (served by label)
  --model claude-haiku-5-5      compare models on the same prompt (pre-upgrade / cost check)
  --release claims-1.4.0        reproduce a release (the retriever bug)
  --limit 20                    first N items (quick look)

Compare runs in Langfuse: Datasets → <dataset> → select runs → Compare. Exit 0 always; the gate
(scripts/prompt_gate.py) is what fails builds.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import a2a_client, claimsdb, config, evals, graph, prompts, servicing  # noqa: E402

DATASETS = {"rules": "claims-golden-v1", "e2e": "claims-golden-v1", "intake": "claims-intake-v1",
            "adversarial": "claims-adversarial-v1", "service": "annuity-service-golden-v1"}
# The prompt each suite's label refers to (the gated prompt).
PROMPT = {"service": servicing.RULES_PROMPT}


def resolve(lf, label: str, name: str = "claims-sop-rules"):
    _, client, cfg = prompts.get(lf, name, label)
    if client is None:
        raise SystemExit(f"prompt label '{label}' does not resolve to a version of {name} — "
                         "refusing to evaluate the fallback prompt")
    return client, cfg


def run(suite: str = "rules", prompt_label: str = "production", model: str | None = None, release: str | None = None,
        run_name: str | None = None, limit: int | None = None, max_concurrency: int = 6, dataset: str | None = None):
    lf = config.get_langfuse()
    pname = PROMPT.get(suite, "claims-sop-rules")
    client, pcfg = resolve(lf, prompt_label, pname)
    model_used = model or pcfg.get("model") or config.AGENT_MODEL
    dataset = dataset or DATASETS[suite]
    labels = {pname: prompt_label}
    rel = release or config.RELEASE
    # Run names must be unique: Langfuse APPENDS items to an existing run with the same name.
    stamp = time.strftime("%m-%d %H:%M")
    run_name = (run_name or f"{suite} · {pname.replace('claims-', '')} {prompt_label} v{{version}} · {model_used} · {rel} · {stamp}").replace(
        "{version}", str(client.version))
    print(f"{suite} on {dataset} · {pname} '{prompt_label}' → v{client.version} · {model_used} · {rel}",
          flush=True)
    ds = lf.get_dataset(dataset)
    items = ds.items[:limit] if limit else ds.items

    async def task(*, item, **_):
        inp = item.input
        if suite == "service":
            req = claimsdb.get_service(inp["request_id"])
            _, observed = claimsdb.service_request_file(req)
            return await servicing.run_service_step(inp["request_id"], inp["request_file"], observed, labels=labels,
                                                    model=model)
        if suite == "rules":
            claim = claimsdb.get(inp["claim_id"])
            _, observed = claimsdb.ground_truth_inputs(claim)
            return await graph.run_rules_step(inp["claim_id"], inp["claim_file"], observed, labels=labels,
                                              model=model, release=release)
        if suite == "intake":
            parts = [{"kind": "file", "file": {"name": d["name"], "mimeType": "image/png",
                                               "bytes": base64.b64encode(d["content"].fetch_bytes()).decode()}}
                     for d in inp["documents"]]
            res = await a2a_client.send_message(f"Extract the fields from the documents of claim {inp['claim_id']}.",
                                                parts, metadata={"claim_id": inp["claim_id"]})
            return {"intake": a2a_client.parse_json(res["text"])}
        res = await graph.run_claim(inp["claim_id"], mode="auto", labels=labels, model=model, release=release,
                                    session_id=None, tags=["experiment", f"suite:{suite}"])
        st = res["state"]
        return {"rules": st.get("rules") or [], "decision": res["recommendation"], "calculation": res["calculation"],
                "citation_validity": st.get("citation_validity"), "retrieval_coverage": st.get("retrieval_coverage"),
                "intake": st.get("intake"), "tools_called": st.get("tools_called"), "claim_file": st.get("claim_file"),
                "guard": res["guard"]}

    evaluators = {"rules": evals.RULE_EVALUATORS, "e2e": evals.E2E_EVALUATORS, "adversarial": evals.E2E_EVALUATORS,
                  "intake": evals.INTAKE_EVALUATORS, "service": evals.SERVICE_EVALUATORS}[suite]
    t0 = time.time()
    result = lf.run_experiment(
        name=f"{dataset} · {suite}", run_name=run_name, data=items, task=task, evaluators=evaluators,
        run_evaluators=[evals.averages], max_concurrency=max_concurrency,
        description=f"suite={suite}, {pname} label={prompt_label} (v{client.version}), model={model_used}, "
                    f"release={rel}", metadata={"suite": suite, "prompt_label": prompt_label,
                                                "prompt_version": str(client.version), "model": model_used,
                                                "release": rel})
    config.flush()
    print(f"{len(items)} items in {time.time() - t0:.0f}s")
    return result, client.version


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="rules", choices=sorted(DATASETS))
    ap.add_argument("--prompt-label", default="production")
    ap.add_argument("--model", default=None)
    ap.add_argument("--release", default=None)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=6)
    a = ap.parse_args()
    result, _ = run(a.suite, a.prompt_label, a.model, a.release, a.run_name, a.limit, a.concurrency)
    for e in result.run_evaluations:
        print(f"  {e.name:<32} {e.value}")
    if getattr(result, "dataset_run_url", None):
        print(f"\nThis run: {result.dataset_run_url}")


if __name__ == "__main__":
    main()

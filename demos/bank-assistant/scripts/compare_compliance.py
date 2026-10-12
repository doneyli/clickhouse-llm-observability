"""Compare original, matched single, and split judges on frozen answers.

--dry-run: validate fixtures, no credentials or model calls.
--preview: also export an unscored HTML walkthrough, clearly marked as a preview.
Default: three real Langfuse experiment runs, a JSON report and an HTML report.
Synthetic expected labels demonstrate mechanics; they are not SME validation.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))
from northwind.compliance import (LEGACY_COMPLIANCE, POLICIES, RUBRIC_VERSION,
                                 SINGLE_COMPLIANCE, aggregate, render)  # noqa: E402

DATASET = "banking-compliance-comparison-v1"
ARMS = {"original": "Original single judge", "single": "Matched single judge", "split": "Five policy judges"}
FIXTURES = DEMO_DIR / "data" / "compliance_cases.json"


def load_cases(path=FIXTURES):
    cases = json.loads(Path(path).read_text())["cases"]
    ids = set()
    for case in cases:
        if case["id"] in ids:
            raise ValueError("Duplicate case id: " + case["id"])
        ids.add(case["id"])
        if set(case["input"]) != {"query", "context", "generation"}:
            raise ValueError("Each input must contain query, context and generation")
        if set(case["expected"]) != {p.id for p in POLICIES}:
            raise ValueError("Every case needs five expected policy labels")
        for p in POLICIES:
            if case["expected"][p.id] not in p.categories:
                raise ValueError(f"Invalid expected label for {case['id']} {p.id}")
    return cases


def parse_reply(raw, policy=None):
    """Strict enums: an invalid score must never round into a compliant answer."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or not isinstance(data.get("reason"), str):
            raise ValueError("missing reason")
        if policy:
            if data.get("verdict") not in policy.categories:
                raise ValueError("invalid verdict")
        else:
            score = data["score"]
            if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float))
                                      or not math.isfinite(score) or score not in (0, 0.5, 1)):
                raise ValueError("invalid score")
        return {**data, "valid": True}
    except (ValueError, TypeError, KeyError):
        return {"verdict": "unknown", "score": None, "valid": False,
                "reason": "Judge returned invalid structured output; review required."}


def evaluate_answer(arm, inputs, judge):
    """judge(template, inputs, policy) is injectable; expected labels never enter it."""
    started = time.perf_counter()
    if arm == "split":
        with ThreadPoolExecutor(max_workers=5) as pool:
            # Each worker needs its own copied context to preserve the active
            # Langfuse/OTel parent and experiment attributes across threads.
            pending = {p.id: pool.submit(copy_context().run, judge, p.prompt, inputs, p) for p in POLICIES}
            parts = {pid: future.result() for pid, future in pending.items()}
        result = aggregate({k: v["verdict"] for k, v in parts.items()})
        output = {"score": result.score, "status": result.status, "coverage": result.coverage,
                  "reason": result.comment(), "policies": parts, "valid": all(p["valid"] for p in parts.values())}
        calls = list(parts.values())
    else:
        call = judge(LEGACY_COMPLIANCE if arm == "original" else SINGLE_COMPLIANCE, inputs, None)
        score = call["score"]
        status = {0: "violation", 0.5: "minor-issue", 1: "compliant"}.get(score, "review-needed")
        output = {**call, "status": status, "policies": {}}
        calls = [call]
    output.update(latency_s=round(time.perf_counter() - started, 3), calls=len(calls),
                  input_tokens=sum(c.get("input_tokens", 0) for c in calls),
                  output_tokens=sum(c.get("output_tokens", 0) for c in calls))
    return output


def expected_score(expected):
    return aggregate(expected).score


def summary(rows):
    """Denominators include invalid outputs. Report abstentions separately."""
    n = len(rows)
    known = [r for r in rows if expected_score(r["expected"]) is not None]
    violations = [r for r in rows if expected_score(r["expected"]) == 0]
    safe = [r for r in rows if expected_score(r["expected"]) in (0.5, 1)]
    valid = lambda r: r.get("output", {}).get("valid", False)
    value = lambda r: r.get("output", {}).get("score")
    agrees = lambda r: valid(r) and value(r) == expected_score(r["expected"])
    out = {"n": n, "agreement": sum(agrees(r) for r in rows) / n if n else None,
           "invalid": sum(not valid(r) for r in rows),
           "unresolved": sum(value(r) is None for r in rows),
           "missed_violations": sum(value(r) != 0 for r in violations), "violation_cases": len(violations),
           "false_alarms": sum(value(r) == 0 for r in safe), "non_violation_cases": len(safe),
           "known_cases": len(known),
           "input_tokens": sum(r.get("output", {}).get("input_tokens", 0) for r in rows),
           "output_tokens": sum(r.get("output", {}).get("output_tokens", 0) for r in rows)}
    lat = sorted(r.get("output", {}).get("latency_s", 0) for r in rows)
    out["latency_p50_s"] = lat[len(lat) // 2] if lat else None
    # Per-policy diagnostic agreement exists only for the split arm. Do not
    # invent individual policy labels for a single judge's overall score.
    for p in POLICIES:
        eligible = [r for r in rows if r.get("output", {}).get("policies")]
        if eligible:
            out[p.id + "_agreement"] = sum(
                r["output"]["policies"].get(p.id, {}).get("valid", False)
                and r["output"]["policies"].get(p.id, {}).get("verdict") == r["expected"][p.id]
                for r in eligible) / len(eligible)
    return out


def report_html(report, cases):
    esc = lambda value: html.escape(str(value), quote=True)
    preview = report.get("preview", False)
    intro = ("Unscored preview — no judges have run. Expected labels below are synthetic fixture labels."
             if preview else "Actual judge outputs on frozen synthetic answers. Fixture agreement is not independent SME validation.")
    by_case = {(arm, row["id"]): row for arm, rows in report.get("runs", {}).items() for row in rows}
    panels = []
    for case in cases:
        exp = aggregate(case["expected"])
        columns = []
        for arm, title in ARMS.items():
            row = by_case.get((arm, case["id"]), {})
            out = row.get("output") or {}
            status = out.get("status", "not run")
            details = "".join(f"<li><b>{p.id}</b> {esc(p.title)}: <strong>{esc(out['policies'][p.id]['verdict'])}</strong>"
                              f"<p>{esc(out['policies'][p.id]['reason'])}</p></li>"
                              for p in POLICIES if p.id in out.get("policies", {}))
            link = row.get("trace_url")
            trace_link = f'<a href="{esc(link)}">Open judge trace in Langfuse</a>' if link else ""
            columns.append(f'<section><h3>{esc(title)}</h3><div class="status">{esc(status)}</div>'
                           f'<p>{esc(out.get("reason", "Awaiting a real experiment run."))}</p><ul>{details}</ul>'
                           f'<small>{esc(out.get("calls", "—"))} calls · {esc(out.get("latency_s", "—"))} s · '
                           f'{esc(out.get("input_tokens", "—"))} input / {esc(out.get("output_tokens", "—"))} output tokens</small>'
                           f'<p>{trace_link}</p></section>')
        panels.append(f'<article><h2>{esc(case["title"])}</h2><p class="expected">Expected: {esc(exp.status)} · '
                      + " · ".join(f'{p.id}: {esc(case["expected"][p.id])}' for p in POLICIES)
                      + f'</p><p><b>Customer:</b> {esc(case["input"]["query"])}</p>'
                      + f'<blockquote>{esc(case["input"]["generation"])}</blockquote>'
                      + f'<details><summary>Evidence provided to matched single and split judges</summary>'
                      + f'<pre>{esc(case["input"]["context"] or "No authoritative context provided.")}</pre></details>'
                      + '<div class="columns">' + "".join(columns) + '</div></article>')
    metric_rows = []
    for arm, rows in report.get("runs", {}).items():
        s = summary(rows)
        metric_rows.append(f'<tr><td>{esc(ARMS[arm])}</td><td>{s["agreement"]:.0%}</td>'
                           f'<td>{s["missed_violations"]}/{s["violation_cases"]}</td>'
                           f'<td>{s["false_alarms"]}/{s["non_violation_cases"]}</td><td>{s["unresolved"]}</td>'
                           f'<td>{s["invalid"]}</td><td>{s["input_tokens"] + s["output_tokens"]:,}</td></tr>')
    metrics = ('<table><thead><tr><th>Approach</th><th>Fixture agreement</th><th>Missed violations</th>'
               '<th>False alarms</th><th>Unresolved</th><th>Invalid</th><th>Tokens</th></tr></thead><tbody>'
               + "".join(metric_rows) + '</tbody></table>') if metric_rows else ""
    return ('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Northwind — Compliance evaluator comparison</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f3f5f8;color:#1b1917;margin:0}main{max-width:1200px;margin:auto;padding:36px 24px}
h1{font-size:36px;line-height:1.2}h2{font-size:23px}h3{font-size:17px}article{background:white;padding:28px;margin:24px 0;border:1px solid #d9dfe8;border-radius:12px}
.columns{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-top:24px}section{background:#f8f9fc;padding:20px;border:1px solid #e2e6ee;border-radius:8px}
.status{font-weight:700;background:#e8eff9;padding:6px 12px;border-radius:4px;display:inline-block}.expected,small{color:#49556b}
blockquote{border-left:4px solid #4e9cff;padding:12px 20px;margin:16px 0;background:#f3f7ff}pre{white-space:pre-wrap;font:14px/1.6 monospace}
ul{padding-left:18px}li p{font-size:14px}a{color:#1f65bb}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #d9dfe8}
@media(max-width:800px){.columns{grid-template-columns:1fr}table{display:block;overflow:auto}}details{margin:12px 0}summary{cursor:pointer}
</style><main><p>Northwind Bank · Conduct policy P1–P5</p><h1>One compliance decision.<br>Five explainable policy checks.</h1>'''
    + f'<p>{esc(intro)}</p><p>The matched single and split arms use identical rules, context and frozen answers. '
      'The original arm reproduces the old rubric without policy context. Token counts are measured usage, not USD prices.</p>'
    + metrics + "".join(panels) + '</main></html>')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--cases", type=Path, default=FIXTURES)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--model")
    ap.add_argument("--output-dir", type=Path, default=DEMO_DIR / "logs" / "compliance-comparison")
    a = ap.parse_args()
    if a.limit < 0:
        ap.error("--limit must be nonnegative")
    cases = load_cases(a.cases)
    if a.limit:
        cases = cases[:a.limit]
    if not cases:
        ap.error("No cases to evaluate")
    if a.dry_run or a.preview:
        print(f"Validated {len(cases)} cases; {len(cases) * 7} planned model calls across three arms. No calls made.")
        if a.preview:
            a.output_dir.mkdir(parents=True, exist_ok=True)
            path = a.output_dir / "preview.html"
            path.write_text(report_html({"preview": True}, cases))
            print(path)
        return

    # Import runtime dependencies only after the dependency-free preview path.
    os.environ["NORTHWIND_ENVIRONMENT"] = "evaluation"
    os.environ["NORTHWIND_SAMPLE_RATE"] = "1.0"
    from northwind import config
    from langfuse import Evaluation, propagate_attributes
    from langfuse.api import NotFoundError
    import anthropic

    if not (config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY and config.ANTHROPIC_API_KEY):
        ap.error("Configure the bank demo's .env/.env.cloud Langfuse credentials and Anthropic key first")
    model = a.model or config.JUDGE_MODEL
    lf = config.get_langfuse()
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    # Fail auth before making any model calls. Do not use project_id()'s fallback.
    project = config.api("GET", "/api/public/projects")["data"][0]
    try:
        lf.get_dataset(DATASET)
    except NotFoundError:
        lf.create_dataset(name=DATASET, description="Frozen synthetic answers: original vs matched single vs five policy judges",
                          metadata={"rubric_version": RUBRIC_VERSION, "label_source": "synthetic-by-construction"})
    for case in cases:
        lf.create_dataset_item(dataset_name=DATASET, id="conduct-v2-" + case["id"], input=case["input"],
                               expected_output=case["expected"], metadata={"case_id": case["id"], "title": case["title"],
                               "label_source": "synthetic-by-construction"})
    ds = lf.get_dataset(DATASET)
    # Restrict an existing hosted dataset to the exact selected cases; no stale
    # or unrelated items may sneak into one arm and confound the comparison.
    selected = {"conduct-v2-" + c["id"] for c in cases}
    ds.items = [item for item in ds.items if item.id in selected]
    if len(ds.items) != len(cases):
        raise RuntimeError("Hosted dataset does not contain every selected case")

    def judge(template, inputs, policy):
        suffix = ('\n\nReply with JSON only: {"reason": "<brief redacted evidence>", "verdict": "<allowed label>"}'
                  if policy else '\n\nReply with JSON only: {"reason": "<brief redacted evidence>", "score": <0|0.5|1|null>}')
        prompt = render(template, inputs) + suffix
        with lf.start_as_current_observation(as_type="generation", name=policy.name if policy else "single-compliance-judge",
                                             model=model, input=prompt) as generation:
            try:
                response = client.messages.create(model=model, temperature=0, max_tokens=600,
                                                   messages=[{"role": "user", "content": prompt}])
                raw = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
                usage = {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
                generation.update(output=raw, usage_details=usage)
                return {**parse_reply(raw, policy), "input_tokens": usage["input"], "output_tokens": usage["output"]}
            except Exception as exc:
                generation.update(level="ERROR", status_message=type(exc).__name__)
                return {"verdict": "unknown", "score": None, "valid": False,
                        "reason": f"Judge call failed ({type(exc).__name__}); no verdict available."}

    def evaluators(*, output, expected_output, **_):
        expected = aggregate(expected_output)
        result = [Evaluation(name="overall-agreement", value=bool(output["valid"] and output["score"] == expected.score),
                             data_type="BOOLEAN"),
                  Evaluation(name="banking-compliance-status", value=output["status"], data_type="CATEGORICAL"),
                  Evaluation(name="invalid-judge-output", value=not output["valid"], data_type="BOOLEAN"),
                  Evaluation(name="judge-latency-s", value=output["latency_s"]),
                  Evaluation(name="judge-total-tokens", value=output["input_tokens"] + output["output_tokens"])]
        if output["score"] is not None:
            result.append(Evaluation(name="banking-compliance", value=output["score"], comment=output["reason"]))
        for p in POLICIES:
            if p.id in output["policies"]:
                got = output["policies"][p.id]
                result.extend([Evaluation(name=p.name, value=got["verdict"], data_type="CATEGORICAL", comment=got["reason"]),
                               Evaluation(name=p.id + "-agreement", value=bool(got["valid"] and got["verdict"] == expected_output[p.id]),
                                          data_type="BOOLEAN")])
        return result

    def run_metrics(*, item_results, **_):
        rows = [{"expected": r.item.expected_output, "output": r.output or {}} for r in item_results]
        return [Evaluation(name=k.replace("_", "-"), value=v) for k, v in summary(rows).items() if v is not None]

    report = {"preview": False, "model": model, "rubric_version": RUBRIC_VERSION,
              "label_source": "synthetic-by-construction", "runs": {}, "run_urls": {}}
    a.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    for arm, title in ARMS.items():
        def task(*, item, **_):
            with propagate_attributes(trace_name="northwind-compliance-comparison", environment="evaluation",
                                      tags=["compliance-comparison", "approach:" + arm], version=RUBRIC_VERSION):
                return evaluate_answer(arm, item.input, judge)
        experiment = ds.run_experiment(name="Compliance evaluator comparison", run_name=f"{stamp} · {title}",
                                       task=task, evaluators=[evaluators], run_evaluators=[run_metrics], max_concurrency=2,
                                       metadata={"approach": arm, "judge_model": model, "rubric_version": RUBRIC_VERSION})
        rows = [{"id": r.item.metadata["case_id"], "expected": r.item.expected_output, "output": r.output or {},
                 "trace_url": f"{config.LANGFUSE_BASE_URL}/project/{project['id']}/traces/{r.trace_id}" if r.trace_id else None}
                for r in experiment.item_results]
        report["runs"][arm] = rows
        report["run_urls"][arm] = getattr(experiment, "dataset_run_url", None)
        # Save after each arm so a later failure cannot erase completed results.
        (a.output_dir / "results.json").write_text(json.dumps(report, indent=2))
        (a.output_dir / "comparison.html").write_text(report_html(report, cases))
        print(title + ": " + json.dumps(summary(rows)), flush=True)
        print(report["run_urls"][arm] or "Experiment completed; open dataset in Langfuse", flush=True)
    config.flush()
    print(a.output_dir / "comparison.html")


if __name__ == "__main__":
    main()

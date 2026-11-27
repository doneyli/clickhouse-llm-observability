#!/usr/bin/env python
"""CI quality gate: evaluate a labelled planner + analyst prompt pair BEFORE it gets `production`.

    .venv/bin/python scripts/prompt_gate.py --prompt-label candidate-coarse   # the coarse-steps candidate -> exit 1
    .venv/bin/python scripts/prompt_gate.py --prompt-label production         # -> exit 0
    .venv/bin/python scripts/prompt_gate.py --prompt-label production --limit 3   # smoke: ~$0.30, ~1.5 min

One label names the pair: the gate resolves BOTH `analytics-agentic-speed-planner` and
`analytics-agentic-speed-analyst` at that label (a label missing on either aborts, because the
agent would silently run its in-code fallback text and the gate would grade a prompt nobody wrote).

It runs the `scenario-explorer-questions` dataset (15 questions, exact SQL ground truth) at the
standard Experiment A wide config from cicd/thresholds.json (300 scenarios/round, 2 rounds), with
scripts/run_experiment.py's task, evaluators and Langfuse read-back, then compares accuracy, the
must-pass questions and the per-question run metrics with the thresholds:

- **hard** (block the release): every item evaluated and read back from Langfuse, accuracy, each
  must-pass question (the hero, q-padding), mean LLM cost per question;
- **soft** (warn): mean wall time, ClickHouse's share of wall time, scenarios swept, model calls,
  errored items.

A MISSING metric fails, never passes. The verdict goes to stdout and, in GitHub Actions, to
$GITHUB_STEP_SUMMARY. Before any model call it checks the Langfuse keys map to the demo's project,
the label resolves on both prompts, and the read-only ClickHouse user can read the table, so a
misconfigured run costs nothing and says why.

Exit codes: 0 passed (or --warn-only); 1 blocked, or nothing evaluated (pre-flight failed).
`--limit N` runs the first N questions in questions.yaml order (the hero first): a smoke test whose
verdict is indicative only; the historical reference used all 15 and calibration is provisional.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import run_experiment as rx  # noqa: E402

from explorer import prompts as P  # noqa: E402
from explorer.config import ClickHouseSettings, RunConfig, load_env, model_prices, UnknownModelPriceError  # noqa: E402
from explorer.evaluators import answer_correct, load_questions, summarize  # noqa: E402

THRESHOLDS_PATH = ROOT / "cicd" / "thresholds.json"
EXPECTED_PROJECT = "analytics-agentic-speed"
GATE_EXPERIMENT = "Prompt gate"
GATED_PROMPTS = (P.PLANNER, P.ANALYST)
MUST_PASS_PREFIX = "must_pass:"


class GateAbort(RuntimeError):
    """Pre-flight failed: nothing was evaluated, so the gate makes no quality claim (exit 1)."""


# ----------------------------------------------------------------------- thresholds
def load_thresholds(path: Path | str = THRESHOLDS_PATH) -> dict[str, Any]:
    """Parse and validate cicd/thresholds.json. Keys starting with '_' are commentary."""
    t = json.loads(Path(path).read_text())
    for key in ("dataset", "config", "hard", "soft"):
        if key not in t:
            raise ValueError(f"{path}: missing '{key}'")
    for kind in ("hard", "soft"):
        for name, rule in t[kind].items():
            bounds = {k: v for k, v in rule.items() if not k.startswith("_")}
            if len(bounds) != 1 or next(iter(bounds)) not in ("min", "max"):
                raise ValueError(f"{path}: {kind}.{name} needs exactly one of 'min' or 'max', got {sorted(bounds)}")
            v = next(iter(bounds.values()))
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{path}: {kind}.{name} bound must be a number, got {v!r}")
    must = t.get("must_pass", [])
    if not isinstance(must, list) or not all(isinstance(q, str) and q for q in must):
        raise ValueError(f"{path}: must_pass must be a list of question ids")
    cfg = t["config"]
    for key in ("max_scenarios_per_round", "max_rounds"):
        if not isinstance(cfg.get(key), int) or cfg[key] < 1:
            raise ValueError(f"{path}: config.{key} must be a positive integer")
    return t


def check(rule: Mapping[str, Any], value: Optional[float]) -> str:
    """PASS / FAIL / MISSING for one value against a {'min': x} or {'max': x} rule."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "MISSING"
    if "min" in rule and value < rule["min"]:
        return "FAIL"
    if "max" in rule and value > rule["max"]:
        return "FAIL"
    return "PASS"


def bound_text(rule: Mapping[str, Any]) -> str:
    return f">= {rule['min']}" if "min" in rule else f"<= {rule['max']}"


# ----------------------------------------------------------------------- metrics
def _field(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def item_question_id(r: Any) -> str:
    """The question an item result is for: the dataset item id is the question id."""
    out = r.output if isinstance(r.output, Mapping) else {}
    item = r.item
    inp = _field(item, "input") or {}
    qid = _field(item, "id") or (inp.get("question_id") if isinstance(inp, Mapping) else None)
    return str(qid or out.get("question_id") or "unknown")


def item_correct(r: Any) -> bool:
    out = r.output if isinstance(r.output, Mapping) else {}
    return bool(answer_correct(input=None, output=out, expected_output=_field(r.item, "expected_output")).value)


def gate_metrics(item_results: list[Any], readback: Optional[Mapping[str, Any]],
                 must_pass: list[str]) -> dict[str, Optional[float]]:
    """Everything the thresholds can name, from the run's item results (the same evaluator
    functions Langfuse scored them with) and the read-back of what landed in Langfuse."""
    s = summarize(item_results)
    costs = [_field(r.output, "run_cost_usd") for r in item_results]
    # An unknown or absent cost on even one item cannot be averaged away.
    costs_known = bool(costs) and all(type(c) in (int, float) and math.isfinite(c) and c >= 0 for c in costs)
    wall = s.get("mean_wall_ms")
    m: dict[str, Optional[float]] = {
        "items_evaluated": float(len(item_results)),
        "items_in_langfuse": float(readback["scored_items"]) if readback and "scored_items" in readback else None,
        "accuracy": s.get("accuracy"),
        "errors": float(s.get("errors", 0)),
        "mean_run_cost_usd": s.get("mean_run_cost_usd") if costs_known else None,
        "mean_wall_s": wall / 1000.0 if wall is not None else None,
        "mean_db_time_share": s.get("mean_db_time_share"),
        "mean_scenarios_evaluated": s.get("mean_scenarios_evaluated"),
        "mean_llm_calls": s.get("mean_llm_calls"),
    }
    correct = {item_question_id(r): item_correct(r) for r in item_results}
    for qid in must_pass:
        m[f"{MUST_PASS_PREFIX}{qid}"] = (1.0 if correct[qid] else 0.0) if qid in correct else None
    return m


def evaluate(thresholds: Mapping[str, Any], metrics: Mapping[str, Optional[float]],
             expected_items: int) -> tuple[list[dict[str, Any]], bool]:
    """(rows, blocked). The gate's own hard rules come first: a run that did not evaluate every
    item, or whose evidence did not land in Langfuse, must not pass."""
    hard: dict[str, dict] = {
        "items_evaluated": {"min": expected_items},
        "items_in_langfuse": {"min": expected_items},
        **{f"{MUST_PASS_PREFIX}{q}": {"min": 1} for q in thresholds.get("must_pass", [])},
        **thresholds["hard"],
    }
    baseline = {k: v for k, v in (thresholds.get("baseline") or {}).items() if not k.startswith("_")}
    rows, blocked = [], False
    for kind, rules in (("hard", hard), ("soft", thresholds["soft"])):
        for name, rule in rules.items():
            rule = {k: v for k, v in rule.items() if not k.startswith("_")}
            value = metrics.get(name)
            status = check(rule, value)
            blocked |= kind == "hard" and status != "PASS"
            rows.append({"name": name, "value": value, "rule": rule, "kind": kind, "status": status,
                         "baseline": baseline.get(name)})
    return rows, blocked


# ----------------------------------------------------------------------- report
def fmt(name: str, v: Optional[float]) -> str:
    if v is None:
        return "—"
    if name.startswith(MUST_PASS_PREFIX):
        return "correct" if v >= 1 else "WRONG"
    if name == "accuracy":
        return f"{v:.0%}"
    if name == "mean_db_time_share":
        return f"{v:.1%}"
    if name == "mean_run_cost_usd":
        return f"${v:.3f}"
    if name == "mean_wall_s":
        return f"{v:.1f} s"
    if name in ("items_evaluated", "items_in_langfuse", "errors", "mean_scenarios_evaluated"):
        return f"{v:.0f}"
    return f"{v:.2f}"


def fmt_bound(name: str, rule: Mapping[str, Any]) -> str:
    if name.startswith(MUST_PASS_PREFIX):
        return "correct"
    op, x = ("≥", rule["min"]) if "min" in rule else ("≤", rule["max"])
    return f"{op} {fmt(name, float(x))}"


def per_question_rows(item_results: list[Any], scrub: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    rows = []
    for r in item_results:
        out = r.output if isinstance(r.output, Mapping) else {}
        err = out.get("error")
        rows.append({"question": item_question_id(r), "correct": item_correct(r),
                     "got": dict(out.get("answer_values") or {}),
                     "want": dict(_field(r.item, "expected_output") or {}),
                     "cost": out.get("run_cost_usd"), "scenarios": out.get("scenarios_evaluated"),
                     "stop": out.get("stop_reason"), "error": scrub_text(str(err), scrub) if err else None})
    return rows


def _answer(d: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in d.items()) or "—"


def render(rows: list[dict[str, Any]], blocked: bool, *, label: str, versions: Mapping[str, Any],
           model: str, cfg: Mapping[str, Any], dataset: str, run_name: str, run_url: Optional[str],
           n_items: int, n_dataset: int, questions: list[dict[str, Any]]) -> str:
    """Markdown verdict: stdout and the Actions job summary."""
    pv, av = versions.get(P.PLANNER, "?"), versions.get(P.ANALYST, "?")
    out = [f"## {'❌ **GATE FAILED**' if blocked else '✅ **GATE PASSED**'} — label `{label}` "
           f"(planner v{pv}, analyst v{av}) · {model}", "",
           f"Run `{run_name}` on `{dataset}`: {n_items} question(s), "
           f"{cfg['max_scenarios_per_round']} scenarios/round, {cfg['max_rounds']} rounds", ""]
    if n_items < n_dataset:
        out += [f"> **Smoke run:** {n_items} of {n_dataset} questions (`--limit`). The configured checks target "
                f"all {n_dataset}; this verdict is indicative only, never a promotion decision.", ""]
    if run_url:
        out += [f"[Open the run in Langfuse]({run_url})", ""]
    out += ["| Metric | Value | Bound | Baseline | Kind | |", "|---|---|---|---|---|---|"]
    for r in rows:
        icon = {"PASS": "✅", "FAIL": "❌" if r["kind"] == "hard" else "⚠️", "MISSING": "⚠️"}[r["status"]]
        if r["status"] == "MISSING" and r["kind"] == "hard":
            icon = "❌ missing"
        base = "—" if r["baseline"] is None else fmt(r["name"], float(r["baseline"]))
        kind = "hard" if r["kind"] == "hard" else "soft (warn)"
        out.append(f"| `{r['name']}` | {fmt(r['name'], r['value'])} | {fmt_bound(r['name'], r['rule'])} | "
                   f"{base} | {kind} | {icon} |")
    if questions:
        out += ["", "<details><summary>Per question</summary>", "",
                "| Question | | Answer | Expected | Cost | Scenarios | Stop |", "|---|---|---|---|---|---|---|"]
        for q in questions:
            cost = "—" if q["cost"] is None else f"${q['cost']:.3f}"
            scen = "—" if q["scenarios"] is None else str(q["scenarios"])
            stop = q["stop"] or "—"
            if q["error"]:
                stop += f" ({q['error'][:120]})"
            out.append(f"| `{q['question']}` | {'✅' if q['correct'] else '❌'} | {_answer(q['got'])} | "
                       f"{_answer(q['want'])} | {cost} | {scen} | {stop} |")
        out += ["", "</details>"]
    hard_fails = [r["name"] for r in rows if r["kind"] == "hard" and r["status"] != "PASS"]
    warns = [r["name"] for r in rows if r["kind"] == "soft" and r["status"] != "PASS"]
    out.append("")
    if hard_fails:
        out += ["**Blocked on:** " + ", ".join(f"`{n}`" for n in hard_fails), "",
                f"Label `{label}` must not be promoted to `production`."]
    else:
        out.append(f"All configured hard thresholds met for this pair (planner v{pv}, analyst v{av}).")
        if n_items < n_dataset:
            out.append("This subset is a smoke check, not promotion evidence. Run the full current dataset.")
        else:
            out.append("This is a dataset regression check; see cicd/README.md for baseline calibration limits.")
    if warns:
        out += ["", "Warnings (soft, not blocking): " + ", ".join(f"`{n}`" for n in warns)]
    return "\n".join(out)


def render_abort(reason: str, *, label: str) -> str:
    return "\n".join([f"## ⏹️ **GATE NOT EVALUATED** — label `{label}`", "", reason, "",
                      "> Nothing was evaluated, so this run makes **no claim** about quality. "
                      "It fails so that an unevaluated label can never reach `production`."])


def write_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as fh:
            fh.write(markdown + "\n\n")


def scrub_text(text: str, secrets: tuple[str, ...]) -> str:
    """Errors from httpx/ClickHouse can name the service host; the summary may be public."""
    for s in secrets:
        if s:
            text = text.replace(s, "<CH_HOST>")
    return text


def make_run_name(label: str, versions: Mapping[str, Any], template: Optional[str], stamp: str) -> str:
    """Always unique: reusing a run name appends to the old dataset run and mixes its scores in."""
    v = f"v{versions.get(P.PLANNER, '?')}/v{versions.get(P.ANALYST, '?')}"
    base = (template or "gate · {label} {version}").replace("{version}", v).replace("{label}", label)
    return f"{base} · {stamp}"


# ----------------------------------------------------------------------- pre-flight
def prompt_versions(lf: Any, label: str) -> dict[str, int]:
    """{prompt name: version} at ``label``; GateAbort when either prompt lacks it."""
    try:
        versions = {name: lf.get_prompt(name, label=label, cache_ttl_seconds=0).version for name in GATED_PROMPTS}
        # Resolve each label exactly once; all subsequent fetches use immutable IDs.
        for name in GATED_PROMPTS:
            P.get_prompt(lf, name, label=label, versions=versions)
        return versions
    except Exception as e:
        raise GateAbort(f"Could not pin label {label!r} on BOTH prompts ({type(e).__name__}: {e}). "
                        "Put the label on a version of BOTH prompts.") from None


def check_project(lf: Any, expected: str) -> str:
    if not lf.auth_check():
        raise GateAbort("Langfuse auth_check failed: check LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL.")
    projects = lf.api.projects.get().data
    name = projects[0].name if projects else None
    if name != expected:
        raise GateAbort(f"the Langfuse keys map to project {name!r}, not {expected!r}: refusing to gate "
                        "(prompts, dataset and evidence would be read from and written to the wrong project).")
    return name


async def _probe_clickhouse(settings: ClickHouseSettings) -> int:
    from explorer.ch_client import ClickHouseClient

    async with ClickHouseClient(settings, timeout_s=30.0) as ch:
        await ch.ping()  # also wakes an idle service before the timed sweeps
        res = await ch.query(f"SELECT count() AS n FROM {settings.qualified_table}")
        return int((res.first() or {}).get("n") or 0)


def check_clickhouse(settings: ClickHouseSettings) -> int:
    try:
        n = asyncio.run(_probe_clickhouse(settings))
    except Exception as e:  # noqa: BLE001
        msg = scrub_text(f"{type(e).__name__}: {e}", (settings.host,))[:400]
        raise GateAbort(f"ClickHouse is not reachable as `{settings.user}`: {msg}. On a GitHub-hosted runner "
                        "this usually means the service's IP access list does not admit the runner "
                        "(see cicd/README.md, ClickHouse access).") from None
    if n <= 0:
        raise GateAbort(f"`{settings.qualified_table}` is empty for `{settings.user}`: load the data first "
                        "(scripts/load_data.py).")
    return n


def load_items(lf: Any, dataset_name: str, limit: Optional[int]) -> tuple[list[Any], int]:
    """(active items in questions.yaml order, hero first, trimmed for the SDK; dataset size)."""
    try:
        dataset = lf.get_dataset(dataset_name)
    except Exception as e:  # noqa: BLE001
        raise GateAbort(f"dataset `{dataset_name}` not found ({type(e).__name__}): run scripts/seed_dataset.py.") from None
    items = [i for i in dataset.items if str(i.status) != "ARCHIVED"]
    order = {q.id: n for n, q in enumerate(load_questions())}
    items.sort(key=lambda i: (order.get(i.id, len(order)), i.id))
    n_dataset = len(items)
    if limit:
        items = items[:limit]
    if not items:
        raise GateAbort(f"dataset `{dataset_name}` has no active items: run scripts/seed_dataset.py.")
    return rx.experiment_items(items), n_dataset


# ----------------------------------------------------------------------- the gate
def run_gate(a: argparse.Namespace, t: Mapping[str, Any]) -> tuple[str, bool]:
    """Pre-flight, run, read back, evaluate. Returns (markdown, blocked); raises GateAbort."""
    load_env()
    base = RunConfig()
    cfg = replace(base, max_scenarios_per_round=t["config"]["max_scenarios_per_round"],
                  max_rounds=t["config"]["max_rounds"], prompt_label=a.prompt_label,
                  model=a.model or base.model)
    try:
        model_prices(cfg.model)
    except UnknownModelPriceError as e:
        raise GateAbort(str(e)) from None
    try:
        settings = ClickHouseSettings.from_env()
    except KeyError as e:
        raise GateAbort(f"{e.args[0]} is not set (the read-only agent user's connection).") from None
    if os.getenv("EXPLORER_TARGET") == "playground":
        raise GateAbort("EXPLORER_TARGET=playground: the public playground's quota cannot run a 300-scenario "
                        "sweep, and its timings are not ours. Gate against the Cloud service.")

    from explorer.tracing import get_langfuse

    lf = get_langfuse()
    check_project(lf, a.project_name)
    versions = prompt_versions(lf, a.prompt_label)
    cfg = replace(cfg, prompt_versions=versions)
    rows_in_table = check_clickhouse(settings)
    items, n_dataset = load_items(lf, t["dataset"], a.limit)

    key = f"gate-{a.prompt_label}"
    run_name = make_run_name(a.prompt_label, versions, a.run_name, rx.utc_stamp())
    description = (f"Prompt gate: label {a.prompt_label} (planner v{versions[P.PLANNER]}, analyst "
                   f"v{versions[P.ANALYST]}) at {cfg.max_scenarios_per_round} scenarios/round, "
                   f"max_rounds={cfg.max_rounds}, model={cfg.model}")
    print(f"gating label {a.prompt_label!r}: planner v{versions[P.PLANNER]}, analyst v{versions[P.ANALYST]}; "
          f"{len(items)}/{n_dataset} questions of {t['dataset']}; {rows_in_table:,} rows readable as {settings.user}; "
          f"model {cfg.model}\n--- {run_name}: {description}")

    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    task = rx.make_task(cfg=cfg, ch_settings=settings, lf=lf, variant_key=key, run_name=run_name,
                        item_timeout_s=a.item_timeout)
    result = lf.run_experiment(
        name=GATE_EXPERIMENT, run_name=run_name, description=description, data=items, task=task,
        evaluators=rx.ITEM_EVALUATORS, run_evaluators=rx.RUN_EVALUATORS, max_concurrency=a.concurrency,
        metadata={"variant": key, "ci_gate": "true", "prompt_label": a.prompt_label,
                  "planner_version": str(versions[P.PLANNER]), "analyst_version": str(versions[P.ANALYST]),
                  "max_scenarios_per_round": str(cfg.max_scenarios_per_round), "max_rounds": str(cfg.max_rounds),
                  "model": cfg.model, "limit": str(a.limit or ""),
                  "github_run_id": os.getenv("GITHUB_RUN_ID", "")},
    )
    lf.flush()
    item_results = list(result.item_results)
    for r in item_results:
        o = r.output if isinstance(r.output, Mapping) else {}
        print(f"  {'ok ' if item_correct(r) else 'BAD'} {item_question_id(r):<24} got={o.get('answer_values')} "
              f"want={_field(r.item, 'expected_output')} stop={o.get('stop_reason')} "
              f"scen={o.get('scenarios_evaluated')} cost={o.get('run_cost_usd')}"
              + (f" ERROR={scrub_text(str(o.get('error')), (settings.host,))[:160]}" if o.get("error") else ""))
    rb = rx.read_back(lf, experiment_id=result.dataset_run_id, expected_items={i.id for i in items},
                      since=started, wait_s=a.readback_wait)
    print(f"read back: {rb['scored_items']}/{len(items)} items scored in Langfuse"
          + (f" (last error: {rb['error']})" if rb.get("error") else "")
          + (f"; Langfuse accuracy {rb['langfuse_summary'].get('accuracy')}" if rb.get("langfuse_summary") else ""))

    must = list(t.get("must_pass", []))
    metrics = gate_metrics(item_results, rb, must)
    rows, blocked = evaluate(t, metrics, expected_items=len(items))
    md = render(rows, blocked, label=a.prompt_label, versions=versions, model=cfg.model,
                cfg={"max_scenarios_per_round": cfg.max_scenarios_per_round, "max_rounds": cfg.max_rounds},
                dataset=t["dataset"], run_name=run_name, run_url=result.dataset_run_url,
                n_items=len(items), n_dataset=n_dataset,
                questions=per_question_rows(item_results, (settings.host,)))
    return md, blocked


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt-label", default="production", help="label to gate (both prompts must carry it)")
    ap.add_argument("--model", default=None, help="override the agent model (pre-upgrade check)")
    ap.add_argument("--thresholds", default=str(THRESHOLDS_PATH))
    ap.add_argument("--run-name", default=None,
                    help="run-name template; {version} -> 'v<planner>/v<analyst>', {label} -> the label. "
                         "A UTC stamp is always appended, so a re-run never appends to an old run")
    ap.add_argument("--limit", type=int, default=None, help="first N questions only (smoke; never in CI)")
    ap.add_argument("--concurrency", type=int, default=3, help="questions in parallel (default 3)")
    ap.add_argument("--item-timeout", type=float, default=rx.DEFAULT_ITEM_TIMEOUT_S, help="seconds per question")
    ap.add_argument("--readback-wait", type=float, default=300.0, help="seconds to wait for Langfuse to show the run")
    ap.add_argument("--project-name", default=EXPECTED_PROJECT, help="project the Langfuse keys must map to")
    ap.add_argument("--warn-only", action="store_true", help="report a failed bar but exit 0")
    a = ap.parse_args(argv)
    if a.limit is not None and a.limit < 1:
        ap.error("--limit must be at least 1")

    t = load_thresholds(a.thresholds)
    try:
        md, blocked = run_gate(a, t)
    except GateAbort as e:
        md = render_abort(str(e), label=a.prompt_label)
        print("\n" + md)
        write_summary(md)
        print(f"\n✗ GATE NOT EVALUATED: {e}")
        return 1
    print("\n" + md + "\n")
    write_summary(md)
    if blocked:
        print(f"✗ GATE FAILED: label '{a.prompt_label}' must not be promoted to production.")
        return 0 if a.warn_only else 1
    print(f"✓ GATE PASSED: label '{a.prompt_label}' meets the bar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

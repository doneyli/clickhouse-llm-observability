#!/usr/bin/env bash
# Story arc 6 — cost regression after a "recall" release (TCO, GATE-05).
# Walkthrough: docs/arcs/cost-regression.md
#
#   assistant-1.5.1  baseline    k=3 + relevance threshold 0.08 (same retrieval code as 1.6.1)
#   assistant-1.6.0  regression  k=8, NO threshold — "more context for better recall"
#   assistant-1.6.1  fix         k=3 + threshold 0.08 (the current code defaults: reverts the change)
#
# Each release is reproduced per process with env vars (see the release knobs at
# the top of northwind/agent.py). Same conversations under every release (fixed
# --seed) and the SAME prompt version — label `baseline` (= v1) by default, so the
# only difference is the retrieval change. `production` moves during the workshop
# (Act 5.4 promotes a candidate); comparing releases across prompt versions would
# mix two changes. Override with NORTHWIND_ARC_PROMPT_LABEL=<label>.
#
#   scripts/run_cost_arc.sh                # traffic + experiments, then verify (~8 min)
#   scripts/run_cost_arc.sh traffic        # live traffic only
#   scripts/run_cost_arc.sh experiments    # golden-set experiments only (1.6.0 vs 1.6.1)
#   scripts/run_cost_arc.sh verify         # read the evidence back via the API; per-release numbers
#     --no-baseline   traffic: skip the 1.5.1 baseline
#     --rerun         experiments: if the run name exists, add a time suffix (default: skip — a
#                     re-used run name would append items to the old run)
#
# Re-runnable: traffic adds new traces; verify reads ONLY the traces of the latest
# traffic run (trace ids parsed from logs/cost-arc/traffic-<release>.log) and the
# latest experiment run per release. Wait ~3 min after traffic before `verify` so
# the managed faithfulness judge has scored the turns.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
LOG=logs/cost-arc
mkdir -p "$LOG"
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL  # config.py reads .env only

# Clean slate per process: no release knob inherited from the shell.
CLEAN=(env -u NORTHWIND_RELEASE -u NORTHWIND_RETRIEVAL_K -u NORTHWIND_RETRIEVAL_MIN_SCORE
       -u NORTHWIND_TX_DEFAULT_DAYS -u NORTHWIND_ENVIRONMENT -u NORTHWIND_SAMPLE_RATE -u NORTHWIND_PROMPT_LABEL)
REL_151=(NORTHWIND_RELEASE=assistant-1.5.1)
REL_160=(NORTHWIND_RELEASE=assistant-1.6.0 NORTHWIND_RETRIEVAL_K=8 NORTHWIND_RETRIEVAL_MIN_SCORE=0)
REL_161=(NORTHWIND_RELEASE=assistant-1.6.1)
DATASET=northwind-golden-qa-v1
PROMPT_LABEL=${NORTHWIND_ARC_PROMPT_LABEL:-baseline}

preflight() {  # the label must resolve: an unknown label silently serves the hard-coded fallback prompt
  "${CLEAN[@]}" $PY - "$PROMPT_LABEL" <<'PYEOF' || { echo "!! prompt label '$PROMPT_LABEL' does not resolve — aborting"; exit 1; }
import sys; sys.path.insert(0, ".")
from northwind import config
v = config.api("GET", "/api/public/v2/prompts/northwind-assistant-system", params={"label": sys.argv[1]})["version"]
print(f"== prompt label '{sys.argv[1]}' → v{v} (every release is compared on this version)")
PYEOF
}

traffic_release() {  # $1 = release name, rest = env assignments
  local rel=$1; shift
  local out="$LOG/traffic-$rel.log"
  echo "== traffic $rel → $out"
  {
    "${CLEAN[@]}" "$@" $PY scripts/generate_traffic.py --scenario core --n 10 --seed 31 --prompt-label "$PROMPT_LABEL"
    "${CLEAN[@]}" "$@" $PY scripts/generate_traffic.py --scenario es --n 4 --seed 32 --prompt-label "$PROMPT_LABEL"
  } > "$out" 2>&1
  echo "   $(grep -c '/traces/' "$out") turns · $(grep -ci 'error\|traceback' "$out") error lines"
}

traffic() {
  # Release order (baseline → regression → fix), so "Cost per turn over time" reads as a timeline.
  [[ " $* " == *" --no-baseline "* ]] || traffic_release assistant-1.5.1 "${REL_151[@]}"
  traffic_release assistant-1.6.0 "${REL_160[@]}"
  traffic_release assistant-1.6.1 "${REL_161[@]}"
}

exp_name_taken() {  # $1 = run-name template → exit 0 if an experiment with the resolved name exists
  "${CLEAN[@]}" $PY - "$1" "$DATASET" "$PROMPT_LABEL" <<'PYEOF'
import sys; sys.path.insert(0, ".")
from northwind import config
tpl, ds, label = sys.argv[1], sys.argv[2], sys.argv[3]
v = config.api("GET", "/api/public/v2/prompts/northwind-assistant-system", params={"label": label})["version"]
name = tpl.replace("{version}", f"v{v}")
dsid = config.api("GET", f"/api/public/v2/datasets/{ds}")["id"]
rows = config.api("GET", "/api/public/experiments", params={"datasetId": dsid, "name": name, "limit": 100,
                  "fromStartTime": "2026-01-01T00:00:00Z", "fields": "core"})["data"]
sys.exit(0 if any(r["name"] == name for r in rows) else 1)
PYEOF
}

experiment_release() {  # $1 = run-name template, rest = env assignments
  local tpl=$1; shift
  if exp_name_taken "$tpl"; then
    if [[ "$RERUN" == 1 ]]; then tpl="$tpl · $(date -u +%H%M)"; else
      echo "   = '$tpl' already exists on $DATASET — skipped (pass --rerun for a new run)"; return 0; fi
  fi
  "${CLEAN[@]}" "$@" $PY scripts/run_experiment.py --dataset "$DATASET" --prompt-label "$PROMPT_LABEL" --run-name "$tpl"
}

experiments() {
  echo "== experiments on $DATASET → $LOG/experiments.log"
  {
    experiment_release "release 1.6.0 · k8 · {version}" "${REL_160[@]}"
    experiment_release "release 1.6.1 · k3 · {version}" "${REL_161[@]}"
  } > "$LOG/experiments.log" 2>&1
  grep -E "already exists|This run:|avg-" "$LOG/experiments.log" | sed 's/^/   /'
}

verify() {
  "${CLEAN[@]}" $PY - "$LOG" "$DATASET" "$PROMPT_LABEL" <<'PYEOF'
"""Per-release evidence, read back from Langfuse (v2 observations + v3 scores + experiments)."""
import json, re, statistics as st, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, ".")
from northwind import config

LOG, DATASET, LABEL = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
RELEASES = ["assistant-1.5.1", "assistant-1.6.0", "assistant-1.6.1"]
LOW = 0.08  # the 1.6.1 relevance threshold: docs below it are what 1.6.0 adds
api = config.api
avg = lambda xs: round(st.mean(xs), 6) if xs else None  # noqa: E731


def pages(path, **p):
    out, cur = [], None
    while True:
        d = api("GET", path, params={**p, "limit": 100, **({"cursor": cur} if cur else {})})
        out += d.get("data") or []
        cur = (d.get("meta") or {}).get("cursor")
        if not cur or not d.get("data"):
            return out


def load(s):
    if isinstance(s, str):
        try:
            return json.loads(s)
        except ValueError:
            return s
    return s


def trace_facts(tid):
    obs = pages("/api/public/v2/observations", traceId=tid, fields="core,basic,usage,io")
    gens = sorted((o for o in obs if o.get("type") == "GENERATION"), key=lambda o: o.get("startTime") or "")
    root = next((o for o in obs if o.get("name") == "northwind-assistant" and o.get("type") == "AGENT"), {})
    rets = [load(o.get("output")) or {} for o in obs if o.get("name") == "kb-retrieval"]
    docs = [d for r in rets if isinstance(r, dict) for d in r.get("documents", [])]
    scores = {}
    for s in pages("/api/public/v3/scores", traceId=tid, fields="core,subject"):
        scores.setdefault(s["name"], s.get("value"))
    costs = [o.get("totalCost") for o in gens if o.get("totalCost") is not None]
    return {"trace_id": tid, "version": root.get("version"), "question": load(root.get("input")),
            "generations": len(gens), "gen_input_tokens": [o.get("inputUsage") or 0 for o in gens],
            "input_tokens": sum(o.get("inputUsage") or 0 for o in gens),
            "output_tokens": sum(o.get("outputUsage") or 0 for o in gens),
            "lf_cost": round(sum(costs), 6) if costs else None, "latency": root.get("latency"),
            "retrievals": len(rets), "docs": [(d.get("id"), d.get("score")) for d in docs],
            "scores": scores}


def summarize(rows):
    def sc(name):
        return [float(r["scores"][name]) for r in rows if isinstance(r["scores"].get(name), (int, float))]
    ret = [r for r in rows if r["retrievals"]]
    docs = [d for r in ret for d in r["docs"]]
    return {
        "turns": len(rows), "turns_with_retrieval": len(ret),
        "turn_cost_usd": avg(sc("turn-cost-usd")), "llm_calls": avg(sc("llm-calls")),
        "turn_latency_s": avg(sc("turn-latency-s")),
        "lf_cost_per_turn": avg([r["lf_cost"] for r in rows if r["lf_cost"] is not None]),
        "lf_cost_turns": sum(1 for r in rows if r["lf_cost"] is not None),
        "input_tokens_per_turn": avg([r["input_tokens"] for r in rows]),
        "input_tokens_per_generation": avg([t for r in rows for t in r["gen_input_tokens"]]),
        "retrieval_turn_cost_usd": avg([float(r["scores"]["turn-cost-usd"]) for r in ret
                                        if "turn-cost-usd" in r["scores"]]),
        "docs_per_retrieval": avg([len(r["docs"]) / r["retrievals"] for r in ret]),
        "docs_below_threshold_share": round(sum(1 for _, s in docs if (s or 0) < LOW) / len(docs), 3) if docs else None,
        "faithfulness": avg(sc("faithfulness")), "faithfulness_n": len(sc("faithfulness")),
        "banking_compliance": avg(sc("banking-compliance")), "banking_compliance_n": len(sc("banking-compliance")),
    }


v = api("GET", "/api/public/v2/prompts/northwind-assistant-system", params={"label": LABEL})["version"]
result = {"prompt": f"label {LABEL} → v{v}", "traffic": {}, "experiments": {}, "hero": {}, "excluded": {}}
facts = {}
for rel in RELEASES:
    f = LOG / f"traffic-{rel}.log"
    if not f.exists():
        continue
    ids = list(dict.fromkeys(re.findall(r"/traces/([0-9a-f]{32})", f.read_text())))
    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(trace_facts, ids))
    # Only turns served by this release AND this prompt version — a label moved mid-run must not mix in.
    facts[rel] = [r for r in rows if r["version"] == f"{rel} · prompt v{v}"]
    if len(facts[rel]) < len(rows):
        result["excluded"][rel] = sorted({str(r["version"]) for r in rows if r not in facts[rel]})
    result["traffic"][rel] = summarize(facts[rel])

# ── experiments (latest run per release prefix) ──
dsid = api("GET", f"/api/public/v2/datasets/{DATASET}")["id"]
for rel, prefix in [("assistant-1.6.0", f"release 1.6.0 · k8 · v{v}"), ("assistant-1.6.1", f"release 1.6.1 · k3 · v{v}")]:
    runs = [e for e in pages("/api/public/experiments", datasetId=dsid, name=prefix, fields="core,scores",
                             fromStartTime="2026-01-01T00:00:00Z") if e["name"].startswith(prefix)]
    if not runs:
        continue
    run = max(runs, key=lambda e: e.get("startTime") or "")
    items = pages("/api/public/experiment-items", experimentId=run["id"], fields="core,scores",
                  fromStartTime="2026-01-01T00:00:00Z")
    with ThreadPoolExecutor(8) as pool:
        tfs = list(pool.map(trace_facts, [it["traceId"] for it in items]))
    per = []
    for it, tf in zip(items, tfs):
        if tf["version"] != f"{rel} · prompt v{v}":
            result["excluded"].setdefault(run["name"], []).append(str(tf["version"]))
            continue
        tf["item_latency"] = None
        try:
            from datetime import datetime
            p = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
            tf["item_latency"] = (p(it["endTime"]) - p(it["startTime"])).total_seconds()
        except Exception:  # noqa: BLE001
            pass
        tf["item_scores"] = {s["name"]: s.get("value") for s in it.get("scores") or []}
        per.append(tf)
    q = lambda n: avg([float(x["item_scores"][n]) for x in per if isinstance(x["item_scores"].get(n), (int, float))])  # noqa: E731
    result["experiments"][rel] = {
        "run": run["name"], "url": f"{config.LANGFUSE_BASE_URL}/project/{config.project_id()}/datasets/{dsid}/runs/{run['id']}",
        "items": len(per), "correctness": q("correctness"), "must_include": q("must-include"),
        "source_recall": q("source-recall"), "cites_expected_source": q("cites-expected-source"),
        "no_unsolicited_upsell": q("no-unsolicited-upsell"),
        "lf_cost_per_item": avg([x["lf_cost"] for x in per if x["lf_cost"] is not None]),
        "input_tokens_per_item": avg([x["input_tokens"] for x in per]),
        "input_tokens_per_generation": avg([t for x in per for t in x["gen_input_tokens"]]),
        "llm_calls_per_item": avg([x["generations"] for x in per]),
        "latency_s_per_item": avg([x["item_latency"] for x in per if x["item_latency"] is not None]),
        "docs_per_retrieval": avg([len(x["docs"]) / x["retrievals"] for x in per if x["retrievals"]]),
        "run_scores": {s["name"]: s.get("value") for s in run.get("scores") or []},
    }

# ── hero pair: a 1.6.0 turn whose retriever returned 8 docs, most below the threshold, + the same question on 1.6.1 ──
if facts.get("assistant-1.6.0") and facts.get("assistant-1.6.1"):
    def low(r):
        return sum(1 for _, s in r["docs"] if (s or 0) < LOW)
    cands = [r for r in facts["assistant-1.6.0"] if r["retrievals"] == 1 and len(r["docs"]) == 8]
    cands.sort(key=lambda r: (("wire" not in str(r["question"]).lower()), -low(r)))
    for h in cands:
        twin = next((r for r in facts["assistant-1.6.1"] if r["question"] == h["question"] and r["retrievals"]), None)
        if twin:
            result["hero"] = {rel: {"url": config.trace_url(r["trace_id"]), "question": r["question"],
                                    "docs": r["docs"], "input_tokens": r["input_tokens"],
                                    "gen_input_tokens": r["gen_input_tokens"], "lf_cost": r["lf_cost"],
                                    "turn_cost_usd": r["scores"].get("turn-cost-usd"),
                                    "latency": r["latency"], "faithfulness": r["scores"].get("faithfulness")}
                              for rel, r in (("assistant-1.6.0", h), ("assistant-1.6.1", twin))}
            break

(LOG / "verify.json").write_text(json.dumps(result, indent=1, default=str))

# ── print ──
T = result["traffic"]
rels = [r for r in RELEASES if r in T]
keys = ["turns", "turns_with_retrieval", "turn_cost_usd", "lf_cost_per_turn", "llm_calls", "turn_latency_s",
        "input_tokens_per_turn", "input_tokens_per_generation", "retrieval_turn_cost_usd", "docs_per_retrieval",
        "docs_below_threshold_share", "faithfulness", "faithfulness_n", "banking_compliance", "banking_compliance_n"]
print(f"\nLIVE TRAFFIC (same conversations per release, prompt {result['prompt']})")
if result["excluded"]:
    print(f"  excluded (served another release/prompt version): {result['excluded']}")
print(f"{'':<30}" + "".join(f"{r:>18}" for r in rels))
for k in keys:
    print(f"{k:<30}" + "".join(f"{str(T[r].get(k)):>18}" for r in rels))
E = result["experiments"]
if E:
    print(f"\nEXPERIMENTS on {DATASET}")
    ek = ["items", "correctness", "must_include", "source_recall", "cites_expected_source", "lf_cost_per_item",
          "input_tokens_per_item", "input_tokens_per_generation", "llm_calls_per_item", "latency_s_per_item",
          "docs_per_retrieval"]
    print(f"{'':<30}" + "".join(f"{r:>18}" for r in E))
    for k in ek:
        print(f"{k:<30}" + "".join(f"{str(E[r].get(k)):>18}" for r in E))
    for r in E:
        print(f"  {E[r]['run']}: {E[r]['url']}")


def delta(a, b):
    return f"{(b - a) / a * 100:+.0f}%" if a and b is not None else "n/a"


if "assistant-1.6.0" in T and "assistant-1.6.1" in T:
    reg, fix = T["assistant-1.6.0"], T["assistant-1.6.1"]
    base = T.get("assistant-1.5.1") or fix
    print("\nREGRESSION vs FIX (live traffic)")
    for k in ["turn_cost_usd", "lf_cost_per_turn", "llm_calls", "turn_latency_s", "input_tokens_per_turn",
              "input_tokens_per_generation"]:
        print(f"  {k:<28} 1.6.0 vs 1.6.1 {delta(fix[k], reg[k]):>6}   1.6.0 vs baseline {delta(base[k], reg[k]):>6}")
    for k in ["turn_cost_usd", "lf_cost_per_turn"]:
        if reg[k] is not None and fix[k] is not None:
            d = reg[k] - fix[k]
            print(f"  at 1,000,000 turns/month ({k}): ({reg[k]:.6f} - {fix[k]:.6f}) x 1,000,000 = USD {d * 1e6:,.0f} per month")
H = result["hero"]
if H:
    print("\nHERO PAIR")
    for rel, h in H.items():
        print(f"  {rel}: {h['url']}\n    question={h['question']!r}\n    docs={h['docs']}\n"
              f"    input tokens per generation (in call order)={h['gen_input_tokens']} turn-cost-usd={h['turn_cost_usd']} "
              f"langfuse cost={h['lf_cost']} latency={h['latency']} faithfulness={h['faithfulness']}")
print(f"\nDashboard: Dashboards → 'Northwind — Business value & failure modes'  ·  raw numbers: {LOG / 'verify.json'}")
PYEOF
}

main() {
  RERUN=0
  for a in "$@"; do [[ "$a" == "--rerun" ]] && RERUN=1; done
  case "${1:-all}" in
    traffic) preflight; traffic "$@" ;;
    experiments) preflight; experiments ;;
    verify) verify ;;
    all|--*)
      preflight
      experiments & E=$!     # experiments run on the golden set while live traffic flows
      traffic "$@"
      wait $E
      echo "== waiting 180 s for the managed faithfulness judge"; sleep 180
      verify ;;
    *) awk 'NR>1 && /^set -uo/{exit} NR>1' "$0"; return 2 ;;
  esac
}

main "$@"; exit $?

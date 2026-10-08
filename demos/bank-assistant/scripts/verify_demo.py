"""End-to-end readiness check for the workshop: every artifact the demo script relies on.

Checks the Langfuse project through the public API (prompts, datasets, runs,
evaluators, rules, recent judge executions, score configs, annotation queue,
dashboard, traces per channel) plus the local services (MCP server, portal,
APM stand-in, n8n). Exit 1 if any REQUIRED check fails.

It also checks demo HYGIENE — the state a presenter inherits from earlier rehearsals:
prompt labels (rollback target, untitled versions, text drift vs northwind/prompts.py),
SME-queue labelling, raw PII in exported production observations, stray ERROR
observations, judge coverage, dirty in-memory MCP banking state, project retention.

Run: .venv/bin/python scripts/verify_demo.py [--hours 24] [--skip-pii-scan]
     (the PII scan reads every production observation in the window — ~30 s per 8k observations)
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from northwind import config, prompts  # noqa: E402
import prompt_label  # noqa: E402  — one definition of "untitled version" for the CLI and this check

FAIL, WARN = [], []


def check(ok: bool, name: str, detail: str = "", required: bool = True):
    tag = "PASS" if ok else ("FAIL" if required else "WARN")
    print(f"[{tag}] {name}{' — ' + detail if detail else ''}", flush=True)
    if not ok:
        (FAIL if required else WARN).append(name)


def get(path, _timeout=30, **params):
    return config.api("GET", path, params=params or None, timeout=_timeout)


def cursor_all(path, limit=100, **params):
    """Follow meta.cursor to the end (scores: limit <= 100; v2 observations: limit <= 1000)."""
    out, cur = [], None
    while True:
        d = get(path, _timeout=120 if limit > 100 else 30, limit=limit, **params,
                **({"cursor": cur} if cur else {}))
        out += d.get("data") or []
        meta = d.get("meta") or {}
        cur = meta.get("cursor") or meta.get("nextCursor")
        if not cur or not d.get("data"):
            return out


# The experiments API requires a start-time lower bound; this one predates the demo.
EXPERIMENTS_SINCE = "2026-01-01T00:00:00Z"


def experiment_names(dataset_id: str) -> list:
    """Experiment (dataset run) names on a dataset — v4 experiments API.

    Replaces GET /datasets/{name}/runs, which Langfuse Cloud removes on 2026-11-16
    and a v4 self-hosted server (the `selfhosted` profile) already answers with 404.
    """
    return [e["name"] for e in cursor_all("/api/public/experiments", datasetId=dataset_id,
                                          fromStartTime=EXPERIMENTS_SINCE, fields="core")]


def page_all(path, limit=100, **params):
    """Follow page/totalPages (annotation-queue items)."""
    out, page = [], 1
    while True:
        d = get(path, limit=limit, page=page, **params)
        out += d.get("data") or []
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return out
        page += 1


def obs_all(**params):
    return cursor_all("/api/public/v2/observations", limit=1000, **params)


# ── Pure helpers for the hygiene checks (no network — unit-tested in tests/test_scripts_verify_demo.py) ──

# Synthetic customer identifiers the demo seeds into prompts and attacks. If one shows up
# un-masked in an exported observation, masking is not working.
PII_NEEDLES = ["4111 1111 1111 1111", "4111111111111111", "4111-1111-1111-1111", "1020304050", "0012345678",
               "998877", "ana.torres@example.com", "ben.okafor@example.com", "maria.g@example.com"]
JUDGE_ENV = "langfuse-llm-as-a-judge"  # judge traces copy the customer message verbatim
SMOKE_SESSION = "smoke-1"  # the known smoke-test session; its ERROR spans are expected
JUDGE_GRACE = timedelta(minutes=10)  # a turn younger than this may simply not be judged yet


def _needle_re(needle: str):
    # Digit needles must not be a slice of a longer number / hex id (998877 inside a trace id).
    if needle[0].isdigit():
        return re.compile(r"(?<![0-9A-Za-z])" + re.escape(needle) + r"(?![0-9A-Za-z])")
    return re.compile(re.escape(needle), re.I)


def find_pii(observations, needles=PII_NEEDLES) -> dict:
    """{needle: [distinct trace ids, in order seen]} for observations whose input / output /
    metadata contain a needle. Serialised with ensure_ascii False AND True so a needle is found
    whether or not the exporter escaped non-ASCII text around it."""
    rx = [(n, _needle_re(n)) for n in needles]
    hits = {}
    for o in observations:
        payload = {"input": o.get("input"), "output": o.get("output"), "metadata": o.get("metadata")}
        hay = (json.dumps(payload, ensure_ascii=False, default=str) + "\n"
               + json.dumps(payload, ensure_ascii=True, default=str))
        for needle, r in rx:
            if r.search(hay):
                ids = hits.setdefault(needle, [])
                if o.get("traceId") not in ids:
                    ids.append(o.get("traceId"))
    return hits


def fmt_pii(hits: dict, per_needle: int = 5) -> str:
    return "; ".join(f"{n} → {', '.join(ids[:per_needle])}" + (f" (+{len(ids) - per_needle} more)"
                                                              if len(ids) > per_needle else "")
                     for n, ids in hits.items())


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def unscored_roots(observations, scored_ids: set, now: datetime, grace=JUDGE_GRACE):
    """(eligible, unscored): root `northwind-assistant` AGENT turns in production that ended more
    than `grace` ago, and those among them without a score on that observation."""
    eligible = [o for o in observations
                if o.get("name") == "northwind-assistant" and o.get("type") == "AGENT"
                and o.get("environment") == "production" and o.get("endTime")
                and parse_ts(o["endTime"]) < now - grace]
    return eligible, [o for o in eligible if o["id"] not in scored_ids]


def _first_diff(a: str, b: str) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def prompt_text_drift(pv: dict, labels: dict) -> list:
    """Labelled prompt versions whose text differs from the repo (northwind/prompts.py).
    pv = {version: API payload with "prompt"}, labels = {label: version}. v4 is the candidate
    whatever labels it carries (production / previous-production move around it)."""
    expect = [("baseline", labels.get("baseline"), "V1_BASELINE", prompts.V1_BASELINE),
              ("development", labels.get("development"), "V3_REGRESSION", prompts.V3_REGRESSION),
              ("candidate", 4 if 4 in pv else None, "V2_CANDIDATE", prompts.V2_CANDIDATE),
              ("staging", labels.get("staging"), "V5_RELEASE", prompts.V5_RELEASE)]
    out = []
    for name, v, const, repo in expect:
        if v is None:
            continue
        live = str(pv[v].get("prompt") or "").strip()
        if live != repo.strip():
            out.append(f"{name} (v{v}) ≠ prompts.{const}: {len(live)} vs {len(repo.strip())} chars, "
                       f"first difference at char {_first_diff(live, repo.strip())}, live starts {live[:24]!r}")
    return out


def mcp_process_start(port: int):
    """(pid, start time in UTC) of whatever listens on `port`, or None. Never calls the MCP server
    itself — its tools emit spans and change banking state."""
    try:
        pids = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True,
                              timeout=10).stdout.split()
        if not pids:
            return None
        lstart = subprocess.run(["ps", "-o", "lstart=", "-p", pids[0]], capture_output=True, text=True,
                                timeout=10).stdout.strip()
        local = datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y")  # ps prints local time
        return pids[0], local.astimezone().astimezone(timezone.utc)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--skip-pii-scan", action="store_true",
                    help="skip the raw-PII scan of production observations (the slow part, ~30 s per 8k observations)")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=a.hours)).isoformat()
    print(f"Langfuse {config.LANGFUSE_BASE_URL} · project {config.project_id()} · window {a.hours}h\n")

    # ── prompts ──
    labels = {}
    pv = {}  # version → full payload (labels, commitMessage, prompt text) for the hygiene checks below
    meta = get("/api/public/v2/prompts", name="northwind-assistant-system")["data"]
    for v in (meta[0]["versions"] if meta else []):
        pv[v] = get(f"/api/public/v2/prompts/northwind-assistant-system", version=v)
        for l in pv[v].get("labels", []):
            labels[l] = v
    check("production" in labels, f"prompt production → v{labels.get('production')}", f"labels={labels}")
    check(labels.get("baseline") == 1, "prompt baseline → v1 (the version with the four business issues)",
          required=False)
    check(labels.get("staging") == 5, "prompt staging → v5 (release candidate)", required=False)
    check("staging" in labels and "development" in labels, "prompt staging + development labels exist")

    # Rollback needs a DIFFERENT version under previous-production (Act 5.4: --rollback is otherwise a no-op).
    prod, prev = labels.get("production"), labels.get("previous-production")
    if prev is None:
        check(False, "prompt previous-production label set",
              "no version carries it — --rollback has nothing to go back to; run scripts/prompt_label.py "
              "--set-previous N", required=False)
    else:
        check(prod != prev, "prompt production ≠ previous-production",
              f"production=v{prod} · previous-production=v{prev}" +
              ("" if prod != prev else " — same version, so --rollback is a no-op; run scripts/prompt_label.py "
                                      "--set-previous N"))
    # Untitled + unlabelled versions are stray "new version" clicks in the UI: delete them.
    orphans = [v for v, pr in sorted(pv.items()) if prompt_label.is_untitled(pr.get("labels", []),
                                                                           pr.get("commitMessage"))]
    for v in orphans:
        check(False, f"orphan/untitled version v{v}", f"no commit message, labels={pv[v].get('labels') or '-'} "
              "— delete it in the UI", required=False)
    if not orphans:
        check(True, "no orphan/untitled prompt versions", f"{len(pv)} versions")
    # The label→text contract with the repo: what is stored must be what the demo script describes.
    drift = prompt_text_drift(pv, labels)
    check(not drift, "prompt texts match northwind/prompts.py",
          "; ".join(drift) if drift else "baseline, development, candidate (v4), staging")

    # ── datasets + runs ──
    expect = {"northwind-golden-qa-v1": 16, "northwind-golden-qa-es-v1": 10, "northwind-redteam-v1": 10,
              "judge-calibration/faithfulness": 15}
    for ds, n in expect.items():
        q = quote(ds, safe="")
        try:
            d = get(f"/api/public/v2/datasets/{q}")
            items = get("/api/public/dataset-items", datasetName=ds, limit=50)["data"]
            check(len(items) >= n, f"dataset {ds}", f"{len(items)} items")
            runs = experiment_names(d["id"])
            check(bool(runs), f"  runs on {ds}", ", ".join(sorted(set(runs)))[:300])
        except RuntimeError as e:
            check(False, f"dataset {ds}", str(e)[:120])
    golden_runs = experiment_names(get("/api/public/v2/datasets/northwind-golden-qa-v1")["id"])
    for want in ["production · claude-sonnet-4-6", "staging · claude-sonnet-4-6", "production · gpt-4.1"]:
        check(want in golden_runs, f"  golden run '{want}'")

    # ── evaluators + rules ──
    rules = cursor_all("/api/public/v2/evaluation-rules")
    by = {r["name"]: r for r in rules}
    for name in ["faithfulness", "banking-compliance", "manipulation-resistance"]:
        r = by.get(name)
        check(bool(r) and r.get("enabled") is True, f"rule {name} enabled",
              f"sampling={r.get('sampling') if r else None}")
    check(len(get("/api/public/score-configs", limit=50)["data"]) >= 4, "score configs registered")

    # ── recent scores: are judges executing? ──
    names = {}
    faithfulness_obs = set()  # observation ids that carry a faithfulness score (judge coverage, below)
    for s in cursor_all("/api/public/v3/scores", fromTimestamp=since, fields="core,subject"):
        names.setdefault(s["name"], [0, None])
        names[s["name"]][0] += 1
        names[s["name"]][1] = max(names[s["name"]][1] or "", s.get("timestamp") or "")
        subject = s.get("subject") or {}
        if s["name"] == "faithfulness" and subject.get("kind") == "observation":
            faithfulness_obs.add(subject.get("id"))
    for n in ["faithfulness", "banking-compliance", "manipulation-resistance"]:
        check(n in names, f"judge scores: {n}", f"n={names.get(n, [0])[0]} latest={names.get(n, [0, None])[1]}")
    for n in ["security-risk", "guardrail-blocked", "pii-in-input", "language-match", "formal-register",
              "user-feedback", "cites-sources", "task-outcome", "contained", "value-usd", "failure-mode",
              "unsolicited-upsell", "advisor-offered", "pii-education"]:
        check(n in names, f"app scores: {n}", f"n={names.get(n, [0])[0]}")

    # ── annotation queue ──
    qs = get("/api/public/annotation-queues", limit=50)["data"]
    q = next((x for x in qs if x["name"].startswith("SME review")), None)
    q_items = page_all(f"/api/public/annotation-queues/{q['id']}/items") if q else []
    n_items = len(q_items)
    check(n_items >= 10, "SME annotation queue", f"{n_items} items")
    n_done = sum(1 for i in q_items if i.get("status") == "COMPLETED")
    check(n_done >= 8, "SME queue labelled (human labels for the judge-calibration act)",
          f"{n_done} completed / {n_items} total" + ("" if n_done >= 8 else " — label 8–10 before Act 2.4"),
          required=False)

    # ── dashboard ──
    try:
        dashes = get("/api/public/unstable/dashboards", limit=50).get("data", [])
        check(any(d.get("name", "").startswith("Northwind — AI quality") for d in dashes), "quality dashboard")
        check(any(d.get("name", "").startswith("Northwind — Business value") for d in dashes), "business value dashboard")
    except RuntimeError as e:
        check(False, "Northwind dashboard", str(e)[:100], required=False)

    # ── project settings ──
    days = get("/api/public/projects")["data"][0].get("retentionDays")
    check(days == 90, "project retention is 90 days", f"retentionDays={days}", required=False)

    # ── traces per channel ──
    # One root observation per trace. traceName is not a query parameter of
    # /v2/observations — passed as one it is silently ignored and the check passes
    # on ANY observation in the window — so it goes in the `filter` JSON.
    for trace_name in ["northwind-assistant", "northwind-voice-call", "n8n-complaint-triage"]:
        roots = json.dumps([
            {"type": "string", "column": "traceName", "operator": "=", "value": trace_name},
            {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
            {"type": "datetime", "column": "startTime", "operator": ">=", "value": since}])
        obs = get("/api/public/v2/observations", filter=roots, limit=50, fields="core")["data"]
        check(len(obs) > 0, f"traces '{trace_name}' in window", f"{len(obs)}{'+' if len(obs) == 50 else ''} traces")

    # ── v4 trace shape: one root per trace, the session id on every observation ──
    # v4 filters and aggregates per observation. An MCP server span that misses the
    # caller's baggage becomes a SECOND root with no session (config.inject_trace_context).
    # A warning, not a failure: traces recorded before that fix stay in the window.
    latest = json.dumps([
        {"type": "string", "column": "traceName", "operator": "=", "value": "northwind-assistant"},
        {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
        {"type": "datetime", "column": "startTime", "operator": ">=", "value": since}])
    roots = get("/api/public/v2/observations", filter=latest, limit=10, fields="core,basic")["data"]
    extra_roots, no_session = 0, 0
    for r in roots:
        obs = get("/api/public/v2/observations", traceId=r["traceId"], limit=100, fields="core,basic")["data"]
        extra_roots += sum(1 for o in obs if o.get("isRootObservation")) > 1
        no_session += bool(r.get("sessionId")) and any(o.get("sessionId") != r["sessionId"] for o in obs)
    check(bool(roots) and not extra_roots, "v4 trace shape: one root per assistant trace",
          f"{extra_roots}/{len(roots)} latest traces have more than one root", required=False)
    check(bool(roots) and not no_session, "v4 trace shape: session id on every observation",
          f"{no_session}/{len(roots)} latest traces have observations outside the session", required=False)

    # ── trace hygiene: what the audience would see in Langfuse ──
    # Errors. The smoke-test session is expected; anything else in production is a stray failure.
    errors = obs_all(level="ERROR", fromStartTime=since, fields="core,basic")
    prod_err = [o for o in errors if o.get("environment") == "production"]
    stray = [o for o in prod_err if o.get("sessionId") != SMOKE_SESSION]
    check(not stray, "no unexplained production ERROR observations" if not stray
          else "unexplained production ERROR observations",
          (f"{len(stray)} (+{len(prod_err) - len(stray)} in {SMOKE_SESSION} ignored): " +
           "; ".join(f"{o['traceId']} {o['name']}" for o in stray[:8]) + (" …" if len(stray) > 8 else ""))
          if stray else f"{len(prod_err)} in {SMOKE_SESSION} ignored", required=False)
    other_err = Counter((o.get("environment"), o.get("name")) for o in errors if o.get("environment") != "production")
    check(not other_err, "no ERROR observations in other environments" if not other_err
          else "ERROR observations in other environments",
          "; ".join(f"{env}/{name} ×{n}" for (env, name), n in other_err.most_common(8)) +
          (" …" if len(other_err) > 8 else ""), required=False)

    # Judge coverage: every settled production turn should carry a faithfulness score.
    roots = obs_all(name="northwind-assistant", environment="production", fromStartTime=since, fields="core,basic")
    eligible, missing = unscored_roots(roots, faithfulness_obs, now)
    oldest = min(missing, key=lambda o: o["startTime"], default=None)
    newest = max(missing, key=lambda o: o["startTime"], default=None)
    # The releases tell old turns (before the judge rule existed) from a judge that has stalled since.
    releases = ", ".join(f"{v} ×{n}" for v, n in Counter(o.get("version") for o in missing).most_common(3))
    check(not missing, "judge coverage (faithfulness on production turns)",
          f"{len(missing)} of {len(eligible)} unscored; oldest {oldest['startTime']}, newest {newest['startTime']}; "
          f"releases {releases}" if missing else f"all {len(eligible)} settled turns scored", required=False)

    # Raw PII: masking must have replaced the synthetic identifiers before export.
    if a.skip_pii_scan:
        print("[SKIP] raw PII in exported observations — --skip-pii-scan")
    else:
        print("scanning production observations for raw PII (slow: reads every payload)…", flush=True)
        prod_obs = obs_all(environment="production", fromStartTime=since, fields="core,basic,io,metadata")
        hits = find_pii(prod_obs)
        check(not hits, "raw PII in exported observations",
              fmt_pii(hits) if hits else f"none in {len(prod_obs)} production observations")
        judge_hits = find_pii(obs_all(environment=JUDGE_ENV, fromStartTime=since, fields="core,basic,io,metadata"))
        check(not judge_hits, f"raw PII in judge traces ({JUDGE_ENV})",
              (fmt_pii(judge_hits, 3) + " — judge traces copy the customer message") if judge_hits
              else "none", required=False)

    # ── local services ──
    for name, url, req in [("portal", "http://localhost:8090/", True), ("MCP server", "http://localhost:8765/mcp", True),
                           ("APM stand-in (Jaeger)", "http://localhost:16686/", True), ("n8n", "http://localhost:5678/", True)]:
        try:
            code = httpx.get(url, timeout=5).status_code
            check(code < 500 or name == "MCP server", f"{name} up", f"HTTP {code}", required=req)
        except Exception as e:  # noqa: BLE001
            check(False, f"{name} up", type(e).__name__, required=req)

    # ── MCP banking state is in memory: blocks and disputes from rehearsals persist until restart ──
    mcp_port = urlparse(config.MCP_URL).port or 8765
    started = mcp_process_start(mcp_port)
    if started is None:
        check(False, "MCP in-memory state", f"no listener on :{mcp_port} to read a start time from",
              required=False)
    else:
        pid, t0 = started
        mutating = Counter()
        for tool in ("block_card", "open_dispute"):
            mutating[tool] = len(obs_all(name=f"mcp-server: {tool}", fromStartTime=t0.isoformat(),
                                         fields="core"))
        n = sum(mutating.values())
        local_t = t0.astimezone().strftime("%Y-%m-%d %H:%M")
        check(n == 0, "MCP in-memory state is clean" if n == 0 else "MCP in-memory state is dirty",
              f"{mutating['block_card']} card blocks / {mutating['open_dispute']} disputes since the MCP server "
              f"started at {local_t} local (pid {pid}) — restart it for a clean banking state" if n
              else f"no card blocks / disputes since the MCP server started at {local_t} local (pid {pid})",
              required=False)

    print(f"\n{len(FAIL)} required failure(s), {len(WARN)} warning(s)")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        sys.exit(1)
    print("READY")


if __name__ == "__main__":
    main()

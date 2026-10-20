"""Seed the decision-model evaluators ("Jev as a judge") — idempotent, reconciling.

Creates, in the Langfuse project behind .env/.env.cloud, the two evaluators
defined in northwind/decision_evals.py plus one rule each:

  customer-signals   7 questions · every production turn (chat + voice)
  conduct-screen     6 questions · every answered production turn

Decision-model evaluators exist only in the Langfuse UI and the Langfuse MCP
server (Cloud ≥ 4.51): the public REST API rejects the type, so this script
speaks MCP (POST /api/public/mcp, project keys as Basic auth).

The model needs an LLM connection the API cannot create — add it once in
Settings → LLM Connections:
  --provider typesafe (default): TypeSafe Jev. Adapter "TypeSafe (System One)",
      upstream TypeSafe, OpenRouter or Vercel AI Gateway, or Custom for a
      self-served System One model such as Strands Decider.
  --provider openai: OpenAI's decision model gpt-6-luna, on the OpenAI
      connection seed_evals.py already uses for audio. Same questions.

Run:
  .venv/bin/python scripts/seed_decision_evals.py --dry-run --test 5   # preview on 5 live turns, writes nothing
  .venv/bin/python scripts/seed_decision_evals.py                      # create / reconcile evaluators + rules
  .venv/bin/python scripts/seed_decision_evals.py --provider openai    # same, on gpt-6-luna
"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, decision_evals as de  # noqa: E402

SETUP = """No LLM connection named "{connection}" in this project.
Add it in Langfuse → Settings → LLM Connections, then re-run:
  adapter "TypeSafe (System One)", provider name "{connection}", upstream TypeSafe /
  OpenRouter / Vercel AI Gateway (paste that upstream's API key), or Custom with the
  base URL of a self-served System One model. See docs/DECISION_MODELS.md.
Or run the same evaluators today on OpenAI's decision model: --provider openai"""


class LangfuseMCP:
    """The project-scoped Langfuse MCP server — one session per script run."""

    def __init__(self, session):
        self.session = session

    async def call(self, tool: str, args: dict):
        res = await self.session.call_tool(tool, args)
        text = "".join(getattr(c, "text", "") for c in res.content)
        data = json.loads(text) if text[:1] in "{[" else text
        if res.isError or (isinstance(data, dict) and data.get("success") is False):
            raise RuntimeError(f"{tool}: {data.get('error') if isinstance(data, dict) else data}")
        return data

    async def all(self, tool: str) -> list:
        out, page = [], 1
        while True:
            d = await self.call(tool, {"page": page, "limit": 100})
            batch = d.get("data") or []
            out += batch
            if len(batch) < 100:
                return out
            page += 1


def _canon_questions(questions) -> list:
    keys = ("id", "type", "scoreName", "instructions", "criteria", "options", "levels")
    return [{k: q.get(k) for k in keys if q.get(k) is not None} for q in questions or []]


def _canon_mapping(mapping) -> list:
    return sorted((m.get("templateVariable"), m.get("selectedColumnId"), m.get("jsonSelector") or None)
                  for m in mapping or [])


def drift(version: dict, want: dict) -> list:
    """What differs between the stored latest version and the definition we want."""
    diffs = []
    if (version.get("provider"), version.get("model")) != (want["modelConfig"]["provider"],
                                                            want["modelConfig"]["model"]):
        diffs.append(f"model {version.get('provider')}/{version.get('model')} → "
                     f"{want['modelConfig']['provider']}/{want['modelConfig']['model']}")
    if _canon_questions(version.get("questions")) != _canon_questions(want["questions"]):
        diffs.append("questions")
    if _canon_mapping(version.get("variableMapping")) != _canon_mapping(want["variableMapping"]):
        diffs.append("state mapping")
    return diffs


def latest_version(evaluator: dict) -> dict:
    return max(evaluator.get("versions") or [{}], key=lambda v: v.get("version") or 0)


def sample_turns(n: int) -> list:
    """Production assistant turns (chat and voice) — the observations the rules select."""
    d = config.api("GET", "/api/public/v2/observations", params={
        "name": "northwind-assistant", "environment": "production", "limit": n, "fields": "core,basic"})
    return d.get("data") or []


async def preview(mcp: LangfuseMCP, evaluator: dict, body: dict, turns: list, evaluator_id: str | None):
    """Run the evaluator on live turns through testEvaluator. Writes no scores."""
    print(f"\n{evaluator['name']} on {len(turns)} live turns (testEvaluator: no scores written)")
    names = [q["scoreName"] for q in evaluator["questions"]]
    for t in turns:
        args = {"observationId": t["id"], "traceId": t["traceId"], "startTime": t["startTime"]}
        args.update({"evaluatorId": evaluator_id} if evaluator_id else body)
        r = await mcp.call("testEvaluator", args)
        got = {s["name"]: s["value"] for s in r.get("scores") or []}
        flags = [f"{n}={got[n]}" for n in names
                 if isinstance(got.get(n), str) or (isinstance(got.get(n), (int, float))
                                                    and de.band(de.questions_by_score()[n], got[n]) != "ignore")]
        print(f"  {t['id'][:12]}  ${r.get('estimatedCostUsd', 0):.6f}  {r.get('durationMs')} ms  "
              f"{', '.join(flags) or 'no flags'}")


async def seed(a, mcp: LangfuseMCP):
    for e in de.EVALUATORS:
        issues = de.problems(e)
        if issues:
            raise SystemExit(f"{e['name']}: {'; '.join(issues)}")
    turns = sample_turns(max(a.test, 1))
    if not turns:
        raise SystemExit("No production assistant turns to test against. Run scripts/generate_traffic.py first.")

    # Preflight: one draft run proves the connection AND the model, before anything is written.
    first = de.definition(de.EVALUATORS[0], a.provider, a.connection, a.model)
    try:
        await mcp.call("testEvaluator", {**first, "observationId": turns[0]["id"],
                                         "traceId": turns[0]["traceId"], "startTime": turns[0]["startTime"]})
    except RuntimeError as exc:
        if "not found" in str(exc):
            raise SystemExit(SETUP.format(connection=a.connection or a.provider)) from None
        raise
    print(f"✓ decision model reachable: {first['modelConfig']['provider']}/{first['modelConfig']['model']}")

    existing = {}
    for ev in await mcp.all("listEvaluators"):
        if ev.get("type") == "DECISION_MODEL":
            existing.setdefault(ev["name"], ev)  # names are not unique: reconcile the first match
    rules = {r["name"]: r for r in await mcp.all("listEvaluationRules")}

    for e in de.EVALUATORS:
        body = de.definition(e, a.provider, a.connection, a.model)
        ev = existing.get(e["name"])
        if ev is None:
            if a.dry_run:
                print(f"+ evaluator {e['name']} (dry run)")
            else:
                ev = await mcp.call("createEvaluator", {"name": e["name"], "description": e["description"], **body})
                ev = ev.get("evaluator", ev)
                print(f"+ evaluator {e['name']} ({ev['id']})")
        else:
            diffs = drift(latest_version(await mcp.call("getEvaluator", {"evaluatorId": ev["id"]})), body)
            if diffs and not a.dry_run:
                await mcp.call("updateEvaluator", {"evaluatorId": ev["id"], "name": e["name"],
                                                   "description": e["description"], **body})
            print(f"{'~' if diffs else '='} evaluator {e['name']}"
                  + (f" — {', '.join(diffs)}{' (dry run)' if a.dry_run else ' → new version'}" if diffs else ""))

        if a.test:
            await preview(mcp, e, body, turns[:a.test], None if a.dry_run or ev is None else ev["id"])
        if a.no_rules or ev is None:
            continue
        rule = {"name": e["rule"]["name"], "enabled": True, "sampling": e["rule"]["sampling"],
                "filter": e["rule"]["filter"], "evaluatorAssignments": [{"evaluatorId": ev["id"]}]}
        if e["rule"]["name"] in rules:
            await mcp.call("updateEvaluationRule", {"evaluationRuleId": rules[e["rule"]["name"]]["id"], **rule})
            print(f"~ rule {rule['name']} (reconciled)")
        else:
            await mcp.call("createEvaluationRule", rule)
            print(f"+ rule {rule['name']}  sampling={rule['sampling']}")
    print(f"Evaluators: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/evals")


async def main(a):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    token = base64.b64encode(f"{config.LANGFUSE_PUBLIC_KEY}:{config.LANGFUSE_SECRET_KEY}".encode()).decode()
    url = config.LANGFUSE_BASE_URL.rstrip("/") + "/api/public/mcp"
    failure = None
    async with streamablehttp_client(url, headers={"Authorization": f"Basic {token}"}, timeout=60) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            try:
                await seed(a, LangfuseMCP(session))
            except SystemExit as exc:  # leave the MCP task group first, or it wraps the exit in a traceback
                failure = exc
    if failure is not None:
        raise failure


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=sorted(de.MODELS), default="typesafe",
                    help="LLM-connection adapter serving the decision model (default: typesafe = Jev)")
    ap.add_argument("--connection", help="the connection's provider name in Langfuse (default: the adapter name)")
    ap.add_argument("--model", help=f"model id (default: {de.MODELS})")
    ap.add_argument("--test", type=int, default=0, metavar="N",
                    help="also run each evaluator on the N newest production turns and print the flags")
    ap.add_argument("--dry-run", action="store_true", help="create and change nothing (tests run as drafts)")
    ap.add_argument("--no-rules", action="store_true",
                    help="create the evaluators only: nothing runs on live traffic until a rule exists")
    asyncio.run(main(ap.parse_args()))

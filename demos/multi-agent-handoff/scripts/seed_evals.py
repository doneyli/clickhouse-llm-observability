"""
Seed online evaluation into the demo's Langfuse project (SPEC §9), stable v2 API only.

    .venv/bin/python scripts/seed_evals.py                 # code evaluators + rules
    .venv/bin/python scripts/seed_evals.py --judges        # + LLM judges (project or inherited org Anthropic connection)
    .venv/bin/python scripts/seed_evals.py --queue         # + score configs and the SESSION review queue
    .venv/bin/python scripts/seed_evals.py --dry-run

What it creates:
  code evaluators (Python, run inside Langfuse, 100% of matches, no model cost)
    handoff-context-complete  on every `handoff`      (metadata.role = handoff)
    handoff-loop              on every turn root      (metadata.role = orchestrator)
  LLM judges (--judges)
    handoff-appropriate       on every `handoff`: was this the right agent, given the roster?
    customer-resolved         on every turn root: did the reply resolve or correctly advance the request?
  review (--queue)
    score configs + the "Transferred conversations" annotation queue (SESSION items)

Every rule is scoped to production `support-turn` traces and selects its observation by
`metadata.role`, never by name. Experiments (environment `sdk-experiment`) never trigger
them. The seeder reconciles: a changed source, prompt, filter or assignment is PATCHed,
not skipped.

The judges use the LLM connection named by HANDOFF_JUDGE_PROVIDER (default "Anthropic": the
clickhouse organization's connection, which every project inherits). `--store-anthropic-key`
would instead write the demo .env's ANTHROPIC_API_KEY as a project connection; it is never
done implicitly, and not needed while the organization connection exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import httpx

DEMO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO))

from handoff import config  # noqa: E402

TRACE_NAME = "support-turn"
JUDGE_MODELS = {"handoff-appropriate": "claude-haiku-5-5", "customer-resolved": "claude-sonnet-5-5"}
# An evaluator names its LLM connection by the connection's *name*, case-sensitive. The clickhouse
# organization's inherited Anthropic connection is named "Anthropic"; a project key cannot list
# organization connections, so a wrong name only shows up as a paused judge (LLM_CONNECTION_MISSING).
JUDGE_PROVIDER = config.setting("HANDOFF_JUDGE_PROVIDER", "Anthropic")


def api(method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    auth = (config.setting("LANGFUSE_PUBLIC_KEY"), config.setting("LANGFUSE_SECRET_KEY"))
    r = httpx.request(method, f"{config.LANGFUSE_BASE_URL}{path}", json=body, params=params, auth=auth, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path} → {r.status_code}: {r.text[:600]}")
    return r.json() if r.content else {}


def paginate(path: str) -> list[dict]:
    out, cursor, page = [], None, 1
    while True:
        if "/v2/" in path:
            d = api("GET", path, params={"limit": 100, **({"cursor": cursor} if cursor else {})})
            out += d.get("data") or []
            cursor = (d.get("meta") or {}).get("cursor") or (d.get("meta") or {}).get("nextCursor")
            if not cursor:
                return out
        else:
            d = api("GET", path, params={"page": page, "limit": 100})
            out += d.get("data") or []
            if not d.get("data") or page >= ((d.get("meta") or {}).get("totalPages") or 1):
                return out
            page += 1


def scope(role: str) -> list[dict]:
    return [
        {"type": "stringOptions", "column": "traceName", "operator": "any of", "value": [TRACE_NAME]},
        {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
        {"type": "stringObject", "column": "metadata", "key": "role", "operator": "=", "value": role},
    ]


CODE = [
    {"name": "handoff-context-complete", "file": "handoff_context_complete.py", "role": "handoff",
     "description": "Every handoff carried the fields its target agent needs; no card numbers in the payload."},
    {"name": "handoff-loop", "file": "handoff_loop.py", "role": "orchestrator",
     "description": "The turn's agent path ping-ponged, repeated a hop, or hit the hop cap."},
]

HANDOFF_APPROPRIATE = """You review one handoff between agents in a telecom support desk.

The agents and what each one owns:
{{roster}}

The handoff (who it went to, the kind, the agent's stated reason, the message it sent, and the payload):
{{handoff}}

Was this the right agent to hand to, at this point?
- true: the target owns this request under the roster above. An outage credit belongs to tech-support first
  (it must confirm the outage) and to billing only after that.
- false: another agent owns it, or the request did not need a handoff at all.

Judge only the choice of agent, not how well the message was written."""

CUSTOMER_RESOLVED = """You review one turn of a telecom support chat.

The customer's message:
{{message}}

The reply the customer received:
{{reply}}

Score how well this reply resolved the customer's request, or correctly moved it forward:
- 1.0: the request is resolved, or the reply asks exactly what is needed next; it is clear and states facts
  plainly (amounts, dates, what happens next).
- 0.5: partly: something is missing, vague, or the customer will have to ask again.
- 0.0: it does not address the request, refuses without reason, or the customer is left with no next step.

You cannot see account data, so judge whether the reply is complete and coherent, not whether its numbers
are correct."""

JUDGES = [
    {"name": "handoff-appropriate", "role": "handoff", "sampling": 1.0, "prompt": HANDOFF_APPROPRIATE,
     "dataType": "BOOLEAN",
     "mapping": [{"variable": "roster", "source": "metadata", "jsonPath": "$.roster"},
                 {"variable": "handoff", "source": "input"}],
     "value": "true if the target agent owns this request under the roster, false otherwise",
     "reasoning": "Name the agent that should have received it, or say why this one is right, in one sentence"},
    {"name": "customer-resolved", "role": "orchestrator", "sampling": 1.0, "prompt": CUSTOMER_RESOLVED,
     "dataType": "NUMERIC",
     "mapping": [{"variable": "message", "source": "input", "jsonPath": "$.message"},
                 {"variable": "reply", "source": "output", "jsonPath": "$.reply"}],
     "value": "0.0 to 1.0; 1 = resolved or correctly advanced",
     "reasoning": "One sentence on what is missing, or say the reply is complete"},
]

FAILURE_MODES = ["none", "misroute", "context-lost", "ping-pong", "wrong-credit", "unresolved", "other"]
SCORE_CONFIGS = [
    {"name": "reviewer-resolved", "dataType": "BOOLEAN",
     "description": "Reviewer: the conversation ended with the customer's issue resolved (1) or not (0)"},
    {"name": "handoff-failure-mode", "dataType": "CATEGORICAL",
     "description": "Reviewer: the main handoff failure in this conversation",
     "categories": [{"label": label, "value": i} for i, label in enumerate(FAILURE_MODES)]},
]
QUEUE = "Transferred conversations"


def _upsert_evaluator(existing: dict, name: str, definition: dict, same) -> dict:
    ev = existing.get(name)
    if ev is None:
        print(f"+ evaluator {name}")
        return api("POST", "/api/public/v2/evaluators", definition)
    if same(ev):
        print(f"= evaluator {name}")
        return ev
    print(f"~ evaluator {name} (new version)")
    return api("PATCH", f"/api/public/v2/evaluators/{ev['id']}", definition)


def _upsert_rule(rules: dict, name: str, role: str, evaluator_id: str, mapping, sampling: float) -> None:
    body = {"name": name, "enabled": True, "sampling": sampling, "filter": scope(role),
            "evaluatorAssignments": [{"evaluatorId": evaluator_id, "variableMapping": mapping}]}
    if name in rules:
        cur = rules[name]
        drift = [k for k in ("enabled", "sampling", "filter") if cur.get(k) != body[k]]
        assigned = [(a.get("evaluatorId"), a.get("variableMapping")) for a in cur.get("evaluatorAssignments") or []]
        if assigned != [(evaluator_id, mapping)]:
            drift.append("evaluatorAssignments")
        if not drift:
            print(f"= rule {name}")
            return
        api("PATCH", f"/api/public/v2/evaluation-rules/{cur['id']}", body)
        print(f"~ rule {name} reconciled ({', '.join(drift)})  role={role} sampling={sampling}")
    else:
        api("POST", "/api/public/v2/evaluation-rules", body)
        print(f"+ rule {name}  role={role} sampling={sampling}")


def seed_code(evaluators: dict, rules: dict) -> None:
    for c in CODE:
        src = (DEMO / "evaluators" / c["file"]).read_text()
        definition = {"type": "code", "name": c["name"], "description": c["description"],
                      "sourceCode": src, "sourceCodeLanguage": "PYTHON"}
        ev = _upsert_evaluator(evaluators, c["name"], definition, lambda e: e.get("sourceCode") == src)
        _upsert_rule(rules, c["name"], c["role"], ev["id"], None, 1.0)


def seed_judges(evaluators: dict, rules: dict) -> None:
    for j in JUDGES:
        model = {"provider": JUDGE_PROVIDER, "model": JUDGE_MODELS[j["name"]]}
        definition = {"type": "llm_as_judge", "name": j["name"],
                      "description": f"multi-agent-handoff · {j['name']} ({j['role']})",
                      "prompt": [{"role": "user", "content": j["prompt"]}], "modelConfig": model,
                      "outputDefinition": {"dataType": j["dataType"], "scoreValueInstructions": j["value"],
                                           "scoreReasoningInstructions": j["reasoning"]}}

        def same(e, j=j, model=model):
            p = e.get("prompt") or [{}]
            text = p[0].get("content") if isinstance(p, list) and p else p
            out = e.get("outputDefinition") or {}
            return (text == j["prompt"] and out.get("dataType") == j["dataType"]
                    and (e.get("modelConfig") or {}) == model)
        ev = _upsert_evaluator(evaluators, j["name"], definition, same)
        _upsert_rule(rules, j["name"], j["role"], ev["id"], j["mapping"], j["sampling"])


def seed_queue() -> None:
    existing = {c["name"]: c for c in paginate("/api/public/score-configs")}
    ids = {}
    for c in SCORE_CONFIGS:
        if c["name"] in existing:
            ids[c["name"]] = existing[c["name"]]["id"]
            print(f"= score config {c['name']}")
        else:
            ids[c["name"]] = api("POST", "/api/public/score-configs", c)["id"]
            print(f"+ score config {c['name']}")
    queues = {q["name"]: q for q in paginate("/api/public/annotation-queues")}
    if QUEUE in queues:
        print(f"= annotation queue {QUEUE} ({queues[QUEUE]['id']})")
        return
    q = api("POST", "/api/public/annotation-queues", {
        "name": QUEUE,
        "description": ("Conversations where a specialist took over. Review the whole session: did the "
                        "customer end resolved, and which handoff failed if not. Labels calibrate "
                        "customer-resolved and handoff-appropriate."),
        "scoreConfigIds": [ids["reviewer-resolved"], ids["handoff-failure-mode"]]})
    print(f"+ annotation queue {QUEUE} ({q['id']})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judges", action="store_true")
    ap.add_argument("--queue", action="store_true")
    ap.add_argument("--store-anthropic-key", action="store_true",
                    help="write the demo .env ANTHROPIC_API_KEY into the project as its LLM connection")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        for c in CODE:
            print(f"code  {c['name']:26s} role={c['role']}  ← evaluators/{c['file']}")
        for j in JUDGES:
            print(f"judge {j['name']:26s} role={j['role']}  model={JUDGE_MODELS[j['name']]}")
        return
    evaluators = {e["name"]: e for e in paginate("/api/public/v2/evaluators")}
    rules = {r["name"]: r for r in paginate("/api/public/v2/evaluation-rules")}
    seed_code(evaluators, rules)
    if args.judges:
        if args.store_anthropic_key:
            api("PUT", "/api/public/llm-connections",
                {"provider": "anthropic", "adapter": "anthropic", "secretKey": config.setting("ANTHROPIC_API_KEY")})
            print("✓ LLM connection: anthropic")
        if not any(c.get("provider") == JUDGE_PROVIDER for c in paginate("/api/public/llm-connections")):
            # A project inherits organization connections, and a project API key cannot list those
            # (organizationLlmApiKeys:read). So this is a note, not a stop: the judges' status after seeding
            # (active vs. paused) and their first scores are the real check.
            print(f"· no project connection named {JUDGE_PROVIDER!r}; expecting an inherited organization connection")
        seed_judges(evaluators, rules)
        for e in paginate("/api/public/v2/evaluators"):
            if e["name"] in JUDGE_MODELS:
                why = f" ({e.get('pausedReason')}: {e.get('pausedMessage')})" if e.get("status") == "paused" else ""
                print(f"  {e['name']}: status {e.get('status', '?')}{why}")
    if args.queue:
        seed_queue()
    print(f"Evaluators: {config.LANGFUSE_BASE_URL}/project/{project_id()}/evals")


def project_id() -> str:
    return api("GET", "/api/public/projects")["data"][0]["id"]


if __name__ == "__main__":
    main()

"""Provision the demo's Langfuse-managed LLM-as-a-Judge evaluators on a REMOTE
Langfuse (Cloud), over the STABLE v2 evaluator API.

Called by `scripts/seed_managed_evaluators.sh` when LANGFUSE_HOST is not
localhost. The self-hosted path stays in that script (direct Postgres INSERT,
because the 3.221.1 server has no evaluator API at all).

Why this file exists
--------------------
The previous implementation used `POST /api/public/unstable/evaluation-rules`
and `/unstable/evaluators`. Those endpoints were REMOVED from Langfuse Cloud on
2026-09-04 and now return 404 for both GET and POST. The old code degraded
politely — warn, print a UI recipe, exit 0 — so `run_demo.sh` still reported
success while leaving a fresh project with no managed judges at all. That is the
failure this module fixes.

What changed in the port (v2 is not a rename of the unstable API)
-----------------------------------------------------------------
  * `mapping`            -> `variableMapping`
  * `variables`          -> gone from the request; Langfuse derives them from
                            the prompt's `{{placeholders}}`
  * `outputDefinition`   -> FLAT. `{"score":{"description":…},"reasoning":…}`
                            became `scoreValueInstructions` /
                            `scoreReasoningInstructions` alongside `dataType`.
  * `target: observation`-> gone. v2 rules are observation rules.
  * rule -> evaluator link: `{"evaluator": {"name":…, "scope": "managed"|"custom"}}`
                            became `evaluatorAssignments: [{"evaluatorId": …}]`,
                            i.e. a reference by STABLE ID, not by name+scope.
  * `prompt`             -> an ordered list of chat messages (a bare string is
                            still accepted as a single user message). This is
                            what makes multi-message judge prompts expressible.

The consequence that shapes this whole file: **v2 has no way to reference a
Langfuse-provided evaluator template.** The old API let a rule point at the
`Helpfulness` / `Relevance` "managed families" by name. v2 has no template
endpoint (`/v2/evaluator-templates`, `/v2/eval-templates` and friends all 404,
and there is an open request for one), and the UI's template gallery
materialises a template into the project as an ordinary local evaluator with
its own prompt. So we carry the prompt text ourselves.

The two prompts below were read back verbatim from the evaluators the old
script had already created in the `real-estate` project (Helpfulness v2,
Relevance v1), so the judges behave exactly as they did before this port.
They are Langfuse's wording, including the scale wobble in Helpfulness ("scale
from 0 to 10" in the instruction, 0-1 in the score definition) — deliberately
NOT corrected, so what this script produces matches what a customer clicking
the same template in the UI gets.

Reconciliation (a behaviour change, on purpose)
-----------------------------------------------
The old script skipped any rule whose name already existed, and said so:
"this 'already present' check skips a rule whose FILTER has drifted. It will
not repair one." That bit us. On 2026-09-03 the live `stated-constraint-respected`
rule was edited to filter on `metadata.langfuse_eval_target =
conversation-transcript`; the agent never writes that key (its snapshot
observations come back with empty metadata), so the judge matched nothing and
scored nothing for the next 20 days, silently. The seeder could not have fixed
it.

So this module RECONCILES: if a rule exists but its filter, assignments,
sampling or enabled flag differ from what the demo needs, it PATCHes it back
and prints what it changed. Pass --no-reconcile to get the old skip-if-present
behaviour.

Usage
-----
    ./.venv/bin/python scripts/seed_managed_evaluators_cloud.py
    ./.venv/bin/python scripts/seed_managed_evaluators_cloud.py --dry-run
    ./.venv/bin/python scripts/seed_managed_evaluators_cloud.py --no-reconcile

Exit codes: 1 only if the keys do not resolve to the expected project (refusing
to write somewhere unexpected). Everything else exits 0 with a printed UI
fallback, so run_demo.sh keeps flowing.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # the .sh already sourced .env; standalone use just needs it too
    load_dotenv = None

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[2m", "\033[0m",
)


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def info(msg: str) -> None:
    print(f"    {DIM}{msg}{RESET}")


# ── the judges ───────────────────────────────────────────────────────────────
# Each entry is one evaluator (how to score) plus one rule (what to score).
#
# TRACE_NAME is the per-turn root span. Every turn is its own trace, so the two
# reply-level judges run on that root observation: input={"query"}, output=the
# final answer.
TRACE_NAME = "handle-concierge-chat-message"
SNAPSHOT_NAME = "conversation-snapshot"  # keep in sync with agent/concierge.py

# Shared by both Langfuse-template judges. 1 = good, consistent with the code
# and custom scores in this demo. (We deliberately do not seed the built-in
# "Hallucination" template: it scores 1 = BAD, which is confusing next to
# everything else. The custom `groundedness` judge covers that concern in the
# consistent direction.)
_TEMPLATE_OUTPUT = {
    "dataType": "NUMERIC",
    "scoreValueInstructions": (
        "Score between 0 and 1. Score 0 if false or negative and 1 if true or positive"
    ),
    "scoreReasoningInstructions": "One sentence reasoning for the score",
}
_TEMPLATE_MAPPING = [
    {"variable": "query", "source": "input", "jsonPath": "$.query"},
    {"variable": "generation", "source": "output"},
]

# The reply-level rules filter on `isRootObservation`, NOT on observation `name`.
# Matching the root by name means a rename of TRACE_NAME silently stops the
# judges firing with no error anywhere — that exact drift had already happened
# once, when the live rules were still filtering on the long-gone
# `property-concierge` / `turn-N` names and had scored nothing for weeks.
# `traceName` stays as the scope guard so the rules ignore experiment and probe
# traffic.
_ROOT_FILTER = [
    {"type": "stringOptions", "column": "traceName", "operator": "any of",
     "value": [TRACE_NAME]},
    {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
]

HELPFULNESS_PROMPT = """Evaluate the helpfulness of the generation on a continuous scale from 0 to 10. A generation can be considered helpful (Score: 1) if it not only effectively addresses the user's query by providing accurate and relevant information, but also does so in a friendly and engaging manner. The content should be clear and assist in understanding or resolving the query.

Example:
Query: Can eating carrots improve your vision?
Generation: Yes, eating carrots significantly improves your vision, especially at night. This is why people who eat lots of carrots never need glasses. Anyone who tells you otherwise is probably trying to sell you expensive eyewear or doesn't want you to benefit from this simple, natural remedy. It's shocking how the eyewear industry has led to a widespread belief that vegetables like carrots don't help your vision. People are so gullible to fall for these money-making schemes.
Score: 0.1
Reasoning: Most of the generation, for instance the part on the eyewear industry, is not directly answering the question so not very helpful to the user. Furthermore, disrespectful words such as 'gullible' make the generation unfactual and thus, unhelpful. Using words with negative connotation generally will scare users off and therefore reduce helpfulness.

Input:
Query: {{query}}
Generation: {{generation}}

Think step by step."""

RELEVANCE_PROMPT = """Evaluate the relevance of the generation on a continuous scale from 0 to 1. A generation can be considered relevant (Score: 1) if it enhances or clarifies the response, adding value to the user's comprehension of the topic in question. Relevance is determined by the extent to which the provided information addresses the specific question asked, staying focused on the subject without straying into unrelated areas or providing extraneous details.

Example:
Query: Can eating carrots improve your vision?
Generation: Yes, eating carrots significantly improves your vision, especially at night. This is why people who eat lots of carrots never need glasses. Anyone who tells you otherwise is probably trying to sell you expensive eyewear or doesn't want you to benefit from this simple, natural remedy. It's shocking how the eyewear industry has led to a widespread belief that vegetables like carrots don't help your vision. People are so gullible to fall for these money-making schemes.
Score: 0.1
Reasoning: Only the first part of the first sentence clearly answers the question and thus, is relevant. The rest of the text is not relevant to answer the query.

Input:
Query: {{query}}
Generation: {{generation}}

Think step by step."""

# The conversation-level judge: the one a template cannot express.
#
# Helpfulness and Relevance each score ONE reply. Neither can answer "did this
# CONVERSATION hold together", because an observation-level evaluator sees ONLY
# the observation it matched — not siblings, not children. So this is a custom
# evaluator whose single variable is the whole transcript, wired to the
# `conversation-snapshot` observation the agent emits on the final turn.
#
# Two deliberate differences from the rules above:
#   * Filtered by observation `name`, NOT `isRootObservation` — the snapshot is
#     a CHILD of the root span, so the root filter would never match it. That
#     makes this rule name-coupled: rename SNAPSHOT_NAME without updating it
#     here and the judge goes quiet with no error anywhere.
#   * The metric is named after the failure it detects, not a generic quality
#     word. Langfuse's own eval guidance is explicit that helpfulness /
#     relevance / task-completion style names are hard to act on.
CONVERSATION_PROMPT = """You are scoring an ENTIRE multi-turn conversation between a user and a real-estate assistant, not a single reply.

A constraint is anything the user stated about what they want: a budget, a city or neighbourhood, a number of bedrooms, a required feature, buy vs rent, or the language they are writing in.

Score 1.0 only if EVERY constraint the user stated at ANY point still held for the rest of the conversation. Penalise heavily, toward 0.0:
- a constraint stated once early and then ignored in a later turn (e.g. the user lowered their budget and a later turn recommended something above it)
- a reference to an earlier property ("that one", "the second option", a neighbourhood name) resolved to the wrong property or to nothing
- the assistant re-asking something the user had already answered
- the assistant switching language away from the user's most recent turn

In your reasoning, name the specific constraint and the turn number where it was dropped. If nothing was dropped, say so in one sentence.

=== CONVERSATION ===
{{transcript}}"""

JUDGES = [
    {
        "name": "Helpfulness",
        "note": "reply-level, from Langfuse's Helpfulness template",
        "evaluator": {
            "type": "llm_as_judge",
            "name": "Helpfulness",
            "prompt": [{"role": "user", "content": HELPFULNESS_PROMPT}],
            "variableMapping": _TEMPLATE_MAPPING,
            "outputDefinition": _TEMPLATE_OUTPUT,
        },
        "rule": {
            "name": "Helpfulness",
            "enabled": True,
            "sampling": 1,
            "filter": _ROOT_FILTER,
        },
    },
    {
        "name": "Relevance",
        "note": "reply-level, from Langfuse's Relevance template",
        "evaluator": {
            "type": "llm_as_judge",
            "name": "Relevance",
            "prompt": [{"role": "user", "content": RELEVANCE_PROMPT}],
            "variableMapping": _TEMPLATE_MAPPING,
            "outputDefinition": _TEMPLATE_OUTPUT,
        },
        "rule": {
            "name": "Relevance",
            "enabled": True,
            "sampling": 1,
            "filter": _ROOT_FILTER,
        },
    },
    {
        "name": "stated-constraint-respected",
        "note": "conversation-level, fires once per conversation",
        "evaluator": {
            "type": "llm_as_judge",
            "name": "stated-constraint-respected",
            "prompt": [{"role": "user", "content": CONVERSATION_PROMPT}],
            "variableMapping": [
                {"variable": "transcript", "source": "input", "jsonPath": "$.transcript"},
            ],
            "outputDefinition": {
                "dataType": "NUMERIC",
                "scoreValueInstructions": (
                    "1.0 if every constraint the user stated held for the whole "
                    "conversation; 0.0 if one was dropped, mis-resolved, or re-asked"
                ),
                "scoreReasoningInstructions": (
                    "Name the specific constraint and the turn it was dropped at, "
                    "or state that none were"
                ),
            },
        },
        "rule": {
            "name": "stated-constraint-respected",
            "enabled": True,
            "sampling": 1,
            "filter": [
                {"type": "stringOptions", "column": "traceName", "operator": "any of",
                 "value": [TRACE_NAME]},
                {"type": "stringOptions", "column": "name", "operator": "any of",
                 "value": [SNAPSHOT_NAME]},
            ],
        },
    },
]


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Api:
    def __init__(self, host: str, public_key: str, secret_key: str, timeout: int = 30):
        self.host = host.rstrip("/")
        self.timeout = timeout
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.auth = f"Basic {token}"

    def request(self, method: str, path: str, body: dict | None = None):
        """Return (status, parsed_json_or_text). Never raises on an HTTP error."""
        url = f"{self.host}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read().decode()
                try:
                    return res.status, json.loads(raw)
                except json.JSONDecodeError:
                    return res.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, raw
        except Exception as e:  # DNS, TLS, timeout
            return 0, str(e)

    def paginate(self, path: str, limit: int = 100) -> list[dict]:
        """Collect every page of a cursor-paginated v2 list endpoint.

        The envelope is {"data": [...], "meta": {"cursor": "..."}}; the cursor is
        absent or null on the last page. Bounded so a pathological response
        cannot spin forever.
        """
        rows: list[dict] = []
        cursor = None
        for _ in range(50):
            sep = "&" if "?" in path else "?"
            url = f"{path}{sep}limit={limit}"
            if cursor:
                url += f"&cursor={urllib.parse.quote(cursor, safe='')}"
            status, payload = self.request("GET", url)
            if status != 200 or not isinstance(payload, dict):
                raise RuntimeError(f"GET {path} -> {status}: {str(payload)[:300]}")
            rows.extend(payload.get("data") or [])
            cursor = (payload.get("meta") or {}).get("cursor")
            if not cursor:
                break
        return rows


def _err(payload) -> str:
    """v2 errors carry structured codes; surface them instead of a bare status."""
    if isinstance(payload, dict):
        for key in ("error", "code", "message"):
            if payload.get(key):
                return f"{payload.get('code') or ''} {payload.get('message') or payload.get(key)}".strip()
        return json.dumps(payload)[:300]
    return str(payload)[:300]


def _norm(value) -> str:
    """Stable form for comparing filters / assignments regardless of key order."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _print_change(field: str, before, after) -> None:
    """Show what actually differs.

    Filters are lists whose common prefix is usually identical (every rule here
    shares the `traceName` scope guard), so printing a truncated dump of each
    side renders two lines that look the same. Diff element-wise instead and
    only print the entries that changed.
    """
    if isinstance(before, list) and isinstance(after, list):
        old = {_norm(x) for x in before}
        new = {_norm(x) for x in after}
        for gone in sorted(old - new):
            info(f"{field} -  {gone}")
        for added in sorted(new - old):
            info(f"{field} +  {added}")
        return
    info(f"{field}: {_norm(before)} → {_norm(after)}")


# ── steps ────────────────────────────────────────────────────────────────────
def verify_project(api: Api, expected: str) -> bool:
    """Refuse to write anywhere unexpected — the LLM-connection PUT below
    uploads a real Anthropic secret. Mirrors config.verify_project."""
    status, payload = api.request("GET", "/api/public/projects")
    if status != 200 or not isinstance(payload, dict):
        print(f"Refusing: could not resolve project on {api.host} "
              f"(HTTP {status}): {_err(payload)}")
        return False
    names = [p.get("name") for p in payload.get("data") or []]
    if expected not in names:
        print(f"Refusing: keys resolve to {names or '<none>'}, expected '{expected}'.")
        return False
    ok(f"Project verified: {expected} @ {api.host}")
    return True


def upsert_llm_connection(api: Api) -> None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        warn("ANTHROPIC_API_KEY not set — add the connection in Settings > LLM Connections")
        return
    status, payload = api.request("PUT", "/api/public/llm-connections", {
        "provider": "anthropic", "adapter": "anthropic", "secretKey": key,
    })
    if status in (200, 201):
        ok("Anthropic LLM connection upserted")
    else:
        warn(f"Could not upsert LLM connection (HTTP {status}: {_err(payload)}) "
             f"— add it in Settings > LLM Connections")


def ensure_evaluator(api: Api, spec: dict, existing: list[dict], dry_run: bool) -> str | None:
    """Return the evaluator id, creating it if no evaluator of that name exists.

    Names are explicitly NOT identifiers in v2 and need not be unique, so this
    lookup is a heuristic — but it is the only idempotency key a seeder has, and
    it matters MORE than it did under the unstable API: creating twice now
    succeeds and leaves two evaluators with the same name rather than a 409.
    """
    name = spec["name"]
    match = next((e for e in existing if e.get("name") == name), None)
    if match:
        ok(f"{name} evaluator present (v{match.get('version')}, {match['id']})")
        return match["id"]
    if dry_run:
        info(f"would POST /api/public/v2/evaluators  name={name}")
        return "dry-run"
    status, payload = api.request("POST", "/api/public/v2/evaluators", spec)
    if status in (200, 201) and isinstance(payload, dict) and payload.get("id"):
        ok(f"{name} evaluator created ({payload['id']})")
        return payload["id"]
    warn(f"{name} evaluator failed (HTTP {status}: {_err(payload)})")
    return None


def ensure_rule(api: Api, spec: dict, evaluator_id: str, existing: list[dict],
                note: str, reconcile: bool, dry_run: bool) -> bool:
    """Create the rule, or PATCH it back into shape if it has drifted."""
    name = spec["name"]
    desired = dict(spec)
    desired["evaluatorAssignments"] = [{"evaluatorId": evaluator_id,
                                        "variableMapping": None}]
    match = next((r for r in existing if r.get("name") == name), None)

    if match is None:
        if dry_run:
            info(f"would POST /api/public/v2/evaluation-rules  name={name}")
            return True
        status, payload = api.request("POST", "/api/public/v2/evaluation-rules", desired)
        if status in (200, 201):
            ok(f"{name} rule created — {note}" if note else f"{name} rule created")
            return True
        warn(f"{name} rule failed (HTTP {status}: {_err(payload)})")
        return False

    # Rule exists. Compare the fields the demo actually depends on.
    drift = []
    if _norm(match.get("filter")) != _norm(desired["filter"]):
        drift.append("filter")
    live_ids = [a.get("evaluatorId") for a in match.get("evaluatorAssignments") or []]
    if live_ids != [evaluator_id]:
        drift.append("evaluatorAssignments")
    if match.get("enabled") is not desired["enabled"]:
        drift.append("enabled")
    if match.get("sampling") != desired["sampling"]:
        drift.append("sampling")

    if not drift:
        ok(f"{name} rule present and correct ({match['id']})")
        return True
    if not reconcile:
        warn(f"{name} rule present but DRIFTED on {', '.join(drift)} — "
             f"left alone (--no-reconcile). It may be scoring nothing.")
        return True
    if dry_run:
        info(f"would PATCH rule {match['id']} — {', '.join(drift)}")
        return True
    status, payload = api.request(
        "PATCH", f"/api/public/v2/evaluation-rules/{match['id']}", desired)
    if status in (200, 201):
        ok(f"{name} rule repaired ({', '.join(drift)})")
        for field in drift:
            _print_change(field, match.get(field), desired.get(field))
        return True
    warn(f"{name} rule PATCH failed (HTTP {status}: {_err(payload)})")
    return False


UI_FALLBACK = """
  Finish in the Langfuse UI (~3 min), project '{project}':
    1. Settings > LLM Connections — confirm the 'anthropic' connection exists.
    2. Evaluators > + New evaluator, twice — pick the 'Helpfulness' and
       'Relevance' templates from the gallery. For each: map query -> input
       ($.query) and generation -> output, then attach a rule filtered to
       traceName any of [{trace}] AND Is Root Observation = true.
    3. Evaluators > + New evaluator > LLM-as-a-Judge (blank, NOT a template) —
       name 'stated-constraint-respected', one variable {{{{transcript}}}},
       numeric score, mapping transcript -> input ($.transcript). Rule filter:
       traceName any of [{trace}] AND name any of [{snapshot}]. This is the
       conversation-level judge: it runs once per conversation, on the snapshot
       observation the agent emits on the final turn.

  Tip: the sample-observation panel on the right of the editor runs the judge
  against real traffic before you save it — worth using on step 3.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the writes that would happen, change nothing")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip existing rules instead of repairing drifted ones")
    args = ap.parse_args()

    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")

    host = os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL") or ""
    pk, sk = os.getenv("LANGFUSE_PUBLIC_KEY", ""), os.getenv("LANGFUSE_SECRET_KEY", "")
    project = os.getenv("LANGFUSE_PROJECT_NAME", "real-estate")
    if not (host and pk and sk):
        print("LANGFUSE_HOST / _PUBLIC_KEY / _SECRET_KEY must all be set.")
        return 1

    api = Api(host, pk, sk)
    if not verify_project(api, project):
        return 1

    if args.dry_run:
        info("dry run — no writes")
    else:
        upsert_llm_connection(api)

    try:
        evaluators = api.paginate("/api/public/v2/evaluators")
        rules = api.paginate("/api/public/v2/evaluation-rules")
    except RuntimeError as e:
        # A 404 here means the stable API is missing, i.e. a self-hosted v3
        # server reached through a non-localhost URL. Say so plainly rather
        # than reporting three identical failures below.
        warn(f"Could not read the evaluator API: {e}")
        warn("If this host is a self-hosted Langfuse v3, it has no evaluator "
             "API — run this script against localhost so it seeds Postgres, "
             "or upgrade the server to v4.")
        print(UI_FALLBACK.format(project=project, trace=TRACE_NAME,
                                 snapshot=SNAPSHOT_NAME))
        return 0

    info(f"{len(evaluators)} evaluator(s), {len(rules)} rule(s) already in the project")

    failed = False
    for judge in JUDGES:
        evaluator_id = ensure_evaluator(api, judge["evaluator"], evaluators, args.dry_run)
        if not evaluator_id:
            failed = True
            continue
        if not ensure_rule(api, judge["rule"], evaluator_id, rules,
                           note=judge["note"], reconcile=not args.no_reconcile,
                           dry_run=args.dry_run):
            failed = True

    if failed:
        print(UI_FALLBACK.format(project=project, trace=TRACE_NAME,
                                 snapshot=SNAPSHOT_NAME))
    elif not args.dry_run:
        print()
        print("  Judges score NEW traffic automatically (allow a few minutes).")
        print("  To score EXISTING traces: Traces > select > Actions > Evaluate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

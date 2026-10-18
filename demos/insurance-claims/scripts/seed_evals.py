"""
Seed managed evaluation — idempotent and reconciling, stable v2 API only.

Creates, in the Langfuse project behind .env/.env.<profile>:
  1. an LLM connection for the judge model (production: Bedrock over a VPC endpoint,
     or an OpenAI-compatible adapter pointed at the internal gateway)
  2. LLM-as-a-judge evaluators + rules (SPEC.md §8)

Every rule selects its target observation by `metadata.role` (set by the code on every
step observation), never by observation name, and only in the production environment
of the claims trace — experiments and probes never burn judge tokens. Sampling per rule
is the cost lever. The seeder reconciles: a changed prompt, filter or mapping is
PATCHed, not skipped.

    .venv/bin/python scripts/seed_evals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import config  # noqa: E402

TRACE_NAME = "claims-orchestrator"
JUDGE = {"provider": "anthropic", "model": config.JUDGE_MODEL}


def role_filter(role: str) -> list[dict]:
    return [
        {"type": "stringOptions", "column": "traceName", "operator": "any of", "value": [TRACE_NAME]},
        {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["production"]},
        {"type": "stringObject", "column": "metadata", "key": "role", "operator": "=", "value": role},
    ]


CERTIFICATE_EXTRACTION = """You are a claims examiner checking the document-intake step of a life-insurance death claim.

Below are the claim DOCUMENTS (images) and the EXTRACTION the intake agent produced from them.

Check every field the extraction reports for a death certificate, claimant statement or beneficiary form against what is printed on the documents: names, dates (date of death, date of birth), state, cause and manner of death, certificate and policy numbers, beneficiaries and shares.

- true: every extracted value matches the document, and every value that is printed and legible was extracted (null only where the document really has no value)
- false: any extracted value differs from the document, is invented, or a printed value was left out

Ignore formatting differences (e.g. "Natural" vs "natural", date formats that denote the same date).

DOCUMENTS:
{{documents}}

EXTRACTION:
{{extraction}}"""

JUDGES = [
    {"name": "certificate-extraction", "role": "doc_intake", "sampling": 1.0,
     "prompt": CERTIFICATE_EXTRACTION, "dataType": "BOOLEAN",
     "mapping": [{"variable": "documents", "source": "input"},
                 {"variable": "extraction", "source": "output"}],
     "value": "true if every extracted field matches the documents, false otherwise",
     "reasoning": "Name each field that does not match and quote both values, or say all fields match"},
]


def paginate(path: str) -> list[dict]:
    if "/v2/" in path:  # v2 endpoints: cursor pagination
        out, cursor = [], None
        while True:
            d = config.api("GET", path, params={"limit": 100, **({"cursor": cursor} if cursor else {})})
            out += d.get("data") or []
            cursor = (d.get("meta") or {}).get("nextCursor") or (d.get("meta") or {}).get("cursor")
            if not cursor:
                return out
    out, page = [], 1
    while True:
        d = config.api("GET", path, params={"page": page, "limit": 100})
        items = d.get("data") or []
        out += items
        if not items or page >= ((d.get("meta") or {}).get("totalPages") or 1):
            return out
        page += 1


def _definition(j: dict) -> dict:
    return {"type": "llm_as_judge", "name": j["name"],
            "description": f"Northwind Life claims · {j['name']} ({j['role']} step)",
            "prompt": [{"role": "user", "content": j["prompt"]}], "modelConfig": JUDGE,
            "outputDefinition": {"dataType": j["dataType"], "scoreValueInstructions": j["value"],
                                 "scoreReasoningInstructions": j["reasoning"]}}


def main() -> int:
    if config.ANTHROPIC_API_KEY:
        config.api("PUT", "/api/public/llm-connections",
                   {"provider": "anthropic", "adapter": "anthropic", "secretKey": config.ANTHROPIC_API_KEY})
        print(f"✓ LLM connection: anthropic (judge model {JUDGE['model']})")

    evaluators = {e["name"]: e for e in paginate("/api/public/v2/evaluators")}
    rules = {r["name"]: r for r in paginate("/api/public/v2/evaluation-rules")}
    for j in JUDGES:
        ev = evaluators.get(j["name"])
        if ev is None:
            ev = config.api("POST", "/api/public/v2/evaluators", _definition(j))
            print(f"+ evaluator {j['name']}")
        else:
            current = ev.get("prompt") or [{}]
            text = current[0].get("content") if isinstance(current, list) and current else current
            if text != j["prompt"] or (ev.get("outputDefinition") or {}).get("dataType") != j["dataType"]:
                ev = config.api("PATCH", f"/api/public/v2/evaluators/{ev['id']}", _definition(j))
                print(f"~ evaluator {j['name']} updated (new version)")
            else:
                print(f"= evaluator {j['name']}")
        body = {"name": j["name"], "enabled": True, "sampling": j["sampling"], "filter": role_filter(j["role"]),
                "evaluatorAssignments": [{"evaluatorId": ev["id"], "variableMapping": j["mapping"]}]}
        if j["name"] in rules:
            config.api("PATCH", f"/api/public/v2/evaluation-rules/{rules[j['name']]['id']}", body)
            print(f"~ rule {j['name']} (reconciled)")
        else:
            config.api("POST", "/api/public/v2/evaluation-rules", body)
            print(f"+ rule {j['name']}  role={j['role']} sampling={j['sampling']}")
    print(f"Evaluators: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/evals")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Managed LLM-as-a-judge evaluators for the marketing agent (v2 evaluator API, idempotent by name).

  request-fulfilled    on each run's root observation: did the reply deliver what the
                       marketer asked for, or stop, stall or deliver something else?
  retrieval-relevance  on every retrieve_brand_context: are the passages relevant to the
                       query? (Expect stale-but-on-topic passages to score HIGH: relevance
                       is not freshness. The retriever's metadata shows what the judge can't.)

Rules score NEW observations only (the API does not backfill). To score older traces,
use a batch evaluation from the UI (select traces → Evaluate), or just run new traffic.

    .venv/bin/python scripts/seed_evaluators.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config  # noqa: E402

JUDGE_MODELS = [os.environ.get("JUDGE_MODEL", "claude-opus-5-5"), "claude-sonnet-5-5"]

REQUEST_FULFILLED = """You are reviewing one run of an AI marketing agent. A marketer gave it a task; the agent used tools and then replied.

Score 1 if the reply delivers what the task asked for (the analysis, the queued corrections, the staged draft, the report), with any caveats stated briefly.
Score 0 if the reply stops short: it says it could not finish, hit a step limit, asks the marketer for facts the agent should know (such as the brand's own current pricing), leaves part of the request out (such as an engine missing from a report), or delivers something other than what was asked.

Task:
{{task}}

Agent reply:
{{reply}}"""

RETRIEVAL_RELEVANCE = """You are grading a retrieval step. A search query was run against a brand's knowledge base and returned passages.

Score from 0 to 1: the share of returned passages that are relevant to answering the query. A passage is relevant if it is about the topic the query asks about. Judge topical relevance only; you cannot see when a passage was written.

Query:
{{query}}

Returned passages:
{{passages}}"""

TRACE = {"type": "stringOptions", "column": "traceName", "operator": "any of", "value": ["marketing-agent"]}

JUDGES = [
    {"evaluator": {"type": "llm_as_judge", "name": "request-fulfilled",
                   "prompt": [{"role": "user", "content": REQUEST_FULFILLED}],
                   "variableMapping": [{"variable": "task", "source": "input"},
                                       {"variable": "reply", "source": "output"}],
                   "outputDefinition": {"dataType": "NUMERIC",
                                        "scoreValueInstructions": "1 if the reply delivers what was asked, 0 if it stops short",
                                        "scoreReasoningInstructions": "One sentence: what was delivered or what is missing"}},
     "rule": {"name": "request-fulfilled", "enabled": True, "sampling": 1,
              "filter": [TRACE, {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True}]}},
    {"evaluator": {"type": "llm_as_judge", "name": "retrieval-relevance",
                   "prompt": [{"role": "user", "content": RETRIEVAL_RELEVANCE}],
                   "variableMapping": [{"variable": "query", "source": "input", "jsonPath": "$.query"},
                                       {"variable": "passages", "source": "output"}],
                   "outputDefinition": {"dataType": "NUMERIC",
                                        "scoreValueInstructions": "Share of returned passages relevant to the query, 0 to 1",
                                        "scoreReasoningInstructions": "One sentence naming any irrelevant passage"}},
     "rule": {"name": "retrieval-relevance", "enabled": True, "sampling": 1,
              "filter": [TRACE, {"type": "stringOptions", "column": "type", "operator": "any of", "value": ["RETRIEVER"]}]}},
]


def _list(path):
    r = config.api("GET", path, params={"limit": 100})
    r.raise_for_status()
    return r.json().get("data", [])


def main() -> int:
    config.verify_project()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY missing from .env")
    r = config.api("PUT", "/llm-connections", json={"provider": "anthropic", "adapter": "anthropic", "secretKey": key})
    print(f"{'+' if r.status_code < 300 else '!'} LLM connection anthropic ({r.status_code})")

    evaluators = {e["name"]: e for e in _list("/v2/evaluators")}
    rules = {r_["name"]: r_ for r_ in _list("/v2/evaluation-rules")}
    for j in JUDGES:
        name = j["evaluator"]["name"]
        ev = evaluators.get(name)
        if ev is None:
            for model in JUDGE_MODELS:
                body = {**j["evaluator"], "modelConfig": {"provider": "anthropic", "model": model}}
                resp = config.api("POST", "/v2/evaluators", json=body)
                if resp.status_code < 300:
                    ev = resp.json()
                    print(f"+ evaluator {name} ({model})")
                    break
                print(f"  evaluator {name} with {model}: {resp.status_code} {resp.text[:200]}")
            if ev is None:
                print(f"! could not create evaluator {name}")
                continue
        else:
            print(f"= evaluator {name}")
        if name in rules:
            print(f"= rule {name}")
            continue
        body = {**j["rule"], "evaluatorAssignments": [{"evaluatorId": ev["id"], "variableMapping": None}]}
        resp = config.api("POST", "/v2/evaluation-rules", json=body)
        print(f"{'+' if resp.status_code < 300 else '!'} rule {name} ({resp.status_code}) {'' if resp.status_code < 300 else resp.text[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

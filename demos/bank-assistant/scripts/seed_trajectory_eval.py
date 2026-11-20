"""Seed the online trajectory check: a Langfuse CODE evaluator and its rule. Idempotent, API-only.

  evaluator  trajectory-policy   Python code evaluator; its source is northwind/trajectory_policy.py,
                                 uploaded verbatim (run the same file's tests before seeding)
  rule       trajectory-policy   every answered production `northwind-assistant` observation (100%:
                                 no model call, so no judge cost)

It runs inside Langfuse with no gold answer and scores the path the agent took on
each live turn: tool calls, repeated calls, actions grounded in ids the agent had
seen, and a dispute lookback that covers the 60-day window. The gold-path check
for experiments is northwind/evals.py `trajectory`.

After editing trajectory_policy.py, re-run: the evaluator gets a new version and
the rule uses the latest one.

  --disable    turn the rule off; the evaluator and its scores stay

Run: .venv/bin/python scripts/seed_trajectory_eval.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, trajectory_policy  # noqa: E402
from seed_evals import ANSWERED_FILTER, paginate  # noqa: E402

NAME = "trajectory-policy"
SOURCE = Path(trajectory_policy.__file__).read_text()
DESCRIPTION = ("Northwind trajectory policy: tool path, repeated calls, grounded actions, "
               "dispute lookback >= 60 days (KB-102). Source: northwind/trajectory_policy.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disable", action="store_true", help="turn the rule off; keep the evaluator and scores")
    a = ap.parse_args()

    ev = next((e for e in paginate("/api/public/v2/evaluators") if e["name"] == NAME and e.get("type") == "code"), None)
    definition = {"type": "code", "sourceCode": SOURCE, "sourceCodeLanguage": "PYTHON"}
    if ev is None:
        ev = config.api("POST", "/api/public/v2/evaluators", {"name": NAME, "description": DESCRIPTION, **definition})
        print(f"+ code evaluator {NAME}")
    else:
        current = ev.get("sourceCode")
        if current is None:  # the list may omit the source; the single-evaluator read has it
            current = config.api("GET", f"/api/public/v2/evaluators/{ev['id']}").get("sourceCode")
        if current != SOURCE:
            ev = config.api("PATCH", f"/api/public/v2/evaluators/{ev['id']}", definition)
            print(f"~ code evaluator {NAME} source updated (new version)")
        else:
            print(f"= code evaluator {NAME}")

    rules = {r["name"]: r for r in paginate("/api/public/v2/evaluation-rules")}
    body = {"name": NAME, "enabled": not a.disable, "sampling": 1.0, "filter": ANSWERED_FILTER,
            "evaluatorAssignments": [{"evaluatorId": ev["id"]}]}
    if NAME in rules:
        config.api("PATCH", f"/api/public/v2/evaluation-rules/{rules[NAME]['id']}", body)
        print(f"~ rule {NAME} (reconciled, enabled={body['enabled']})")
    else:
        config.api("POST", "/api/public/v2/evaluation-rules", body)
        print(f"+ rule {NAME}  enabled={body['enabled']}  sampling=1.0")
    print(f"Evaluators: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/evals")


if __name__ == "__main__":
    main()

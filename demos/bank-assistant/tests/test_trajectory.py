"""Contract for the trajectory checks: the offline gold-path evaluator (northwind.evals.trajectory)
and the online policy code evaluator (northwind/trajectory_policy.py, run inside Langfuse).

Run: python -m unittest discover -s tests -t . -v
"""

import json
import types
import unittest
from pathlib import Path

from northwind import evals, trajectory_policy as tp

POLICY_SOURCE = Path(tp.__file__).read_text()


def step(tool, turn=None, returned=(), **args):
    s = {"tool": tool, "args": args, "ok": True, "returned": list(returned)}
    return {**s, "turn": turn} if turn else s


GOLD = {"steps": ["list_accounts", "get_recent_transactions", "open_dispute"],
        "args": {"get_recent_transactions": {"days_at_least": 41},
                 "open_dispute": {"transaction_id_in": ["TX-87950"]}},
        "forbidden": ["block_card", "schedule_callback"]}
ACCOUNTS = step("list_accounts", returned=["ACC-1001-01", "4417"])


def scores(steps):
    return {e.name: e for e in evals.trajectory(input={}, output={"tool_calls": steps},
                                                metadata={"expected_trajectory": GOLD})}


class GoldPath(unittest.TestCase):
    def test_right_steps_in_order_pass(self):
        s = scores([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=60,
                                   returned=["TX-87950"]),
                    step("open_dispute", transaction_id="TX-87950", reason="not received")])
        self.assertEqual([s[n].value for n in ("trajectory-match", "trajectory-order", "trajectory-args",
                                                "trajectory-no-forbidden", "trajectory-steps")],
                         [1.0, 1.0, 1.0, 1.0, 3.0])
        self.assertIn("actual: list_accounts → get_recent_transactions(days=60) → open_dispute(TX-87950)",
                      s["trajectory-match"].comment)

    def test_release_150_lookback_fails_on_the_argument_not_the_order_alone(self):
        # 1.5.0: the lookup used the 30-day default, missed the 41-day-old charge, and handed off
        s = scores([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=30),
                    step("schedule_callback", topic="dispute")])
        self.assertEqual(s["trajectory-match"].value, 0.0)
        self.assertEqual(s["trajectory-order"].comment, "missing open_dispute")
        self.assertEqual(s["trajectory-args"].value, 0.0)
        self.assertIn("transaction lookup 30 days, the charge is 41 days old", s["trajectory-args"].comment)
        self.assertEqual((s["trajectory-no-forbidden"].value, s["trajectory-no-forbidden"].comment),
                         (0.0, "called schedule_callback"))

    def test_dispute_on_the_wrong_charge_fails_args(self):
        s = scores([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=60),
                    step("open_dispute", transaction_id="TX-88101", reason="x")])
        self.assertEqual((s["trajectory-order"].value, s["trajectory-args"].value), (1.0, 0.5))
        self.assertIn("FAIL: dispute opened on TX-88101 (expected TX-87950)", s["trajectory-args"].comment)

    def test_out_of_order_and_extra_steps(self):
        s = scores([step("get_recent_transactions", account_id="ACC-1001-01", days=60), ACCOUNTS,
                    step("open_dispute", transaction_id="TX-87950", reason="x")])
        self.assertEqual(s["trajectory-order"].value, 0.0)
        self.assertTrue(s["trajectory-order"].comment.startswith("out of order"))

    def test_lookup_after_the_dispute_does_not_count(self):
        s = scores([ACCOUNTS, step("open_dispute", transaction_id="TX-87950", reason="x"),
                    step("get_recent_transactions", account_id="ACC-1001-01", days=60)])
        self.assertIn("FAIL: transaction lookup never made", s["trajectory-args"].comment)

    def test_two_turn_conversation_is_one_path(self):
        s = scores([{**ACCOUNTS, "turn": 1},
                    step("get_recent_transactions", turn=1, account_id="ACC-1001-01", days=60),
                    step("search_knowledge_base", turn=1, query="dispute window"),
                    step("open_dispute", turn=2, transaction_id="TX-87950", reason="x")])
        self.assertEqual(s["trajectory-match"].value, 1.0)
        self.assertIn("→ [turn 2] → open_dispute(TX-87950)", s["trajectory-match"].comment)
        self.assertIn("extra: search_knowledge_base", s["trajectory-steps"].comment)

    def test_items_without_a_gold_path_write_no_score(self):
        self.assertEqual(evals.trajectory(input={}, output={"tool_calls": []}, metadata={}), [])

    def test_disputes_experiment_runs_it(self):
        self.assertIn(evals.trajectory, evals.DISPUTE_EVALUATORS)


def ctx(steps, message="I want to dispute the GADGETSTORE ONLINE charge.", context="", as_json=True):
    md = {"tool_call_history": json.dumps(steps) if as_json else steps, "context": context}
    return types.SimpleNamespace(observation=types.SimpleNamespace(input=message, output="…", metadata=md,
                                                                   tool_calls=[]), experiment=None)


def policy(c):
    return {s.name: s for s in tp.evaluate(c).scores}


class OnlinePolicy(unittest.TestCase):
    def test_grounded_dispute(self):
        s = policy(ctx([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=60,
                                       returned=["TX-87950"]),
                        step("open_dispute", transaction_id="TX-87950", reason="x")]))
        self.assertEqual(s["trajectory-steps"].value, 3)
        self.assertIs(s["trajectory-grounded-actions"].value, True)
        self.assertEqual(s["trajectory-redundant-calls"].value, 0)

    def test_giving_up_after_a_short_lookback_is_flagged_without_any_gold(self):
        s = policy(ctx([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=30)]))
        self.assertIs(s["trajectory-dispute-lookback"].value, False)
        self.assertIn("no dispute opened; widest transaction lookup 30 days", s["trajectory-dispute-lookback"].comment)
        self.assertNotIn("trajectory-grounded-actions", s)  # no action taken: no score

    def test_full_lookback_before_asking_to_confirm_passes(self):
        s = policy(ctx([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=60)]))
        self.assertIs(s["trajectory-dispute-lookback"].value, True)

    def test_short_lookback_that_opened_the_dispute_is_not_judged(self):
        # "charged twice this week": a 7-day lookup is right when it finds the charge
        s = policy(ctx([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=7,
                                       returned=["TX-88116"]),
                        step("open_dispute", transaction_id="TX-88116", reason="duplicate")],
                       message="STREAMFLIX charged me twice this week, please dispute the duplicate."))
        self.assertNotIn("trajectory-dispute-lookback", s)

    def test_failed_dispute_call_still_judges_the_lookback(self):
        failed = {**step("open_dispute", transaction_id="TX-87950", reason="x"), "ok": False}
        s = policy(ctx([ACCOUNTS, step("get_recent_transactions", account_id="ACC-1001-01", days=30,
                                       returned=["TX-87950"]), failed]))
        self.assertIs(s["trajectory-dispute-lookback"].value, False)

    def test_invented_transaction_id_is_ungrounded(self):
        s = policy(ctx([step("open_dispute", transaction_id="TX-12345", reason="x", returned=["TX-12345"])]))
        self.assertIs(s["trajectory-grounded-actions"].value, False)
        self.assertIn("open_dispute(TX-12345)", s["trajectory-grounded-actions"].comment)

    def test_id_from_earlier_in_the_conversation_grounds_the_confirm_turn(self):
        context = "[tool:open_dispute] {}\n\n[earlier in this conversation]\nassistant: I found TX-87950 for USD 389.99."
        s = policy(ctx([step("open_dispute", transaction_id="TX-87950", reason="x")],
                       message="Yes, that's the one, please open the dispute.", context=context))
        self.assertIs(s["trajectory-grounded-actions"].value, True)

    def test_card_named_by_the_customer_grounds_block_card(self):
        s = policy(ctx([step("block_card", card_last4="4417", reason="stolen")],
                       message="My card ending 4417 was stolen, block it"))
        self.assertIs(s["trajectory-grounded-actions"].value, True)
        self.assertNotIn("trajectory-dispute-lookback", s)

    def test_repeated_identical_call_counts_as_redundant(self):
        lookup = step("get_recent_transactions", account_id="ACC-1001-01", days=60)
        self.assertEqual(policy(ctx([ACCOUNTS, lookup, lookup]))["trajectory-redundant-calls"].value, 1)

    def test_metadata_as_parsed_list_and_no_tools(self):
        self.assertEqual(policy(ctx([ACCOUNTS], as_json=False))["trajectory-steps"].value, 1)
        none = policy(ctx([], message="What is the late fee?"))
        self.assertEqual((none["trajectory-steps"].value, none["trajectory-steps"].comment), (0, "no tool calls"))


class HostedSource(unittest.TestCase):
    """The file runs as-is in the code-evaluator runtime: stdlib only, runtime-provided types."""

    def test_imports_only_the_standard_library(self):
        imports = {line.split()[1].split(".")[0] for line in POLICY_SOURCE.splitlines()
                   if line.startswith(("import ", "from "))}
        self.assertLessEqual(imports, {"json", "re", "dataclasses", "typing"})

    def test_runs_with_the_runtime_types_already_defined(self):
        calls = []

        class Score:  # stand-ins for the runtime's own classes
            def __init__(self, **kw):
                calls.append(kw)

        class EvaluationResult:
            def __init__(self, scores):
                self.scores = scores

        ns = {"Score": Score, "EvaluationResult": EvaluationResult}
        exec(compile(POLICY_SOURCE, "trajectory_policy.py", "exec"), ns)  # noqa: S102
        self.assertIs(ns["Score"], Score)  # the module must not shadow the runtime's types
        ns["evaluate"](ctx([ACCOUNTS]))
        self.assertEqual(calls[0]["name"], "trajectory-steps")


if __name__ == "__main__":
    unittest.main()

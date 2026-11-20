"""Decision-model evaluator contract (northwind/decision_evals.py), without a live project.

The spec is only checked by the server when the seeder runs, and a decision model
fails quietly: a question that reads a state field nobody maps, or a Choice
without an escape hatch, still returns confident answers. These tests pin the
conventions, the vocabulary shared with the deterministic scores, and the
act / review / ignore routing.
Run: python -m unittest discover -s tests -t . -v
"""
import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR / "scripts"))
sys.path.insert(0, str(DEMO_DIR))
from northwind import decision_evals as de  # noqa: E402
import decision_review_band as band_report  # noqa: E402
import seed_decision_evals as seeder  # noqa: E402

AGENT = (DEMO_DIR / "northwind" / "agent.py").read_text()
SEED_EVALS = (DEMO_DIR / "scripts" / "seed_evals.py").read_text()


class SpecTest(unittest.TestCase):
    def test_every_evaluator_is_valid(self):
        for e in de.EVALUATORS:
            self.assertEqual(de.problems(e), [], e["name"])

    def test_problems_catches_an_unmapped_field_and_a_missing_escape_hatch(self):
        broken = {"name": "x", "state": ["customer_message"], "questions": [
            {"id": "q", "type": "noul", "scoreName": "q", "instructions": "Is `assistant_reply` rude?",
             "criteria": {"true": "rude", "false": "polite"}},
            {"id": "c", "type": "choice", "scoreName": "c", "instructions": "Topic of `customer_message`?",
             "options": [{"value": "other"}, {"value": "fees"}]}]}
        found = " ".join(de.problems(broken))
        self.assertIn("not in the state", found)
        self.assertIn("'other' escape hatch", found)

    def test_instructions_are_english(self):
        # The state stays in the customer's language; Spanish instructions do not help Jev.
        for name, q in de.questions_by_score().items():
            self.assertTrue(q["instructions"].isascii(), name)

    def test_score_names_do_not_collide_with_existing_scores(self):
        existing = set(re.findall(r'name="([a-z-]+)"', AGENT)) | set(re.findall(r'"name": "([a-z-]+)"', SEED_EVALS))
        self.assertTrue({"intent", "unsolicited-upsell", "faithfulness"} <= existing)
        self.assertEqual(set(de.questions_by_score()) & existing, set())

    def test_customer_intent_speaks_the_deterministic_intent_vocabulary(self):
        options = {o["value"] for o in de.questions_by_score()["customer-intent"]["options"]}
        deterministic = set(re.findall(r'intent = "([a-z-]+)"', (DEMO_DIR / "northwind" / "business.py").read_text()))
        self.assertTrue(deterministic, "intent labels moved out of business.py")
        for intent in deterministic | {"product-cards"}:
            self.assertIn(band_report.intent_family(intent), options, intent)

    def test_conduct_rule_skips_exactly_the_blocking_risks(self):
        blocking = re.search(r"blocking = \[r for r in risks if r in \(([^)]*)\)\]", AGENT).group(1)
        self.assertEqual(sorted(f"risk:{r}" for r in re.findall(r'"([a-z_]+)"', blocking)), sorted(de.BLOCKING_RISKS))
        conduct = next(e for e in de.EVALUATORS if e["name"] == "conduct-screen")
        self.assertIn({"type": "arrayOptions", "column": "tags", "operator": "none of", "value": de.BLOCKING_RISKS},
                      conduct["rule"]["filter"])

    def test_the_agent_emits_every_metadata_field_the_state_reads(self):
        for field in de.FIELDS.values():
            if field["selectedColumnId"] == "metadata":
                key = field["jsonSelector"].removeprefix("$.")
                self.assertRegex(AGENT, rf'"{key}":', key)

    def test_definition_pins_the_model_and_maps_only_the_evaluator_state(self):
        e = de.EVALUATORS[0]
        body = de.definition(e, "typesafe", connection="jev-openrouter")
        self.assertEqual(body["modelConfig"], {"provider": "jev-openrouter", "model": "jev-1.13.0"})
        self.assertEqual([m["templateVariable"] for m in body["variableMapping"]], e["state"])


class BandTest(unittest.TestCase):
    def test_noul_and_score_thresholds(self):
        noul, score = {"type": "noul"}, {"type": "score"}
        self.assertEqual([de.band(noul, v) for v in (0.7, 0.69, 0.3, 0.29)], ["act", "review", "review", "ignore"])
        self.assertEqual([de.band(score, v) for v in (2.0, 1.99, 1.0, 0.99)], ["act", "review", "review", "ignore"])

    def test_report_splits_by_language_and_returns_conduct_review_band_most_uncertain_first(self):
        turns = {
            "a": {"lang": "en", "trace": "t", "conduct-unsolicited-upsell": 0.95, "unsolicited-upsell": 1,
                  "customer-intent": "product-question", "intent": "product-fees"},
            "b": {"lang": "es", "trace": "t", "conduct-unsolicited-upsell": 0.52, "unsolicited-upsell": 0,
                  "customer-intent": "complaint", "intent": "product-fees"},
            "c": {"lang": "es", "trace": "t", "conduct-credential-request": 0.35, "signal-complaint": 0.4},
            "d": {"lang": "en", "trace": "t"},  # not scored by the decision models
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            review = band_report.report(turns)
        self.assertEqual([(oid, name) for _, oid, name in review],
                         [("b", "conduct-unsolicited-upsell"), ("c", "conduct-credential-request")])
        text = out.getvalue()
        self.assertIn("4 production assistant turns in the window, 3 with decision-model scores", text)
        self.assertIn("agree on 1/2 turns", text)
        self.assertIn("customer asked 'complaint', agent handled it as 'product-question'", text)
        self.assertIn("both flag 1 · decision model only 0 · regex only 0 · neither 1", text)

    def test_queue_adds_review_band_turns_once(self):
        calls = []

        def api(method, path, body=None, params=None):
            calls.append((method, path, body))
            if path == "/api/public/annotation-queues":
                return {"data": [{"id": "sme", "name": band_report.QUEUE}], "meta": {"totalPages": 1}}
            if method == "GET":
                return {"data": [{"objectId": "b", "objectType": "OBSERVATION"}], "meta": {"totalPages": 1}}
            return {}

        review = [(0.02, "b", "conduct-x"), (0.1, "c", "conduct-x"), (0.1, "c", "conduct-y"), (0.2, "e", "conduct-x")]
        with mock.patch.object(band_report.config, "api", side_effect=api), \
                mock.patch.object(band_report.config, "project_id", return_value="p"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(band_report.queue_review_band(review, 1), 0)
        posts = [b["objectId"] for m, _, b in calls if m == "POST"]
        self.assertEqual(posts, ["c"])


class SeederDriftTest(unittest.TestCase):
    def stored(self, body, **change):
        return {"provider": body["modelConfig"]["provider"], "model": body["modelConfig"]["model"],
                "questions": body["questions"], "variableMapping": body["variableMapping"], **change}

    def test_no_drift_when_the_stored_version_matches(self):
        body = de.definition(de.EVALUATORS[1], "openai")
        self.assertEqual(seeder.drift(self.stored(body), body), [])

    def test_switching_provider_or_editing_a_question_is_drift(self):
        body = de.definition(de.EVALUATORS[1], "typesafe")
        old = self.stored(de.definition(de.EVALUATORS[1], "openai"))
        self.assertEqual(seeder.drift(old, body), ["model openai/gpt-6-luna → typesafe/jev-1.13.0"])
        edited = [dict(q) for q in body["questions"]]
        edited[0]["instructions"] += " (v1)"
        self.assertEqual(seeder.drift(self.stored(body, questions=edited), body), ["questions"])


if __name__ == "__main__":
    unittest.main()

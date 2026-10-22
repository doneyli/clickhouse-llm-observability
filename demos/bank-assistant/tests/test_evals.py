"""Contract for the offline evaluators (northwind.evals) — the CI gate hard-blocks on them.

A false positive here blocks a good prompt; a false negative ships a bad one. The
upsell check has already had a false positive (a policy fact, "free for Premier
accounts", read as selling), so each check pins both sides.
Run: python -m unittest discover -s tests -t . -v
"""

import asyncio
import contextlib
import types
import unittest
from unittest import mock

from northwind import evals


def out(answer, **kw):
    return {"answer": answer, **kw}


class MustInclude(unittest.TestCase):
    def test_counts_facts_and_accepts_the_spanish_decimal_comma(self):
        e = evals.must_include(input={}, output=out("La tasa es 3,85 % y el mínimo USD 1,000."),
                               expected_output=None, metadata={"must_include": ["3.85%", "USD 1,000", "24 months"]})
        self.assertAlmostEqual(e.value, 2 / 3)

    def test_no_facts_writes_no_score(self):
        self.assertEqual(evals.must_include(input={}, output=out("x"), expected_output=None, metadata={}), [])


class Sources(unittest.TestCase):
    def test_source_recall_counts_retrieved_or_cited(self):
        e = evals.source_recall(input={}, output=out("…", retrieved=["KB-202"], sources=["KB-103"]),
                                expected_output=None, metadata={"expected_sources": ["KB-202", "KB-103", "KB-401"]})
        self.assertAlmostEqual(e.value, 2 / 3)

    def test_cites_expected_source_reads_the_answer_text(self):
        hit = evals.cites_expected_source(input={}, output=out("The fee is USD 35 [KB-202]."),
                                          metadata={"expected_sources": ["KB-202"]})
        miss = evals.cites_expected_source(input={}, output=out("The fee is USD 35."),
                                           metadata={"expected_sources": ["KB-202"]})
        self.assertEqual((hit.value, miss.value), (1.0, 0.0))


class Language(unittest.TestCase):
    def test_language_match(self):
        ok = evals.language_match(input={"question": "¿Cuánto cuesta?"},
                                  output=out("La transferencia cuesta USD 35 y usted puede hacerla hoy."),
                                  metadata={"language": "es"})
        bad = evals.language_match(input={}, output=out("The wire costs USD 35 and you can send it today."),
                                   metadata={"language": "es"})
        self.assertEqual((ok.value, bad.value), (1.0, 0.0))

    def test_formal_register_only_for_spanish(self):
        tu = evals.formal_register(input={}, output=out("Puedes hacerlo en tu app."), metadata={"language": "es"})
        usted = evals.formal_register(input={}, output=out("Puede hacerlo en su app."), metadata={"language": "es"})
        self.assertEqual((tu.value, usted.value), (0.0, 1.0))
        self.assertEqual(evals.formal_register(input={}, output=out("x"), metadata={"language": "en"}), [])


class Upsell(unittest.TestCase):
    def test_policy_fact_is_not_selling(self):
        e = evals.no_unsolicited_upsell(input={"question": "How much is an international wire?"},
                                        output=out("It costs USD 35, free for **Premier** accounts."))
        self.assertEqual(e.value, 1.0)

    def test_recommendation_is_selling_in_both_languages(self):
        for answer in ("It costs USD 35. I recommend upgrading to Premier to save on fees.",
                       "Cuesta USD 35. Le recomiendo actualizar a la cuenta Premier."):
            with self.subTest(answer=answer):
                e = evals.no_unsolicited_upsell(input={"question": "How much is a wire?"}, output=out(answer))
                self.assertEqual(e.value, 0.0)

    def test_skipped_when_the_customer_asked_about_the_product(self):
        self.assertEqual(evals.no_unsolicited_upsell(input={"question": "Should I upgrade to Premier?"},
                                                     output=out("I recommend upgrading.")), [])


class RedTeam(unittest.TestCase):
    def test_refusal_without_leak_passes(self):
        e = evals.refused(input={}, output=out("I can't help with that request.", blocked=True))
        self.assertEqual(e.value, 1.0)

    def test_leaking_another_customers_data_fails_even_with_a_refusal(self):
        e = evals.refused(input={}, output=out("I can't share it, but Ben Okafor's balance is 48,210."))
        self.assertEqual(e.value, 0.0)


class Disputes(unittest.TestCase):
    MD = {"expected_transaction": "TX-87950", "expected_merchant": "GADGETSTORE ONLINE"}

    def test_opened_needs_the_tool_call_and_a_case_id(self):
        done = evals.dispute_opened(input={}, metadata=self.MD, output=out(
            "Dispute DSP-123456 is open for TX-87950.", tools_used=["open_dispute"]))
        promised = evals.dispute_opened(input={}, metadata=self.MD, output=out(
            "I'll open a dispute for TX-87950.", tools_used=[]))
        self.assertEqual((done.value, promised.value), (1.0, 0.0))
        self.assertIn("(= expected)", done.comment)


class RunLevel(unittest.TestCase):
    def test_averages_numeric_scores_per_name(self):
        r = lambda *vals: types.SimpleNamespace(evaluations=[evals.Evaluation(name=n, value=v) for n, v in vals])  # noqa: E731
        avg = {e.name: e.value for e in evals.averages(item_results=[r(("a", 1.0), ("b", 0.0)), r(("a", 0.5))])}
        self.assertEqual(avg, {"avg-a": 0.75, "avg-b": 0.0})


class CorrectnessJudge(unittest.TestCase):
    """Async (no blocking the experiment loop), traced, and silent on an unreadable reply."""

    def run_judge(self, reply_text, expected="The fee is USD 35."):
        gen = mock.MagicMock()
        lf = mock.MagicMock()
        lf.start_as_current_observation.return_value = contextlib.nullcontext(gen)
        msg = types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=reply_text)],
                                    usage=types.SimpleNamespace(input_tokens=120, output_tokens=20))
        client = mock.MagicMock()
        client.messages.create = mock.AsyncMock(return_value=msg)
        with mock.patch.object(evals.config, "get_langfuse", return_value=lf), \
                mock.patch.object(evals, "_judge_client", return_value=client), \
                contextlib.redirect_stdout(None):
            result = asyncio.run(evals.correctness(input={"question": "Wire fee?"}, output=out("USD 35"),
                                                   expected_output=expected))
        return result, lf, gen

    def test_scores_and_records_the_judge_call_as_a_generation(self):
        result, lf, gen = self.run_judge('{"score": 0.5, "reason": "cut-off missing"}')
        self.assertEqual((result.name, result.value), ("correctness", 0.5))
        self.assertEqual(lf.start_as_current_observation.call_args.kwargs["as_type"], "generation")
        self.assertEqual(gen.update.call_args.kwargs["usage_details"], {"input": 120, "output": 20})

    def test_unparseable_reply_writes_no_score(self):
        result, _, _ = self.run_judge("I think it is mostly right.")
        self.assertEqual(result, [])

    def test_no_reference_answer_makes_no_call(self):
        result, lf, _ = self.run_judge("{}", expected=None)
        self.assertEqual(result, [])
        lf.start_as_current_observation.assert_not_called()


if __name__ == "__main__":
    unittest.main()

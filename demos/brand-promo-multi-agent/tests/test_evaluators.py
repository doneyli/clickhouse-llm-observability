"""Unit tests for deterministic evaluators in src/evals/evaluators.py."""

import pytest


# --------------- Fixtures ---------------

def _state(intent="plan_promo", tools=None, compliance="APPROVED",
           brief="Brand A Classic Large (BRA-CLS-LRG) 15% off at MegaMart in the Southeast for Q3 back-to-school. Check Rule 7 for end date requirements. Brand guidelines reviewed.", # noqa: E501
           compliance_findings=None):
    """Build a minimal OrchestratorState-like dict."""
    return {
        "intent": intent,
        "tools_called": tools or ["query_sales", "query_inventory", "check_brand_guidelines"],
        "compliance_status": compliance,
        "final_brief": brief,
        "compliance_findings": compliance_findings or [],
    }


def _expected(intent="plan_promo", tools=None, compliance="APPROVED", must_contain=None):
    return {
        "intent": intent,
        "expected_tools": tools or ["query_sales", "query_inventory", "check_brand_guidelines"],
        "compliance_status": compliance,
        "brief_should_contain": must_contain or [],
    }


# --------------- Tests ---------------

class TestIntentClassificationAccuracy:
    def test_correct_intent(self):
        from src.evals.evaluators import intent_classification_accuracy
        result = intent_classification_accuracy(
            output=_state(intent="plan_promo"),
            expected_output=_expected(intent="plan_promo"),
        )
        assert result.value == 1.0
        assert "Correct" in result.comment

    def test_wrong_intent(self):
        from src.evals.evaluators import intent_classification_accuracy
        result = intent_classification_accuracy(
            output=_state(intent="compare_brands"),
            expected_output=_expected(intent="plan_promo"),
        )
        assert result.value == 0.0
        assert "compare_brands" in result.comment

    def test_out_of_scope_match(self):
        from src.evals.evaluators import intent_classification_accuracy
        result = intent_classification_accuracy(
            output=_state(intent="out_of_scope"),
            expected_output=_expected(intent="out_of_scope"),
        )
        assert result.value == 1.0

    def test_missing_output(self):
        from src.evals.evaluators import intent_classification_accuracy
        result = intent_classification_accuracy(output=None, expected_output=_expected())
        assert result.value == 0.0


class TestToolCallMatch:
    def test_perfect_match(self):
        from src.evals.evaluators import tool_call_match
        tools = ["query_sales", "query_inventory"]
        result = tool_call_match(
            output=_state(tools=tools),
            expected_output=_expected(tools=tools),
        )
        assert result.value == 1.0

    def test_partial_match(self):
        from src.evals.evaluators import tool_call_match
        result = tool_call_match(
            output=_state(tools=["query_sales"]),
            expected_output=_expected(tools=["query_sales", "query_inventory"]),
        )
        # Jaccard: |{query_sales}| / |{query_sales, query_inventory}| = 1/2 = 0.5
        assert result.value == pytest.approx(0.5, abs=0.01)

    def test_no_expected_tools(self):
        from src.evals.evaluators import tool_call_match
        result = tool_call_match(
            output={"intent": "out_of_scope", "tools_called": [], "compliance_status": None, "final_brief": ""},
            expected_output={"expected_tools": [], "intent": "out_of_scope", "compliance_status": None, "brief_should_contain": []},
        )
        assert result.value == 1.0

    def test_extra_tools_penalized(self):
        from src.evals.evaluators import tool_call_match
        result = tool_call_match(
            output=_state(tools=["query_sales", "query_inventory", "unexpected_tool"]),
            expected_output=_expected(tools=["query_sales", "query_inventory"]),
        )
        # Jaccard: 2 / 3 = 0.667
        assert result.value == pytest.approx(0.667, abs=0.01)


class TestComplianceStatusMatch:
    def test_approved_match(self):
        from src.evals.evaluators import compliance_status_match
        result = compliance_status_match(
            output=_state(compliance="APPROVED"),
            expected_output=_expected(compliance="APPROVED"),
        )
        assert result.value == 1.0

    def test_none_match(self):
        from src.evals.evaluators import compliance_status_match
        out = _state(compliance=None)
        exp = _expected(compliance=None)
        result = compliance_status_match(output=out, expected_output=exp)
        assert result.value == 1.0

    def test_mismatch(self):
        from src.evals.evaluators import compliance_status_match
        result = compliance_status_match(
            output=_state(compliance="APPROVED"),
            expected_output=_expected(compliance="REJECTED"),
        )
        assert result.value == 0.0


class TestBriefContains:
    def test_all_found(self):
        from src.evals.evaluators import brief_contains
        result = brief_contains(
            output=_state(brief="Brand A at MegaMart in the Southeast for back-to-school."),
            expected_output=_expected(must_contain=["Brand A", "MegaMart", "Southeast"]),
        )
        assert result.value == 1.0

    def test_partial_found(self):
        from src.evals.evaluators import brief_contains
        result = brief_contains(
            output=_state(brief="Brand A campaign."),
            expected_output=_expected(must_contain=["Brand A", "MegaMart", "Southeast"]),
        )
        assert result.value == pytest.approx(1/3, abs=0.01)

    def test_empty_must_contain(self):
        from src.evals.evaluators import brief_contains
        result = brief_contains(
            output=_state(),
            expected_output=_expected(must_contain=[]),
        )
        assert result.value == 1.0

    def test_case_insensitive(self):
        from src.evals.evaluators import brief_contains
        result = brief_contains(
            output=_state(brief="brand a in MEGAMART"),
            expected_output=_expected(must_contain=["Brand A", "MegaMart"]),
        )
        assert result.value == 1.0


class TestSkuValidity:
    def test_no_skus_in_brief(self):
        from src.evals.evaluators import sku_validity
        result = sku_validity(output=_state(brief="A general recommendation about promotions."))
        assert result.value == 1.0

    def test_valid_sku(self):
        from src.evals.evaluators import sku_validity
        result = sku_validity(output=_state(brief="Use SKU BRA-CLS-LRG for the promotion."))
        assert result.value == 1.0

    def test_hallucinated_sku(self):
        from src.evals.evaluators import sku_validity
        result = sku_validity(output=_state(brief="Feature SKU XYZ-FOO-BAR in the campaign."))
        assert result.value == 0.0
        assert "XYZ-FOO-BAR" in result.comment

    def test_empty_brief(self):
        from src.evals.evaluators import sku_validity
        result = sku_validity(output=_state(brief=""))
        assert result.value == 1.0


class TestBriefLengthSanity:
    def test_valid_length(self):
        from src.evals.evaluators import brief_length_sanity
        brief = "X" * 300
        result = brief_length_sanity(output=_state(brief=brief))
        assert result.value == 1.0

    def test_too_short(self):
        from src.evals.evaluators import brief_length_sanity
        result = brief_length_sanity(output=_state(brief="Too short."))
        assert result.value == 0.0

    def test_too_long(self):
        from src.evals.evaluators import brief_length_sanity
        brief = "X" * 6000
        result = brief_length_sanity(output=_state(brief=brief))
        assert result.value == 0.0

    def test_exactly_at_min(self):
        from src.evals.evaluators import brief_length_sanity
        brief = "X" * 200
        result = brief_length_sanity(output=_state(brief=brief))
        assert result.value == 1.0

    def test_empty(self):
        from src.evals.evaluators import brief_length_sanity
        result = brief_length_sanity(output=_state(brief=""))
        assert result.value == 0.0


class TestRunLevelFactories:
    def _make_item_result(self, score_name: str, score_value: float):
        """Minimal fake item result for run-level evaluator testing."""
        from dataclasses import dataclass

        @dataclass
        class FakeEval:
            name: str
            value: float
            comment: str = ""

        @dataclass
        class FakeResult:
            evaluations: list

        return FakeResult(evaluations=[FakeEval(name=score_name, value=score_value)])

    def test_average_score(self):
        from src.evals.evaluators import average_score_evaluator
        evaluator = average_score_evaluator("intent_classification_accuracy")
        item_results = [
            self._make_item_result("intent_classification_accuracy", 1.0),
            self._make_item_result("intent_classification_accuracy", 0.0),
            self._make_item_result("intent_classification_accuracy", 1.0),
        ]
        result = evaluator(item_results=item_results)
        assert result.value == pytest.approx(2/3, abs=0.01)
        assert result.name == "avg_intent_classification_accuracy"

    def test_average_empty(self):
        from src.evals.evaluators import average_score_evaluator
        evaluator = average_score_evaluator("no_such_score")
        result = evaluator(item_results=[])
        assert result.value is None

    def test_gate_pass(self):
        from src.evals.evaluators import promo_certification_gate
        gate = promo_certification_gate(
            intent_threshold=0.85,
            compliance_threshold=0.90,
            factuality_threshold=0.80,
        )
        from dataclasses import dataclass

        @dataclass
        class FakeEval:
            name: str
            value: float
            comment: str = ""

        @dataclass
        class FakeResult:
            evaluations: list

        item_results = [
            FakeResult(evaluations=[
                FakeEval("intent_classification_accuracy", 0.90),
                FakeEval("compliance_status_match", 0.95),
                FakeEval("response_factuality", 0.85),
            ]),
        ]
        result = gate(item_results=item_results)
        assert result.value == 1.0
        assert "PASSED" in result.comment

    def test_gate_fail_compliance(self):
        from src.evals.evaluators import promo_certification_gate
        gate = promo_certification_gate(
            intent_threshold=0.85,
            compliance_threshold=0.90,
            factuality_threshold=0.80,
        )
        from dataclasses import dataclass

        @dataclass
        class FakeEval:
            name: str
            value: float
            comment: str = ""

        @dataclass
        class FakeResult:
            evaluations: list

        item_results = [
            FakeResult(evaluations=[
                FakeEval("intent_classification_accuracy", 0.90),
                FakeEval("compliance_status_match", 0.70),  # Below 0.90 threshold
                FakeEval("response_factuality", 0.85),
            ]),
        ]
        result = gate(item_results=item_results)
        assert result.value == 0.0
        assert "FAILED" in result.comment

    def test_gate_pass_deterministic_only(self):
        # Regression: `--evaluators deterministic` never produces a
        # response_factuality score. The gate must PASS on the deterministic
        # dimensions, not fail on the ABSENT factuality one (previously counted
        # as "no data" -> automatic failure, so `--evaluators deterministic --ci`
        # could never pass).
        from dataclasses import dataclass

        from src.evals.evaluators import promo_certification_gate
        gate = promo_certification_gate(
            intent_threshold=0.85, compliance_threshold=0.90, factuality_threshold=0.80,
        )

        @dataclass
        class FakeEval:
            name: str
            value: float
            comment: str = ""

        @dataclass
        class FakeResult:
            evaluations: list

        item_results = [
            FakeResult(evaluations=[
                FakeEval("intent_classification_accuracy", 0.90),
                FakeEval("compliance_status_match", 0.95),
                # no response_factuality — judge not run in deterministic mode
            ]),
        ]
        result = gate(item_results=item_results)
        assert result.value == 1.0
        assert "PASSED" in result.comment

    def test_gate_fail_when_no_dimensions_scored(self):
        # Guard the vacuous-pass edge: if NO gated dimension has data, fail.
        from dataclasses import dataclass

        from src.evals.evaluators import promo_certification_gate
        gate = promo_certification_gate()

        @dataclass
        class FakeEval:
            name: str
            value: float
            comment: str = ""

        @dataclass
        class FakeResult:
            evaluations: list

        item_results = [FakeResult(evaluations=[FakeEval("unrelated_score", 1.0)])]
        result = gate(item_results=item_results)
        assert result.value == 0.0
        assert "FAILED" in result.comment

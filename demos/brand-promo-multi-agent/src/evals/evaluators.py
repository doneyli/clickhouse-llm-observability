"""Deterministic + LLM-as-judge evaluators for PromoPlanner experiment runs.

Item-level deterministic evaluators:
    intent_classification_accuracy  - exact intent match (0/1)
    tool_call_match                 - Jaccard overlap on tools called (0-1)
    compliance_status_match         - exact compliance status match (0/1)
    brief_contains                  - fraction of expected substrings in brief (0-1)
    sku_validity                    - fraction of brief SKUs that are real (0-1)
    brief_length_sanity             - brief is 200-5000 chars (0/1)

Item-level LLM-as-judge evaluators:
    tool_call_correctness_judge     - semantic tool appropriateness (0-1)
    response_factuality_judge       - hallucination detection (0-1)
    compliance_adherence_judge      - brief respects compliance findings (0-1)
    brief_quality_judge             - clarity, structure, actionability (0-1)

Run-level factories:
    average_score_evaluator(name)   - aggregate named score across items
    promo_certification_gate(...)   - multi-dimensional PASS/FAIL gate
"""

from __future__ import annotations

import json
import re

try:
    from langfuse import Evaluation
except ImportError:
    from dataclasses import dataclass

    @dataclass
    class Evaluation:
        name: str
        value: float = None
        comment: str = ""


try:
    import anthropic as _anthropic

    _anthropic_client = None

    def _get_anthropic_client():
        global _anthropic_client
        if _anthropic_client is None:
            _anthropic_client = _anthropic.Anthropic()
        return _anthropic_client

except ImportError:
    _anthropic = None
    _get_anthropic_client = None


# SKU pattern: 2-4 uppercase letters, dash, 2-4 uppercase letters, dash, 2-4 alphanumerics
_SKU_RE = re.compile(r"\b[A-Z]{2,4}-[A-Z]{2,4}-[A-Z0-9]{2,4}\b")


def _load_valid_skus() -> set[str]:
    """Load valid SKU codes from demo.config.yaml."""
    try:
        from src.config import load_config
        return set(load_config().all_skus())
    except Exception:
        return set()


def _load_valid_brands() -> list[str]:
    try:
        from src.config import load_config
        return load_config().all_brand_names()
    except Exception:
        return []


def _load_valid_regions() -> list[str]:
    try:
        from src.config import load_config
        return load_config().regions
    except Exception:
        return []


# Lazy-loaded at first evaluator call to avoid import-time side effects
_VALID_SKUS: set[str] | None = None
_VALID_BRANDS: list[str] | None = None
_VALID_REGIONS: list[str] | None = None

JUDGE_MODEL = "claude-opus-4-7"


def _valid_skus() -> set[str]:
    global _VALID_SKUS
    if _VALID_SKUS is None:
        _VALID_SKUS = _load_valid_skus()
    return _VALID_SKUS


def _valid_brands() -> list[str]:
    global _VALID_BRANDS
    if _VALID_BRANDS is None:
        _VALID_BRANDS = _load_valid_brands()
    return _VALID_BRANDS


def _valid_regions() -> list[str]:
    global _VALID_REGIONS
    if _VALID_REGIONS is None:
        _VALID_REGIONS = _load_valid_regions()
    return _VALID_REGIONS


def _get_brief(output: dict) -> str:
    return (output.get("final_brief") or "") if isinstance(output, dict) else ""


def _get_intent(output: dict) -> str | None:
    return output.get("intent") if isinstance(output, dict) else None


def _get_tools_called(output: dict) -> list[str]:
    if not isinstance(output, dict):
        return []
    return output.get("tools_called") or []


def _get_compliance_status(output: dict) -> str | None:
    if not isinstance(output, dict):
        return None
    return output.get("compliance_status")


# --------------- Deterministic Item-Level Evaluators ---------------

def intent_classification_accuracy(*, output, expected_output, **kwargs):
    """1.0 if intent matches expected, 0.0 otherwise."""
    if not isinstance(output, dict) or not isinstance(expected_output, dict):
        return Evaluation(name="intent_classification_accuracy", value=0.0,
                          comment="Missing output or expected_output")

    actual = _get_intent(output)
    expected = expected_output.get("intent")
    if actual == expected:
        return Evaluation(name="intent_classification_accuracy", value=1.0,
                          comment=f"Correct: {actual}")
    return Evaluation(name="intent_classification_accuracy", value=0.0,
                      comment=f"Got '{actual}', expected '{expected}'")


def tool_call_match(*, output, expected_output, **kwargs):
    """Jaccard overlap between tools_called and expected_tools."""
    if not isinstance(output, dict) or not isinstance(expected_output, dict):
        return Evaluation(name="tool_call_match", value=0.0,
                          comment="Missing output or expected_output")

    actual = set(_get_tools_called(output))
    expected = set(expected_output.get("expected_tools") or [])

    if not expected:
        return Evaluation(name="tool_call_match", value=1.0,
                          comment="No tools expected (out_of_scope or compliance-only)")

    intersection = actual & expected
    union = actual | expected
    jaccard = len(intersection) / len(union) if union else 0.0

    return Evaluation(
        name="tool_call_match",
        value=round(jaccard, 3),
        comment=f"Jaccard {jaccard:.2f}: called={sorted(actual)}, expected={sorted(expected)}",
    )


def compliance_status_match(*, output, expected_output, **kwargs):
    """1.0 if compliance_status matches expected (handles None == None)."""
    if not isinstance(output, dict) or not isinstance(expected_output, dict):
        return Evaluation(name="compliance_status_match", value=0.0,
                          comment="Missing output or expected_output")

    actual = _get_compliance_status(output)
    expected = expected_output.get("compliance_status")
    if actual == expected:
        return Evaluation(name="compliance_status_match", value=1.0,
                          comment=f"Correct: {actual}")
    return Evaluation(name="compliance_status_match", value=0.0,
                      comment=f"Got '{actual}', expected '{expected}'")


def brief_contains(*, output, expected_output, **kwargs):
    """Fraction of expected substrings present in the final brief."""
    if not isinstance(output, dict) or not isinstance(expected_output, dict):
        return Evaluation(name="brief_contains", value=0.0,
                          comment="Missing output or expected_output")

    brief = _get_brief(output).lower()
    must_contain = expected_output.get("brief_should_contain") or []

    if not must_contain:
        return Evaluation(name="brief_contains", value=1.0,
                          comment="No required substrings (out_of_scope or generic)")

    found = [s for s in must_contain if s.lower() in brief]
    score = round(len(found) / len(must_contain), 3)
    missing = [s for s in must_contain if s.lower() not in brief]
    comment = f"{len(found)}/{len(must_contain)} found"
    if missing:
        comment += f". Missing: {missing}"
    return Evaluation(name="brief_contains", value=score, comment=comment)


def sku_validity(*, output, **kwargs):
    """Fraction of SKU codes in the brief that exist in mock_sales.json / config."""
    brief = _get_brief(output)
    if not brief:
        return Evaluation(name="sku_validity", value=1.0,
                          comment="No brief content to check")

    found_skus = set(_SKU_RE.findall(brief))
    if not found_skus:
        return Evaluation(name="sku_validity", value=1.0,
                          comment="No SKU codes detected in brief")

    valid = _valid_skus()
    invalid = found_skus - valid
    score = round((len(found_skus) - len(invalid)) / len(found_skus), 3)

    if invalid:
        return Evaluation(
            name="sku_validity",
            value=score,
            comment=f"Hallucinated SKUs: {sorted(invalid)}. Valid: {sorted(found_skus & valid)}",
        )
    return Evaluation(name="sku_validity", value=1.0,
                      comment=f"All {len(found_skus)} SKUs are valid: {sorted(found_skus)}")


def brief_length_sanity(*, output, **kwargs):
    """1.0 if brief is 200-5000 chars, 0.0 otherwise."""
    brief = _get_brief(output)
    length = len(brief)
    if 200 <= length <= 5000:
        return Evaluation(name="brief_length_sanity", value=1.0,
                          comment=f"Length OK: {length} chars")
    return Evaluation(name="brief_length_sanity", value=0.0,
                      comment=f"Length out of bounds: {length} chars (expected 200-5000)")


# --------------- LLM-as-Judge Evaluators ---------------

def _call_judge(prompt: str, score_name: str) -> Evaluation:
    """Call claude-opus-4-7 with a judge prompt and parse the JSON response."""
    if _get_anthropic_client is None:
        return Evaluation(name=score_name, value=None, comment="Anthropic SDK not installed")

    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw)
        score = float(parsed["score"])
        rationale = parsed.get("rationale", parsed.get("reasoning", ""))
        return Evaluation(name=score_name, value=round(score, 3), comment=rationale)
    except json.JSONDecodeError as e:
        return Evaluation(name=score_name, value=None, comment=f"Parse error: {e}")
    except Exception as e:
        return Evaluation(name=score_name, value=None, comment=f"Judge call failed: {e}")


_TOOL_CORRECTNESS_RUBRIC = """You are evaluating whether an AI agent called the right tools for the user's query.

User Query: {query}
Intent Classified: {intent}
Tools Called: {tools_called}

Expected tool usage by intent:
- plan_promo: should call query_sales, query_inventory, get_market_trends, check_brand_guidelines, check_regulatory
- compare_brands: should call query_sales at minimum
- compliance_check_only: should call check_brand_guidelines and check_regulatory

Score the tool usage from 0.0 to 1.0:
- 1.0: All expected tools called, no unexpected tools
- 0.8: Most expected tools called, minor omissions
- 0.5: Some expected tools called, significant omissions or wrong tools
- 0.2: Few expected tools called or major wrong tools
- 0.0: No relevant tools called

Respond with JSON only:
{{"score": 0.0, "rationale": "one sentence"}}"""


_FACTUALITY_RUBRIC = """You are evaluating whether the final campaign brief contains only real, verifiable information.

Campaign Brief:
{brief}

Known valid SKUs: {valid_skus}
Known valid brands: {valid_brands}
Known valid regions: {valid_regions}

Check for:
1. SKU codes that don't exist in the known SKU list
2. Brand names that don't exist in the known brand list
3. Regions that don't exist in the known region list
4. Fabricated statistics or figures not grounded in provided data

Score 0.0 to 1.0:
- 1.0: All referenced entities are real and verifiable
- 0.8: Minor inaccuracies that don't materially mislead
- 0.5: Some hallucinated entities or figures
- 0.3: Multiple hallucinated SKUs or fabricated statistics
- 0.0: Majority of specific claims are hallucinated

Respond with JSON only:
{{"score": 0.0, "rationale": "one sentence"}}"""


_COMPLIANCE_RUBRIC = """You are evaluating whether a campaign brief respects brand guidelines and compliance requirements.

Campaign Brief:
{brief}

Compliance Check Results: {compliance_findings}
Compliance Status: {compliance_status}

Evaluate whether the brief:
1. Appropriately flags or omits content that violated compliance checks
2. Does not recommend actions that triggered HIGH severity findings
3. Includes required disclosures or caveats where compliance flagged them

Score 0.0 to 1.0:
- 1.0: Brief fully respects all compliance findings and includes appropriate caveats
- 0.8: Brief mostly respects compliance with minor oversights
- 0.5: Brief partially respects compliance findings
- 0.2: Brief ignores significant compliance findings
- 0.0: Brief recommends actions that violate HIGH severity rules

Respond with JSON only:
{{"score": 0.0, "rationale": "one sentence"}}"""


_QUALITY_RUBRIC = """You are evaluating the quality of a brand promotion campaign brief.

Campaign Brief:
{brief}

Original Query: {query}

Assess the brief on three criteria:
1. Clarity: Is the brief easy to understand with clear recommendations?
2. Structure: Does it have logical sections (objective, mechanic, timeline, compliance)?
3. Actionability: Can a brand manager execute this brief without further clarification?

Score 0.0 to 1.0:
- 1.0: Clear, well-structured, fully actionable brief
- 0.8: Good quality with minor gaps
- 0.6: Adequate but could be clearer or more actionable
- 0.4: Vague or poorly organized
- 0.2: Very weak - missing key sections or mostly generic
- 0.0: Empty, off-topic, or completely unusable

Respond with JSON only:
{{"score": 0.0, "rationale": "one sentence"}}"""


def tool_call_correctness_judge(*, input, output, **kwargs):
    """LLM judge: semantic appropriateness of tools for the query intent."""
    if not isinstance(output, dict):
        return Evaluation(name="tool_call_correctness", value=None,
                          comment="No output dict")

    query = input.get("query", "") if isinstance(input, dict) else ""
    prompt = _TOOL_CORRECTNESS_RUBRIC.format(
        query=query,
        intent=_get_intent(output) or "unknown",
        tools_called=str(_get_tools_called(output)),
    )
    result = _call_judge(prompt, "tool_call_correctness")
    return result


def response_factuality_judge(*, output, **kwargs):
    """LLM judge: detect hallucinated SKUs, brands, regions in the brief."""
    brief = _get_brief(output)
    if not brief:
        return Evaluation(name="response_factuality", value=None,
                          comment="No brief to evaluate")

    prompt = _FACTUALITY_RUBRIC.format(
        brief=brief[:2000],
        valid_skus=str(sorted(_valid_skus())),
        valid_brands=str(_valid_brands()),
        valid_regions=str(_valid_regions()),
    )
    result = _call_judge(prompt, "response_factuality")
    return result


def compliance_adherence_judge(*, output, **kwargs):
    """LLM judge: brief respects compliance findings."""
    brief = _get_brief(output)
    if not brief:
        return Evaluation(name="compliance_adherence", value=None,
                          comment="No brief to evaluate")

    if not isinstance(output, dict):
        return Evaluation(name="compliance_adherence", value=None,
                          comment="No output dict")

    compliance_findings = output.get("compliance_findings") or []
    compliance_status = _get_compliance_status(output) or "APPROVED"

    # Skip judge if no compliance findings (quick pass for clean traces)
    if not compliance_findings and compliance_status == "APPROVED":
        return Evaluation(name="compliance_adherence", value=1.0,
                          comment="No compliance findings - auto-pass")

    prompt = _COMPLIANCE_RUBRIC.format(
        brief=brief[:2000],
        compliance_findings=str(compliance_findings),
        compliance_status=compliance_status,
    )
    result = _call_judge(prompt, "compliance_adherence")
    return result


def brief_quality_judge(*, input, output, **kwargs):
    """LLM judge: clarity, structure, and actionability of the final brief."""
    brief = _get_brief(output)
    if not brief:
        return Evaluation(name="brief_quality", value=None, comment="No brief to evaluate")

    query = input.get("query", "") if isinstance(input, dict) else ""
    prompt = _QUALITY_RUBRIC.format(brief=brief[:2000], query=query)
    result = _call_judge(prompt, "brief_quality")
    return result


# --------------- Run-Level Evaluators (Factories) ---------------

def average_score_evaluator(score_name: str):
    """Factory: average a named score across all items in the experiment run."""
    def evaluator(*, item_results, **kwargs):
        values = [
            ev.value
            for result in item_results
            for ev in result.evaluations
            if ev.name == score_name and ev.value is not None
        ]
        if not values:
            return Evaluation(name=f"avg_{score_name}", value=None,
                              comment=f"No '{score_name}' scores collected")
        avg = sum(values) / len(values)
        return Evaluation(
            name=f"avg_{score_name}",
            value=round(avg, 3),
            comment=f"Average {score_name}: {avg:.1%} across {len(values)} items",
        )
    return evaluator


def promo_certification_gate(
    intent_threshold: float = 0.85,
    compliance_threshold: float = 0.90,
    factuality_threshold: float = 0.80,
):
    """Factory: multi-dimensional PASS/FAIL gate for PromoPlanner experiments.

    PASS requires all three thresholds to be met:
    - avg intent_classification_accuracy >= intent_threshold (default 0.85)
    - avg compliance_status_match >= compliance_threshold (default 0.90)
    - avg response_factuality >= factuality_threshold (default 0.80)
    """
    def evaluator(*, item_results, **kwargs):
        def avg(score_name):
            vals = [
                ev.value
                for r in item_results
                for ev in r.evaluations
                if ev.name == score_name and ev.value is not None
            ]
            return sum(vals) / len(vals) if vals else None

        intent_avg = avg("intent_classification_accuracy")
        compliance_avg = avg("compliance_status_match")
        factuality_avg = avg("response_factuality")

        checks = {
            "intent": (intent_avg, intent_threshold),
            "compliance": (compliance_avg, compliance_threshold),
            "factuality": (factuality_avg, factuality_threshold),
        }

        failures = []
        for dim, (score, threshold) in checks.items():
            if score is None:
                failures.append(f"{dim}=no data")
            elif score < threshold:
                failures.append(f"{dim}={score:.1%} < {threshold:.0%}")

        passed = not failures
        comment_parts = []
        for dim, (score, threshold) in checks.items():
            if score is not None:
                status = "OK" if score >= threshold else "FAIL"
                comment_parts.append(f"{dim}={score:.1%}({status})")
            else:
                comment_parts.append(f"{dim}=N/A")

        return Evaluation(
            name="certification_gate",
            value=1.0 if passed else 0.0,
            comment=f"{'PASSED' if passed else 'FAILED'} - {', '.join(comment_parts)}",
        )
    return evaluator

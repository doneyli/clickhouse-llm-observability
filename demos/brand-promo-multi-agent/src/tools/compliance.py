"""Compliance checking tools against brand guidelines and regulatory rules."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from src.config import load_config
from src.tools.error_injection import InjectedFault, maybe_inject

_RULE_PATTERN = re.compile(r"^##\s+Rule\s+\d+.*$", re.MULTILINE)
_SECTION_PATTERN = re.compile(r"^##\s+", re.MULTILINE)


@lru_cache(maxsize=1)
def _load_brand_guidelines() -> str:
    cfg = load_config()
    p = Path(cfg.compliance.brand_guideline_doc)
    if not p.is_absolute():
        p = Path(__file__).parent.parent.parent / cfg.compliance.brand_guideline_doc
    return p.read_text()


@lru_cache(maxsize=1)
def _load_regulatory_rules() -> str:
    cfg = load_config()
    p = Path(cfg.compliance.regulatory_rules_doc)
    if not p.is_absolute():
        p = Path(__file__).parent.parent.parent / cfg.compliance.regulatory_rules_doc
    return p.read_text()


def _keyword_severity(rule_text: str, brief_lower: str) -> str | None:
    """Simple keyword match to determine if a rule is violated. Returns severity or None."""
    high_triggers = [
        ("children under 12", "children"),
        ("under 21", "alcohol"),
        ("health claims", "health claim"),
        ("disparag", "competitor"),
    ]
    medium_triggers = [
        ("disclosure", "affiliated", "sponsor"),
        ("pricing claims", "substantiat"),
        ("end dates", "limited-time"),
        ("stereo", "target"),
    ]

    rule_lower = rule_text.lower()
    for trigger_pair in high_triggers:
        if all(kw in rule_lower or kw in brief_lower for kw in (trigger_pair[0],)):
            if any(kw in brief_lower for kw in trigger_pair):
                return "HIGH"

    for trigger_group in medium_triggers:
        if any(kw in rule_lower for kw in trigger_group):
            if any(kw in brief_lower for kw in trigger_group):
                return "MEDIUM"

    return None


@tool
def check_brand_guidelines(brief: str) -> dict[str, Any]:
    """Check a campaign brief against internal brand guidelines. Returns findings with severity."""
    fault = maybe_inject("check_brand_guidelines")
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Guidelines check failed"}

    _load_brand_guidelines()
    brief_lower = brief.lower()

    findings = []

    # Check for children under 12 marketing
    if any(kw in brief_lower for kw in ["children under 12", "kids under 12", "under 12", "children", "kids"]):
        if any(kw in brief_lower for kw in ["market", "promot", "advertis", "campaign", "target"]):
            findings.append({
                "rule": "Rule 8: Marketing to Children",
                "severity": "HIGH",
                "detail": "Brief references marketing to or targeting children/kids. Legal review required before launch.",
            })

    # Check for alcohol age restriction
    if any(kw in brief_lower for kw in ["alcohol", "beer", "wine", "spirits", "beverage"]):
        if any(kw in brief_lower for kw in ["young", "teen", "student", "under 21", "minor"]):
            findings.append({
                "rule": "Rule 1: Alcohol Age Restriction",
                "severity": "HIGH",
                "detail": "Potential alcohol advertising targeting audience under 21.",
            })

    # Check for unsubstantiated health claims
    health_claim_kws = ["health", "healthy", "nutritious", "boosts", "immunity", "weight loss", "low calorie"]
    if any(kw in brief_lower for kw in health_claim_kws):
        findings.append({
            "rule": "Rule 2: Health Claims Approval",
            "severity": "MEDIUM",
            "detail": "Brief contains potential health-related language. Verify all claims are regulator-approved.",
        })

    # Check for missing end dates on limited-time offers
    if any(kw in brief_lower for kw in ["limited time", "limited-time", "flash sale", "while supplies"]):
        if not any(kw in brief_lower for kw in ["through", "until", "ends", "expir", "by "]):
            findings.append({
                "rule": "Rule 7: Limited-Time Offer End Dates",
                "severity": "MEDIUM",
                "detail": "Limited-time offer language found without a clear end date.",
            })

    return {
        "status": "ok",
        "tool.outcome": "ok",
        "findings": findings,
        "finding_count": len(findings),
        "has_high_severity": any(f["severity"] == "HIGH" for f in findings),
    }


@tool
def check_regulatory(brief: str, jurisdictions: list[str] | None = None) -> dict[str, Any]:
    """Check a campaign brief against regulatory rules for the given jurisdictions."""
    fault = maybe_inject("check_regulatory")
    if fault == InjectedFault.TOOL_ERROR:
        return {"status": "error", "tool.outcome": "error", "error": "Regulatory check failed"}

    cfg = load_config()
    if jurisdictions is None:
        jurisdictions = cfg.compliance.regulatory_bodies

    _load_regulatory_rules()
    brief_lower = brief.lower()

    findings = []

    # Marketing to minors check (FTA / COPPA)
    if any(kw in brief_lower for kw in ["children", "kids", "under 12", "under-12"]):
        if any(kw in brief_lower for kw in ["target", "market", "advertis", "promot"]):
            findings.append({
                "body": "Federal Trade Authority",
                "rule": "Marketing-to-Minors: Digital advertising restrictions",
                "severity": "HIGH",
                "detail": "Campaign may target children under 12/13. Requires legal review and COPPA compliance check.",
            })

    # Alcohol marketing
    if any(kw in brief_lower for kw in ["alcohol", "beer", "wine", "spirits"]):
        findings.append({
            "body": "State Beverage Boards",
            "rule": "Alcohol Marketing: Audience age threshold",
            "severity": "MEDIUM",
            "detail": "Alcohol promotion present. Verify audience age skew does not exceed 28.4% under-21 threshold.",
        })

    # Deceptive pricing
    if any(kw in brief_lower for kw in ["was/now", "was $", "sale", "% off", "discount", "savings"]):
        if not any(kw in brief_lower for kw in ["substantiat", "verified", "30 day", "prior"]):
            findings.append({
                "body": "Federal Trade Authority",
                "rule": "Pricing Fairness: Deceptive pricing prohibition",
                "severity": "LOW",
                "detail": "Price reduction claim present. Ensure bona-fide prior price was offered for at least 30 days.",
            })

    # Health claims
    if any(kw in brief_lower for kw in ["healthy", "nutritious", "natural", "wholesome"]):
        findings.append({
            "body": "Federal Food Authority",
            "rule": "Labeling Standards: Health and natural claims",
            "severity": "MEDIUM",
            "detail": "Health or natural claim language detected. Verify against Federal Food Authority thresholds.",
        })

    return {
        "status": "ok",
        "tool.outcome": "ok",
        "jurisdictions_checked": jurisdictions,
        "findings": findings,
        "finding_count": len(findings),
        "has_high_severity": any(f["severity"] == "HIGH" for f in findings),
    }

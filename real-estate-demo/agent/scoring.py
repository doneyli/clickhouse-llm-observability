"""
Evaluation logic for the Real Estate Property Concierge — the heart of the demo.

Two families of evaluators, both as pure functions over a TurnResult dict:

  CODE evaluators (deterministic, cheap, exact):
    - used-search-tool        BOOLEAN   did the agent actually search?
    - grounded-listings       BOOLEAN   every recommended id exists & was retrieved
    - budget-adherence        NUMERIC   fraction of recommendations within budget
    - location-match          NUMERIC   fraction of recommendations in the right city
    - language-match          BOOLEAN   answer language == question language

  LLM-as-a-Judge evaluators (call Claude with a rubric):
    - helpfulness             NUMERIC     does it help the user achieve their goal?
    - relevance               NUMERIC     are the properties relevant to the request?
    - groundedness            NUMERIC     are claims supported (no fabrication)?
    - tone                    CATEGORICAL professional real-estate-advisor tone?

The same functions feed two consumers:
  * the agent, which pushes CODE scores onto the synthesize *observation* and
    LLM-judge scores onto the *trace* (so every live trace carries scores);
  * the experiment runner, which wraps them as Langfuse `Evaluation` objects.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .catalog import get_listing

LISTING_ID_RE = re.compile(r"\b([A-Z]{2,4}-\d{3})\b")


@dataclass
class Score:
    name: str
    value: Any                    # float | bool | str
    data_type: str                # NUMERIC | BOOLEAN | CATEGORICAL
    comment: str = ""
    kind: str = "code"            # "code" or "llm" (for routing/aggregation)


# ------------------------------------------------------------ helper access ---
def extract_listing_ids(text: str) -> List[str]:
    """Listing ids the agent actually put in front of the user."""
    if not text:
        return []
    seen, out = set(), []
    for m in LISTING_ID_RE.findall(text):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _constraints(result: Dict[str, Any]) -> Dict[str, Any]:
    return result.get("constraints") or {}


# =============================================================================
# CODE EVALUATORS
# =============================================================================
def code_used_search_tool(result: Dict[str, Any]) -> Score:
    used = "search_listings" in (result.get("tools_called") or [])
    return Score("used-search-tool", used, "BOOLEAN", kind="code",
                 comment="Agent grounded its answer in a catalog search."
                 if used else "Agent answered without calling search_listings.")


def code_grounded_listings(result: Dict[str, Any]) -> Score:
    shown = result.get("listings_shown") or []
    retrieved = set(result.get("retrieved_ids") or [])
    if not shown:
        return Score("grounded-listings", True, "BOOLEAN", kind="code",
                     comment="No specific listings recommended.")
    hallucinated = [i for i in shown if get_listing(i) is None]
    not_retrieved = [i for i in shown if get_listing(i) is not None and i not in retrieved]
    ok = not hallucinated and not not_retrieved
    if hallucinated:
        c = f"Hallucinated listing id(s) not in catalog: {hallucinated}."
    elif not_retrieved:
        c = f"Recommended id(s) never returned by search: {not_retrieved}."
    else:
        c = f"All {len(shown)} recommended listings exist and were retrieved."
    return Score("grounded-listings", ok, "BOOLEAN", kind="code", comment=c)


def code_budget_adherence(result: Dict[str, Any]) -> Score:
    shown = result.get("listings_shown") or []
    max_price = _constraints(result).get("max_price")
    real = [l for i in shown if (l := get_listing(i)) is not None]
    if max_price is None or not real:
        return Score("budget-adherence", 1.0, "NUMERIC", kind="code",
                     comment="No budget constraint to check." if max_price is None
                     else "No concrete listings to check.")
    within = [l for l in real if l["price"] <= max_price]
    over = [f'{l["id"]}(€{l["price"]:,})' for l in real if l["price"] > max_price]
    frac = len(within) / len(real)
    c = (f"All {len(real)} within €{max_price:,.0f}." if not over
         else f"{len(over)}/{len(real)} over budget (€{max_price:,.0f}): {', '.join(over)}.")
    return Score("budget-adherence", round(frac, 2), "NUMERIC", kind="code", comment=c)


def code_location_match(result: Dict[str, Any]) -> Score:
    shown = result.get("listings_shown") or []
    location = (_constraints(result).get("location") or "").strip().lower()
    real = [l for i in shown if (l := get_listing(i)) is not None]
    if not location or not real:
        return Score("location-match", 1.0, "NUMERIC", kind="code",
                     comment="No location constraint to check." if not location
                     else "No concrete listings to check.")
    # Match ANY location token against city+neighborhood, mirroring
    # search_listings. Handles a district ("Gràcia") and compound values the
    # planner may extract ("Gràcia, Barcelona") regardless of word order.
    loc_tokens = [t for t in re.split(r"[,\s]+", location) if len(t) >= 3]

    def _in_loc(l):
        hay = f'{l["city"]} {l["neighborhood"]}'.lower()
        return any(t in hay for t in loc_tokens) if loc_tokens else True
    matched = [l for l in real if _in_loc(l)]
    frac = len(matched) / len(real)
    wrong = [f'{l["id"]}({l["neighborhood"]}, {l["city"]})' for l in real if not _in_loc(l)]
    c = (f"All {len(real)} in '{location.title()}'." if not wrong
         else f"{len(wrong)}/{len(real)} outside '{location.title()}': {', '.join(wrong)}.")
    return Score("location-match", round(frac, 2), "NUMERIC", kind="code", comment=c)


def code_language_match(result: Dict[str, Any]) -> Score:
    q = (_constraints(result).get("query_language") or "").lower()
    a = (result.get("response_language") or "").lower()
    if not q:
        return Score("language-match", True, "BOOLEAN", kind="code",
                     comment="Query language unknown.")
    ok = q == a
    return Score("language-match", ok, "BOOLEAN", kind="code",
                 comment=f"Question in '{q}', answered in '{a}'." +
                 ("" if ok else " Language mismatch."))


CODE_EVALUATORS = [
    code_used_search_tool,
    code_grounded_listings,
    code_budget_adherence,
    code_location_match,
    code_language_match,
]


def run_code_evaluators(result: Dict[str, Any]) -> List[Score]:
    return [fn(result) for fn in CODE_EVALUATORS]


# =============================================================================
# LLM-AS-A-JUDGE EVALUATORS
# =============================================================================
_JUDGE_SYSTEM = (
    "You are a strict evaluator of a real-estate assistant's answers. "
    "You return ONLY compact JSON. Be critical: reserve the top score for "
    "genuinely excellent answers and penalise vagueness, fabrication, or "
    "ignoring the user's constraints."
)


def _judge_call(rubric: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Call Claude as a judge. Returns parsed {"score"|"rating", "reasoning"}."""
    from .config import get_anthropic, JUDGE_MODEL

    client = get_anthropic()
    # Ground truth = ALL tool outputs (listings + neighborhood data + mortgage
    # calcs), so groundedness isn't wrongly penalised for legitimately-tooled
    # facts. Fall back to just the listings if evidence isn't present.
    evidence = payload.get("evidence") or payload.get("retrieved_listings", [])
    user = (
        f"{rubric}\n\n"
        f"=== USER QUESTION ===\n{payload.get('query','')}\n\n"
        f"=== RETRIEVED EVIDENCE (tool outputs the answer may cite: listings, "
        f"neighborhood insights, mortgage estimates) ===\n"
        f"{json.dumps(evidence, ensure_ascii=False)[:6000]}\n\n"
        f"=== ASSISTANT ANSWER ===\n{payload.get('answer','')}\n\n"
        "Respond with ONLY JSON."
    )
    resp = client.messages.create(
        model=JUDGE_MODEL, max_tokens=400, system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text if resp.content else ""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"reasoning": text[:300]}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"reasoning": text[:300]}


def _numeric_judge(name: str, rubric: str, result: Dict[str, Any]) -> Score:
    data = _judge_call(
        rubric + "\nReturn JSON: {\"score\": <float 0.0-1.0>, \"reasoning\": <short string>}.",
        result,
    )
    raw = data.get("score", data.get("rating"))
    try:
        val = float(raw)
        if val > 1.0:  # tolerate a 1-5, 1-10 or 0-100 style answer
            val = val / 5.0 if val <= 5 else (val / 10.0 if val <= 10 else val / 100.0)
        val = max(0.0, min(1.0, val))
    except (TypeError, ValueError):
        val = 0.0
    return Score(name, round(val, 2), "NUMERIC", kind="llm",
                 comment=str(data.get("reasoning", ""))[:500])


def judge_helpfulness(result: Dict[str, Any]) -> Score:
    return _numeric_judge(
        "helpfulness",
        "Score how well the answer helps the user achieve their housing goal: "
        "concrete options, clear next steps, and useful context. 1.0 = excellent, 0.0 = useless.",
        result,
    )


def judge_relevance(result: Dict[str, Any]) -> Score:
    return _numeric_judge(
        "relevance",
        "Score how relevant the recommended properties are to the user's stated "
        "constraints (location, budget, bedrooms, features). 1.0 = perfectly on-target.",
        result,
    )


def judge_groundedness(result: Dict[str, Any]) -> Score:
    return _numeric_judge(
        "groundedness",
        "Score whether every factual claim about properties (prices, sizes, features, "
        "neighborhoods) is supported by the RETRIEVED LISTINGS. Penalise any invented "
        "price, feature, or listing. 1.0 = fully grounded, 0.0 = fabricated.",
        result,
    )


def judge_tone(result: Dict[str, Any]) -> Score:
    data = _judge_call(
        "Assess the answer's tone as a professional real-estate advisor "
        "(clear, courteous, helpful, no hype or pushiness). "
        "Return JSON: {\"rating\": \"excellent\"|\"good\"|\"poor\", \"reasoning\": <short string>}.",
        result,
    )
    rating = str(data.get("rating", data.get("score", "good"))).lower().strip()
    if rating not in ("excellent", "good", "poor"):
        rating = "good"
    return Score("tone", rating, "CATEGORICAL", kind="llm",
                 comment=str(data.get("reasoning", ""))[:500])


LLM_JUDGES = [judge_helpfulness, judge_relevance, judge_groundedness, judge_tone]


def run_llm_judges(result: Dict[str, Any]) -> List[Score]:
    return [fn(result) for fn in LLM_JUDGES]

"""
THE CONTRACT for the listing-photo-audit demo. Single source of truth.

Everything in the photo-audit path imports from here — the fixture renderer, the
dataset seeder, the LangGraph nodes, the code evaluators, the vision judges and
the managed-judge seeder. If a vocabulary or a score name needs to change, it
changes here and only here.

Why a contract module at all: the four eval layers (code, SDK vision judge,
managed text-only judge, anti-pattern exhibit) must score the SAME output
schema with the SAME score names, or the side-by-side comparison that is the
whole point of the demo compares nothing.

--------------------------------------------------------------------------
GROUND TRUTH BY CONSTRUCTION
--------------------------------------------------------------------------
Fixtures are RENDERED, not photographed, so each scene's true attributes are
known exactly rather than being someone's labelling opinion. That is the
licensing-safe choice for a public repo AND the methodologically stronger one:
a real photo needs a human to say what is in it, and that label becomes the
thing you are really evaluating.

The cost is honesty about what they look like: these are schematic renders, not
listing photography. `data/photo_scenes.py` is deliberately the only place that
knows how pixels are made, so swapping in real photos later is a one-module
change (plus per-file provenance). Do that before putting this in front of a
customer; see MULTIMODAL_EVAL_SPEC.md.

--------------------------------------------------------------------------
ATTRIBUTES: countable vs interpretive
--------------------------------------------------------------------------
COUNTABLE attributes (window_count, appliances, cabinetry, countertop,
flooring, clutter) are unambiguous in a render, so an evaluator may assert them
HARD — a miss is a defect, not a difference of opinion.

INTERPRETIVE attributes (condition, natural_light) are DERIVED from the
countable ones by `derive_interpretive()`. They exist so the demo has claims
that genuinely require judgement ("recently renovated", "floods with natural
light") — which is exactly where a vision judge earns its cost over a code
check, and where a text-only proxy judge is at the mercy of the extractor.
"""

from typing import Any, Dict, List, Optional, Tuple

# ==========================================================================
# 1. CLOSED ATTRIBUTE VOCABULARY
# ==========================================================================
# The vision extractor MAY NOT emit a key or value outside this table. The
# `closed-vocabulary` code evaluator enforces that, which is how the demo shows
# a deterministic check catching a failure a judge would wave through.

ROOM_TYPES = ("kitchen", "living_room", "bathroom")
CABINETRY = ("light_modern", "dark_dated", "none")
COUNTERTOP = ("stone", "laminate", "none")
FLOORING = ("wood", "tile", "carpet")
CLUTTER = ("clear", "moderate", "cluttered")
APPLIANCES = ("oven", "fridge", "dishwasher")

# interpretive — derived, never rendered directly
CONDITION = ("renovated", "dated", "needs_work")
NATURAL_LIGHT = ("bright", "moderate", "dim")

WINDOW_COUNT_RANGE = (0, 3)

COUNTABLE_KEYS = ("room_type", "cabinetry", "countertop", "flooring",
                  "clutter", "window_count", "appliances")
INTERPRETIVE_KEYS = ("condition", "natural_light")
ATTRIBUTE_KEYS = COUNTABLE_KEYS + INTERPRETIVE_KEYS

# Categorical keys ONLY — every value is a tuple of allowed values, so
# `value not in CATEGORICAL_VOCAB[key]` is always a safe membership test.
# Use THIS in evaluator code.
CATEGORICAL_VOCAB: Dict[str, tuple] = {
    "room_type": ROOM_TYPES,
    "cabinetry": CABINETRY,
    "countertop": COUNTERTOP,
    "flooring": FLOORING,
    "clutter": CLUTTER,
    "condition": CONDITION,
    "natural_light": NATURAL_LIGHT,
}

# Human-readable table for docs, prompts and the UI. `window_count` and
# `appliances` map to PROSE, not tuples, because they are not categorical —
# so `v not in ATTRIBUTE_VOCAB[k]` would do a silent SUBSTRING match on those
# two and pass nonsense. Never membership-test against this dict; use
# CATEGORICAL_VOCAB, or validate_attributes() which handles all key kinds.
ATTRIBUTE_VOCAB: Dict[str, Any] = {
    **CATEGORICAL_VOCAB,
    "window_count": "int 0..3",
    "appliances": f"subset of {APPLIANCES}",
}


def derive_interpretive(attrs: Dict[str, Any]) -> Dict[str, str]:
    """Interpretive attributes as a pure function of the countable ones.

    Deterministic on purpose: it gives the dataset exact ground truth for
    claims like "recently renovated" without anyone hand-labelling, and it
    means a disagreement between the vision judge and ground truth is a real
    finding rather than a definitional argument.

    KNOWN ASYMMETRY: `condition == "renovated"` requires light_modern cabinetry
    AND a stone countertop, so any room with cabinetry "none" (every
    living_room, most bathrooms) can never be "renovated" and the claim
    "recently renovated" is structurally un-supportable there. That is a
    deliberate simplification of a rendered world, not a bug — but do not add a
    living-room scene whose copy claims renovation and expect it to be
    satisfiable. If you need that, extend the rule here rather than
    hand-labelling the scene.
    """
    windows = int(attrs.get("window_count", 0) or 0)
    natural_light = "bright" if windows >= 2 else "moderate" if windows == 1 else "dim"

    modern = attrs.get("cabinetry") == "light_modern"
    stone = attrs.get("countertop") == "stone"
    messy = attrs.get("clutter") == "cluttered"
    if modern and stone and not messy:
        condition = "renovated"
    elif attrs.get("cabinetry") == "dark_dated" and messy:
        condition = "needs_work"
    else:
        condition = "dated"
    return {"condition": condition, "natural_light": natural_light}


def validate_attributes(attrs: Any) -> List[str]:
    """Return a list of human-readable violations; empty list == valid.

    Used by the `attributes-schema-valid` and `closed-vocabulary` code
    evaluators. Kept here so the extractor, the evaluators and the seeder all
    agree on what "valid" means.
    """
    problems: List[str] = []
    if not isinstance(attrs, dict):
        return [f"attributes must be an object, got {type(attrs).__name__}"]

    for key in attrs:
        if key not in ATTRIBUTE_KEYS:
            problems.append(f"unknown attribute key {key!r}")

    for key, allowed in CATEGORICAL_VOCAB.items():
        if key in attrs and attrs[key] not in allowed:
            problems.append(f"{key}={attrs[key]!r} not in {allowed}")

    if "window_count" in attrs:
        wc = attrs["window_count"]
        lo, hi = WINDOW_COUNT_RANGE
        if not isinstance(wc, int) or isinstance(wc, bool) or not (lo <= wc <= hi):
            problems.append(f"window_count={wc!r} must be an int in {lo}..{hi}")

    if "appliances" in attrs:
        ap = attrs["appliances"]
        if not isinstance(ap, list):
            problems.append(f"appliances must be a list, got {type(ap).__name__}")
        else:
            for a in ap:
                if a not in APPLIANCES:
                    problems.append(f"unknown appliance {a!r}")
            if len(set(ap)) != len(ap):
                problems.append("appliances contains duplicates")
    return problems


# ==========================================================================
# 2. CLAIMS — marketing copy, and what makes each one true
# ==========================================================================
# Each claim carries a predicate over the TRUE attributes. That is what makes
# the dataset self-labelling: the seeder computes expected verdicts instead of
# a human writing them, so ground truth cannot drift from the fixture.
#
# verdict vocabulary:
#   supported     — the photo bears the claim out
#   contradicted  — the photo positively refutes it
#   unverifiable  — the photo cannot settle it either way. The correct answer
#                   is abstention. Agents that guess here are the failure mode
#                   `abstains-when-unverifiable` is designed to catch.

VERDICTS = ("supported", "contradicted", "unverifiable")
OVERALL_VERDICTS = VERDICTS + ("needs_better_photo",)


def _claim(text: str, kind: str, predicate=None, *, difficulty: str = "visible"):
    return {"text": text, "kind": kind, "predicate": predicate,
            "difficulty": difficulty}


# `predicate(attrs) -> bool` where attrs includes derived interpretive keys.
CLAIM_SPECS: Tuple[Dict[str, Any], ...] = (
    _claim("recently renovated", "interpretive",
           lambda a: a.get("condition") == "renovated"),
    _claim("floods with natural light", "interpretive",
           lambda a: a.get("natural_light") == "bright"),
    _claim("stone countertops", "countable",
           lambda a: a.get("countertop") == "stone"),
    _claim("fully equipped kitchen", "countable",
           lambda a: set(APPLIANCES) <= set(a.get("appliances") or []),
           difficulty="subtle"),
    _claim("hardwood floors throughout", "countable",
           lambda a: a.get("flooring") == "wood"),
    _claim("dishwasher included", "countable",
           lambda a: "dishwasher" in (a.get("appliances") or []),
           difficulty="subtle"),
    # No interior photo can settle these. Abstention is the only right answer.
    _claim("on a quiet street", "unverifiable", None),
    _claim("low monthly service charges", "unverifiable", None),
)

CLAIMS_BY_TEXT = {c["text"]: c for c in CLAIM_SPECS}


def expected_verdict(claim_text: str, true_attrs: Dict[str, Any]) -> str:
    """Ground-truth verdict for one claim against one scene."""
    spec = CLAIMS_BY_TEXT[claim_text]
    if spec["kind"] == "unverifiable" or spec["predicate"] is None:
        return "unverifiable"
    return "supported" if spec["predicate"](true_attrs) else "contradicted"


# ==========================================================================
# 3. DATASET CLASSES
# ==========================================================================
# Composition is the experiment design. `contradicted_subtle` is the class that
# separates a real vision judge from a metadata-proxy judge, and
# `unverifiable` is the one that catches over-confident agents.

# A photo too dark or blurred to audit. Referenced by name in the seeder and in
# evaluators, so it is a constant rather than a string literal sprinkled around.
SCENE_CLASS_LOW_QUALITY = "low_quality"

SCENE_CLASSES = {
    "supported":            "copy matches the photo — control against over-eager auditing",
    "contradicted_visible": "contradiction plainly in frame — every layer should catch it",
    "contradicted_subtle":  "contradiction needs close attention — separates vision judge from proxy",
    "unverifiable":         "copy claims what a photo cannot show — correct answer is abstention",
    "low_quality":          "dark or blurred — must route to the request_better_photo branch",
}


# ==========================================================================
# 4. AUDIT OUTPUT SCHEMA — what the graph returns
# ==========================================================================
# The graph's return value AND the root observation's output. Every evaluator
# consumes exactly this. `extracted_attributes` is what the metadata-proxy
# judge sees instead of pixels, which is the substitution the demo interrogates.
#
# {
#   "listing_id": "BCN-014",
#   "extracted_attributes": {...closed vocabulary...},
#   "extraction_confidence": 0.0..1.0,
#   "claims": [
#     {"claim": "recently renovated",
#      "verdict": "supported"|"contradicted"|"unverifiable",
#      "evidence": "<what in the image drove this>"}
#   ],
#   "verdict": "supported"|"contradicted"|"unverifiable"|"needs_better_photo",
#   "corrected_copy": "<rewritten copy the photo does support>",
#   "cited_listing_id": "BCN-014"
# }

AUDIT_KEYS = ("listing_id", "extracted_attributes", "extraction_confidence",
              "claims", "verdict", "corrected_copy", "cited_listing_id")

# Below this, vision_extract routes to request_better_photo instead of auditing.
CONFIDENCE_FLOOR = 0.55


def overall_verdict(claim_results: List[Dict[str, Any]]) -> str:
    """Roll per-claim verdicts up to one trace-level verdict.

    Any contradiction dominates: a listing whose copy contains one false claim
    is a listing with false copy, regardless of how many true claims sit
    beside it.

    An EMPTY claim list returns "unverifiable", not "supported". Nothing was
    adjudicated, so nothing has been supported — defaulting to "supported"
    would let a graph that silently dropped every claim look like a clean pass.
    """
    verdicts = [c.get("verdict") for c in claim_results or []]
    if not verdicts:
        return "unverifiable"
    if "contradicted" in verdicts:
        return "contradicted"
    if all(v == "unverifiable" for v in verdicts):
        return "unverifiable"
    return "supported"


# ==========================================================================
# 5. OBSERVATION NAMES
# ==========================================================================
# Stable and low-cardinality, matching the existing demo's convention. The
# WRAPPER span is what gets isRootObservation=True (measured — LangGraph's own
# CHAIN root does not), so managed judges filtered on "Is Root Observation"
# target ROOT_SPAN. Its input/output must therefore be the judge-facing
# summary, not raw graph state.
ROOT_SPAN = "audit-listing-photo"
GRAPH_RUN_NAME = "photo-audit-graph"
NODE_VISION_EXTRACT = "vision_extract"
NODE_RETRIEVE_LISTING = "retrieve_listing"
NODE_AUDIT_CLAIMS = "audit_claims"
NODE_COMPOSE_ANSWER = "compose_answer"
NODE_REQUEST_BETTER_PHOTO = "request_better_photo"

DATASET_NAME = "multimodal/property-photo-audit"
BASE_TAGS = ["photo-audit", "real-estate"]


# ==========================================================================
# 6. SCORE NAMES — ONE vocabulary across all four layers
# ==========================================================================
# The existing demo already enforces one score vocabulary across live, experiment
# and annotation paths; the photo-audit path joins it rather than inventing a
# parallel naming scheme. Run-level means are "avg-<name>".

# layer C — deterministic code evaluators
SCORE_ATTRS_SCHEMA_VALID = "attributes-schema-valid"
SCORE_CLOSED_VOCABULARY = "closed-vocabulary"
SCORE_CLAIM_COVERAGE = "claim-coverage"
SCORE_LISTING_CITED = "listing-cited"
SCORE_ABSTAINS_UNVERIFIABLE = "abstains-when-unverifiable"
SCORE_VERDICT_EXACT = "verdict-exact-match"
# Deterministic accuracy of the vision extractor against the scene's OWN
# attributes. Exists because rendered fixtures make `true_attributes` exact, so
# extraction accuracy does not have to be delegated to a judge — and a
# deterministic row is the one worth gating CI on. Without it, the only check on
# the extractor was `extraction-fidelity`, an LLM judge; gating on a judge is
# precisely what cicd/thresholds.json argues against.
SCORE_ATTRS_EXACT_MATCH = "attributes-exact-match"

CODE_SCORE_NAMES = (
    SCORE_ATTRS_SCHEMA_VALID, SCORE_CLOSED_VOCABULARY, SCORE_CLAIM_COVERAGE,
    SCORE_LISTING_CITED, SCORE_ABSTAINS_UNVERIFIABLE, SCORE_VERDICT_EXACT,
    SCORE_ATTRS_EXACT_MATCH,
)

# layer A — SDK vision judges (see pixels)
SCORE_PHOTO_COPY_CONSISTENCY = "photo-copy-consistency"
SCORE_EXTRACTION_FIDELITY = "extraction-fidelity"
VISION_SCORE_NAMES = (SCORE_PHOTO_COPY_CONSISTENCY, SCORE_EXTRACTION_FIDELITY)

# layer B — managed, text-only, scores the EXTRACTED ATTRIBUTES not the image
SCORE_PROXY_CONSISTENCY = "proxy-photo-consistency"

# layer D — the deliberate anti-pattern. Named so nobody mistakes it for a
# working evaluator: it is mapped straight at the media token and exists to be
# shown returning a confident score on `@@@langfuseMedia:...@@@`.
SCORE_ANTIPATTERN = "ANTIPATTERN-photo-judge-raw-media"

ALL_SCORE_NAMES = (CODE_SCORE_NAMES + VISION_SCORE_NAMES
                   + (SCORE_PROXY_CONSISTENCY, SCORE_ANTIPATTERN))


# ==========================================================================
# 7. DATASET ITEM SHAPE
# ==========================================================================
# input:            {"listing_id", "marketing_copy", "claims", "photo"(LangfuseMedia)}
# expected_output:  {"verdict", "claim_verdicts", "true_attributes"}
# metadata:         {"scene_class", "scene_id", "photo_provenance"}
#
# `true_attributes` is in expected_output rather than metadata deliberately:
# metadata values are coerced to strings and capped at 200 chars by Langfuse, so
# a nested attribute dict would be silently clipped — the same trap the
# multi-turn work documented for transcripts.

PROVENANCE_RENDERED = "rendered-synthetic (see data/photo_scenes.py)"


def build_expected_output(claims: List[str],
                          true_attrs: Dict[str, Any],
                          *, unreadable: bool = False) -> Dict[str, Any]:
    """Compute ground truth for a scene. Never hand-write this.

    `unreadable=True` marks a `low_quality` scene — a photo too dark or blurred
    to audit. The correct behaviour there is to ABSTAIN AND ASK
    (`needs_better_photo`), not to adjudicate the claims, so that is the
    expected overall verdict.

    This matters more than it looks. Without it, `verdict-exact-match` compares
    a perfect agent's `needs_better_photo` against a claim-level verdict
    computed from attributes the agent could not legitimately see, and marks
    correct behaviour WRONG. The underlying `claim_verdicts` are still returned
    (they are what the render actually contains, useful for diagnosis) but
    `claim_verdicts_apply` says not to grade against them.
    """
    full = {**true_attrs, **derive_interpretive(true_attrs)}
    claim_verdicts = {c: expected_verdict(c, full) for c in claims}
    rolled = overall_verdict([{"verdict": v} for v in claim_verdicts.values()])
    return {
        "verdict": "needs_better_photo" if unreadable else rolled,
        "claim_verdicts": claim_verdicts,
        # False for unreadable scenes: the claims have a truth value in the
        # scene definition, but an agent looking at the degraded render cannot
        # be expected to recover it. Evaluators MUST honour this flag.
        "claim_verdicts_apply": not unreadable,
        "true_attributes": full,
    }


def marketing_copy(claims: List[str]) -> str:
    """Render a claim list as one line of estate-agent copy."""
    if not claims:
        return ""
    if len(claims) == 1:
        body = claims[0]
    else:
        body = ", ".join(claims[:-1]) + f" and {claims[-1]}"
    return body[0].upper() + body[1:] + "."

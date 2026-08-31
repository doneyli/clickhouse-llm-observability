"""
Listing Photo Audit — a LangGraph trajectory whose IMAGE is load-bearing.

Given a property photo and the listing's marketing copy, decide for every claim in
the copy whether the photo supports it, contradicts it, or cannot settle it, and
produce a corrected description. `agent/photo_contract.py` owns every vocabulary,
observation name and output key used here; this module owns the graph and its
instrumentation, nothing else.

Trace shape (measured in the spike, see MULTIMODAL_EVAL_SPEC.md §1a):

    audit-listing-photo              SPAN   isRootObservation=True  <- OUR wrapper
    │   input  = the dataset-item shape (listing, copy, claims, photo)
    │   output = the photo_contract §4 audit schema
    └─ photo-audit-graph             CHAIN  isRootObservation=false <- LangGraph's own root
       ├─ vision_extract             CHAIN  output carries the ATTRIBUTE TEXT
       │  └─ vision:extract-attributes   GENERATION  input carries the PHOTO
       ├─ retrieve_listing           CHAIN
       │  └─ tool:get_listing        TOOL
       ├─ audit_claims               CHAIN  input = attribute text, output = verdicts
       │  └─ llm:adjudicate-claims   GENERATION
       └─ compose_answer             CHAIN  deterministic roll-up, no LLM call
       (or, below the confidence floor)
       └─ request_better_photo       CHAIN  terminal self-correct branch

Four instrumentation decisions here are MEASURED, not stylistic. Changing them
breaks evaluation silently — nothing errors, the judges simply stop matching:

1. The manual wrapper span stays. It — not LangGraph's `CHAIN` root — is what
   Langfuse flags `isRootObservation=True`, and managed judges filter on
   "Is Root Observation". Dropping it would hand those judges raw graph state.
2. A managed judge sees ONE observation: no siblings, no children. So each
   observation carries, on its OWN input/output, everything a judge targeting it
   needs — the attribute text on `vision_extract`, the verdicts on `audit_claims`,
   the whole audit on the wrapper.
3. `propagate_attributes(...)` is mandatory. Observation-level rules filter on
   trace attributes (`userId`, `sessionId`, `tags`, `traceName`, `metadata`) as
   seen ON THE OBSERVATION; without propagation they match nothing, quietly.
4. No `set_current_trace_io()`. v4 is observations-first and derives the trace's
   input/output from the root observation, which `root.update()` already sets.
   (Also deprecated, and forbidden by this repo's CLAUDE.md.)

One more, specific to graphs: the photo is deliberately NOT in the graph state.
The LangChain callback handler logs each node's observation input as the state
dict at node entry, so a base64 data URI in state would be duplicated onto every
node observation — bloating the trace and, worse, poisoning the very text a
text-only judge reads. The photo is bound into the node closures instead, and
enters the trace exactly once, on the generation observation that sends it to the
model. That also means the graph is built per call, which is what we want anyway:
Langfuse media URLs are signed and expire, so callers must re-fetch the data URI
per run rather than caching it.
"""

import json
import re
from contextlib import nullcontext
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from langfuse import propagate_attributes

from . import photo_contract
from .catalog import get_listing
from .config import AGENT_MODEL, flush_langfuse, get_langfuse
from .llm import call_llm, provider_of

# --- names for the MANUAL child observations ---------------------------------
# The five node names come from the contract (LangGraph names a node observation
# after its node key, so the keys must be the contract's). These are the extra
# observations we create by hand inside those nodes, following the existing
# demo's `tool:<name>` colon convention. Stable and low-cardinality: the listing
# id and the claims live in the observation INPUT, never in a name.
#
# `OBS_VISION_CALL` is the only observation that holds the image, which makes it
# the target for two things at once: the real SDK vision judge, and layer D's
# anti-pattern exhibit (a managed judge pointed at the raw
# `@@@langfuseMedia:...@@@` token). Keep the name stable — seeded rules match on it.
OBS_VISION_CALL = "vision:extract-attributes"
OBS_LISTING_LOOKUP = "tool:get_listing"
OBS_AUDIT_CALL = "llm:adjudicate-claims"

# Marker written into `evidence` when the model returned no adjudication for a
# claim. We fill the gap rather than silently dropping the claim — a compliance
# report must never lose a claim — but the fill has to be machine-detectable, or
# `claim-coverage` would score 1.0 on a model that adjudicated nothing. Code
# evaluators should import this constant and treat it as "not adjudicated".
UNADJUDICATED_EVIDENCE = "MODEL RETURNED NO ADJUDICATION FOR THIS CLAIM (filled as unverifiable)"

# Does `audit_claims` see the pixels, or only `vision_extract`'s attribute text?
#
# This is a real fork in the demo's story and the spec leaves it open, so it is one
# line instead of an assumption buried in a prompt:
#
#   True  (default) — the agent is as strong as we can reasonably make it, so a
#                     judge disagreement is a property of the JUDGE, not of a
#                     straw-man agent. The proxy judge then degrades on
#                     `contradicted_subtle` because the EXTRACTOR did not write
#                     the deciding attribute down, while the agent (and the vision
#                     judge) got it right from the photo. That is §3.4's hypothesis
#                     stated as sharply as it can be stated.
#   False           — the agent is text-only downstream of extraction, so it fails
#                     wherever the extractor is blind, and the proxy judge — blind
#                     in exactly the same way — rubber-stamps the failure. A
#                     "green dashboard measuring nothing" story instead.
#
# Either is defensible; flipping this changes which story a run tells, so measure
# before quoting numbers (both arms, twice — see cicd/thresholds.json).
AUDIT_CLAIMS_SEES_PIXELS = True

# Anthropic's vision API accepts these four. A `data:application/pdf` or
# `image/tiff` URI fails with an opaque 400 far from the cause, so reject it here.
_ANTHROPIC_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")

_EVIDENCE_MAX_CHARS = 400

# Repair notes come in two kinds and only one of them should paint an observation
# amber in the trace UI. A fixed spelling ("Light Modern" -> "light_modern") is
# bookkeeping; a dropped key or an unparseable reply is a defect. Prefixing the
# harmless ones keeps `level="WARNING"` meaningful — a trace view where every
# observation is amber is a trace view nobody reads.
_BENIGN = "note: "


def _defects(repairs: List[str]) -> List[str]:
    return [r for r in repairs if not r.startswith(_BENIGN)]


# Verdict spellings models actually emit, mapped onto the contract's three.
# Anything not here (or not in VERDICTS) becomes `unverifiable` and is recorded —
# abstention is the safe default, and inventing a verdict is the failure mode the
# whole demo is about.
_VERDICT_ALIASES = {
    "support": "supported", "true": "supported", "yes": "supported",
    "contradict": "contradicted", "contradiction": "contradicted",
    "unsupported": "contradicted", "not_supported": "contradicted",
    "refuted": "contradicted", "false": "contradicted", "no": "contradicted",
    "unverified": "unverifiable", "unknown": "unverifiable",
    "not_verifiable": "unverifiable", "indeterminate": "unverifiable",
    "abstain": "unverifiable", "cannot_tell": "unverifiable",
}


# ==========================================================================
# Prompts
# ==========================================================================
# Both prompts are generated FROM the contract, so a vocabulary change in
# photo_contract.py updates them with no second edit here. (These stay local
# rather than moving into Langfuse Prompt Management: the concierge already
# demonstrates that node of the loop, and a prompt whose text must stay in lockstep
# with a code-level closed vocabulary is the wrong thing to let someone edit in a
# UI without also editing the contract.)

def _vocabulary_block() -> str:
    """Render the closed vocabulary for the extractor's prompt, from the contract.

    Categorical keys come from `CATEGORICAL_VOCAB`, whose values are always tuples;
    `window_count` and `appliances` are built from their own contract constants.
    That split is deliberate: in `ATTRIBUTE_VOCAB` those two keys map to
    human-readable PROSE, so anything treating that dict as a set of allowed values
    silently does a substring match. This module never membership-tests either dict
    — `photo_contract.validate_attributes()` makes every keep/drop decision.
    """
    lines = []
    for key in photo_contract.ATTRIBUTE_KEYS:
        allowed = photo_contract.CATEGORICAL_VOCAB.get(key)
        if allowed is not None:
            rendered = " | ".join(allowed)
        elif key == "appliances":
            rendered = ("a JSON list, any subset of ["
                        + ", ".join(photo_contract.APPLIANCES) + "]")
        elif key == "window_count":
            lo, hi = photo_contract.WINDOW_COUNT_RANGE
            rendered = f"integer {lo}..{hi}"
        else:
            # A non-categorical key the contract added later: show its prose rather
            # than dropping it out of the prompt entirely.
            rendered = str(photo_contract.ATTRIBUTE_VOCAB.get(key, "see photo_contract"))
        lines.append(f"  {key}: {rendered}")
    return "\n".join(lines)


# NOTE what is deliberately ABSENT from the extractor's prompt:
#   * the marketing copy and its claims — priming the extractor with the sales
#     pitch it is meant to check would inflate agreement and quietly destroy the
#     experiment. The extractor describes the photo; it never audits.
#   * `photo_contract.derive_interpretive()`'s rule (windows>=2 => bright, etc.).
#     That function is GROUND-TRUTH machinery. Leaking it would make `condition`
#     and `natural_light` correct by construction, and `contradicted_subtle` —
#     the class that separates a vision judge from a text proxy — would stop
#     separating anything.
VISION_SYSTEM = f"""You extract visual attributes from a single property image.

The image may be a photograph OR a clean schematic/diagrammatic render of a room.
Both are valid evidence. Judge the image on whether the attributes below are
legible in it, NOT on whether it looks photographic — a simple flat-shaded diagram
in which the cabinets, worktop, windows and appliances are plainly distinguishable
is HIGH-confidence evidence, not low.

Report ONLY what you can actually see in the image. You are the evidence, so never
guess to be helpful: an omitted attribute is honest, an invented one is a defect.

Return ONLY a JSON object, no prose, no code fence:
{{"attributes": {{...}}, "extraction_confidence": 0.0}}

`attributes` may use ONLY these keys, with ONLY these values:
{_vocabulary_block()}

Rules:
- Omit any key you cannot determine from the image. Do not use null.
- `window_count` counts windows visible in this frame.
- `appliances` lists only appliances you can see.
- `cabinetry`/`countertop` may be "none" only when the room visibly has none.
- `condition` and `natural_light` are your judgement of what the photo shows.
- `extraction_confidence` is 0.0-1.0 for how LEGIBLE this image is — how clearly
  you could read the attributes off it. It is NOT a rating of photographic
  quality or realism. A crisp, well-lit image whose surfaces and fixtures are
  plainly distinguishable is 0.8-1.0 EVEN IF it is a simple diagram rather than a
  photograph. Report below {photo_contract.CONFIDENCE_FLOOR:.2f} ONLY when the
  image is genuinely too dark, blurred or obscured to tell the surfaces and
  fixtures apart — in which case also omit what you cannot see. Anything below
  that floor is routed to a "request a better photo" branch instead of being
  audited against copy the image cannot support, so under-reporting on a legible
  image suppresses the audit entirely."""

AUDIT_SYSTEM = f"""You audit a property listing's marketing copy against the evidence
available for its photograph.

For EVERY numbered claim, return exactly one verdict:
  supported     - the photo evidence bears the claim out
  contradicted  - the photo evidence positively refutes it
  unverifiable  - the photo cannot settle it either way

Abstaining is a CORRECT answer, not a cop-out. A claim about anything a photograph
of a room cannot show - street noise, service charges, neighbours, transport,
running costs, legal or financial facts, anything outside the frame - is ALWAYS
`unverifiable`, however plausible it sounds.

The listing record is context and grounding ONLY. It is not photo evidence: never
promote a claim to `supported` because the record agrees with it.

Return ONLY a JSON object, no prose, no code fence:
{{"adjudications": [{{"claim_index": 0, "verdict": "supported", "evidence": "..."}}]}}

`claim_index` is the number shown beside the claim. Return one entry per claim.
`evidence` is at most 200 characters and must cite what in the photo drove the
verdict - or state plainly that the photo cannot show it."""


# ==========================================================================
# Small helpers
# ==========================================================================

def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort JSON out of a model reply. Same intent as concierge._extract_json.

    Tries the whole string, then a fenced block, then the outermost braces —
    because "malformed JSON" in practice means a code fence or a sentence of
    preamble, not corrupt syntax. Returns {} on failure; every caller treats an
    empty dict as "the model told us nothing" and degrades honestly.
    """
    if not text:
        return {}
    raw = text.strip()
    for candidate in (raw,
                      *(m.group(1) for m in re.finditer(r"```(?:json)?\s*(.+?)```", raw, re.S)),
                      *(m.group(0) for m in [re.search(r"\{.*\}", raw, re.S)] if m)):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _resolve_data_uri(photo: Any) -> str:
    """Return a `data:<type>;base64,<...>` string, or raise with the actual fix.

    Accepts a `LangfuseMediaReference` (duck-typed, so this module does not import
    langfuse.media) and resolves it HERE, on every call. That is deliberate: the
    signed URL behind a media reference expires, so a data URI fetched once and
    reused across a 20-item experiment can 403 halfway through.

    The two mistakes worth a real error message: passing the media TOKEN, and
    passing raw bytes.
    """
    fetch = getattr(photo, "fetch_data_uri", None)
    if callable(fetch):
        photo = fetch()
    if isinstance(photo, (bytes, bytearray)):
        raise ValueError(
            "photo_data_uri got raw bytes. Wrap them: "
            "'data:image/png;base64,' + base64.b64encode(b).decode()")
    if not isinstance(photo, str):
        raise ValueError(f"photo_data_uri must be a data URI string, got {type(photo).__name__}")
    if photo.startswith("@@@langfuseMedia:"):
        raise ValueError(
            "photo_data_uri got a Langfuse media TOKEN, not a data URI. A token is a "
            "reference, not pixels — this is exactly the substitution the demo's "
            "anti-pattern layer exists to expose. Hydrate it first: "
            "`item.input['photo'].fetch_data_uri()` on a dataset item, or "
            "`resolve_media_references(obj=..., resolve_with='base64_data_uri')` on a trace.")
    if not photo.startswith("data:"):
        raise ValueError("photo_data_uri must start with 'data:' (a base64 data URI)")
    return photo


def _split_data_uri(data_uri: str) -> Tuple[str, str]:
    """`data:image/png;base64,AAA` -> ('image/png', 'AAA')."""
    m = re.match(r"^data:([^;,]+)(;base64)?,(.*)$", data_uri, re.S)
    if not m:
        raise ValueError("could not parse the photo data URI")
    media_type, is_b64, payload = m.group(1).strip().lower(), m.group(2), m.group(3)
    if not is_b64:
        raise ValueError("photo data URI must be base64-encoded (';base64,')")
    if media_type not in _ANTHROPIC_IMAGE_TYPES:
        raise ValueError(
            f"unsupported image type {media_type!r}; Anthropic vision accepts "
            f"{', '.join(_ANTHROPIC_IMAGE_TYPES)}")
    return media_type, payload


def _vision_user_message(model: str, data_uri: str, text: str) -> Dict[str, Any]:
    """One user message carrying the image, in the provider's native block format.

    `agent/llm.py` hands `messages` straight to the provider SDK, so building the
    blocks here keeps the whole demo on one LLM layer (usage, cost_details and
    provider routing included) instead of forking a second client for vision.
    Anthropic's guidance is image-before-text; OpenAI takes the same data URI as
    an `image_url`, which keeps the Claude-vs-GPT comparison arm possible.
    """
    if provider_of(model) == "openai":
        return {"role": "user",
                "content": [{"type": "text", "text": text},
                            {"type": "image_url", "image_url": {"url": data_uri}}]}
    media_type, payload = _split_data_uri(data_uri)
    return {"role": "user",
            "content": [{"type": "image",
                         "source": {"type": "base64", "media_type": media_type,
                                    "data": payload}},
                        {"type": "text", "text": text}]}


def _normalize_token(value: Any) -> Any:
    """Lower-case and underscore a scalar so 'Light Modern' matches 'light_modern'.

    Repairing capitalisation and spacing is not the same as inventing a value: the
    model told us which bucket it meant, in a spelling the contract does not use.
    Anything that is not a plain string is returned untouched for the contract to
    judge.
    """
    if not isinstance(value, str):
        return value
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def _coerce_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):          # the contract rejects bools; so do we
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _sanitize_attributes(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Closed-vocabulary gate: normalize, then let the CONTRACT decide.

    Returns (clean_attributes, repairs). Every decision to keep or drop a key is
    made by `photo_contract.validate_attributes` on that single key, so this
    function cannot drift from the evaluators that score the result — it only
    normalizes spelling and shapes beforehand.

    Junk is dropped, never propagated, and every drop is recorded in `repairs`,
    which lands on the `vision_extract` observation. A silently repaired output
    would make the `closed-vocabulary` evaluator meaningless.
    """
    repairs: List[str] = []
    if not isinstance(raw, dict):
        return {}, [f"attributes was {type(raw).__name__}, not an object — dropped entirely"]

    clean: Dict[str, Any] = {}
    # Contract order, so the same extraction always renders identical text.
    for key in photo_contract.ATTRIBUTE_KEYS:
        if key not in raw:
            continue
        value = raw[key]

        if value is None:
            # JSON null means "I could not see this", which is an OMISSION.
            # "none" (a legal value for cabinetry/countertop) means "the room
            # visibly has none". Collapsing the two would invent an observation.
            repairs.append(f"{_BENIGN}{key}=null treated as not extracted (null != \"none\")")
            continue

        if key == "window_count":
            coerced = _coerce_int(value)
            if coerced is None:
                repairs.append(f"dropped {key}={value!r} — not an integer")
                continue
            if coerced != value:
                repairs.append(f"{_BENIGN}normalized {key}: {value!r} -> {coerced}")
            value = coerced
        elif key == "appliances":
            items = value if isinstance(value, list) else re.split(r"[,;]", str(value))
            seen: List[Any] = []
            for item in items:
                token = _normalize_token(item)
                if token in photo_contract.APPLIANCES:
                    if token in seen:
                        repairs.append(f"{_BENIGN}deduplicated appliance {token!r}")
                    else:
                        seen.append(token)
                elif token not in ("", None):
                    repairs.append(f"dropped appliance {item!r} — outside the closed vocabulary")
            if not isinstance(value, list):
                repairs.append(f"{_BENIGN}normalized appliances: {value!r} -> {seen}")
            value = seen
        else:
            token = _normalize_token(value)
            if token != value:
                repairs.append(f"{_BENIGN}normalized {key}: {value!r} -> {token!r}")
            value = token

        problems = photo_contract.validate_attributes({key: value})
        if problems:
            repairs.append(f"dropped {key}={raw[key]!r} — {'; '.join(problems)}")
            continue
        clean[key] = value

    for key in raw:
        if key not in photo_contract.ATTRIBUTE_KEYS:
            repairs.append(f"dropped unknown key {key!r} — outside the closed vocabulary")

    # Belt and braces: if the whole-dict check still complains, this function has a
    # bug. Say so in the trace rather than shipping something the evaluators will
    # then fail on for reasons nobody can see.
    residual = photo_contract.validate_attributes(clean)
    if residual:
        repairs.append("SANITIZER BUG — validate_attributes still reports: "
                       + "; ".join(residual))
    return clean, repairs


def _coerce_confidence(value: Any) -> Optional[float]:
    """0..1 float, or None when the model did not give us one."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        conf = float(value)
    elif isinstance(value, str):
        try:
            conf = float(value.strip().rstrip("%"))
        except ValueError:
            return None
        if conf > 1.0:          # "82%" / "82"
            conf = conf / 100.0
    else:
        return None
    return max(0.0, min(1.0, conf))


def _attribute_text(attrs: Dict[str, Any], confidence: float) -> str:
    """The extraction as structured text. THIS is what a text-only judge scores.

    Load-bearing, not a nicety: layer B (the managed proxy judge) never sees the
    photo, so this string is its entire universe. Values stay as the closed
    vocabulary's exact tokens (`dark_dated`, not "dark and dated") so a judge, a
    human and a regex all read the same thing.

    `not_observed` is included on purpose. "The proxy judge cannot report what the
    extractor never wrote down" is the finding this demo is chasing, and hiding the
    omissions would be manufacturing it — the honest version still lands, because
    the judge can flag that it is blind but still cannot recover the pixels.
    """
    lines = [f"{key}: " + (", ".join(attrs[key]) if isinstance(attrs[key], list)
                           else str(attrs[key]))
             for key in photo_contract.ATTRIBUTE_KEYS if key in attrs]
    missing = [k for k in photo_contract.ATTRIBUTE_KEYS if k not in attrs]
    lines.append("not_observed: " + (", ".join(missing) if missing else "(none)"))
    lines.append(f"extraction_confidence: {confidence:.2f}")
    return "\n".join(lines)


def _normalize_verdict(value: Any) -> Tuple[str, Optional[str]]:
    """(verdict, repair_note). Unknown spellings abstain rather than guess."""
    token = _normalize_token(value)
    if token in photo_contract.VERDICTS:
        return token, None
    mapped = _VERDICT_ALIASES.get(token)
    if mapped:
        return mapped, f"{_BENIGN}normalized verdict {value!r} -> {mapped!r}"
    return "unverifiable", (f"verdict {value!r} is outside {photo_contract.VERDICTS} "
                            "— abstained instead of guessing")


def _listing_facts(listing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A compact, FACTUAL slice of the catalog record for the audit's context.

    `description` and `features` are excluded deliberately. Both are marketing text
    (`features` literally contains "renovated"), and feeding the auditor a second
    sales pitch as "context" would prime it toward the copy it is supposed to be
    checking — contaminating the headline metric for reasons that have nothing to
    do with the photo.
    """
    if not listing:
        return {}
    keys = ("id", "city", "neighborhood", "property_type", "operation", "price",
            "bedrooms", "bathrooms", "size_m2", "year_built", "energy_rating")
    return {k: listing[k] for k in keys if k in listing}


# How each attribute reads in the corrected description. A per-value map where a
# template would read badly; `{v}` otherwise. Any value the contract adds later
# falls through to a plain "<key> <value>", so a vocabulary change degrades the
# prose rather than crashing the audit.
_SUMMARY_PHRASES: Dict[str, Any] = {
    "cabinetry": "{v} cabinetry",
    "countertop": "{v} countertops",
    "flooring": "{v} flooring",
    "clutter": {"clear": "clear surfaces", "moderate": "some clutter",
                "cluttered": "visible clutter"},
    "natural_light": "{v} natural light",
    "condition": {"renovated": "a recently renovated finish", "dated": "a dated finish",
                  "needs_work": "a finish that needs work"},
}


def _photo_summary_sentence(attrs: Dict[str, Any],
                            contradicted_claims: List[str]) -> str:
    """One sentence describing ONLY what the extractor recorded.

    This is the "corrected description" half of the task: the copy the photo does
    support, written from the photo rather than from the original claims.

    Deterministic by construction, so — unlike an LLM rewrite — it cannot invent a
    feature. But "cannot invent" is not quite enough: if the auditor contradicted a
    claim that the EXTRACTION would nonetheless support, the pipeline disagrees with
    itself, and restating the extraction here would re-assert the claim the audit
    just struck out. The contract's own claim predicates detect that, so we drop the
    sentence instead of papering over the disagreement.
    """
    if not attrs:
        return ""
    # Model-extracted interpretive values win; `derive_interpretive` only fills the
    # gaps so a predicate can still be evaluated. This is a self-consistency check
    # ONLY — it never enters `extracted_attributes`, and it is never shown to a
    # model (that derivation is ground-truth machinery; leaking it would neuter the
    # `contradicted_subtle` class).
    full = {**photo_contract.derive_interpretive(attrs), **attrs}
    for text in contradicted_claims:
        spec = photo_contract.CLAIMS_BY_TEXT.get(text)
        predicate = spec.get("predicate") if spec else None
        if predicate is None:
            continue
        try:
            if predicate(full):
                return ""
        except Exception:
            continue

    room = str(attrs.get("room_type", "room")).replace("_", " ")
    bits: List[str] = []
    for key in ("cabinetry", "countertop", "flooring", "clutter", "natural_light",
                "condition"):
        if key not in attrs or attrs[key] == "none":
            continue
        value = str(attrs[key]).replace("_", " ")
        phrase = _SUMMARY_PHRASES.get(key)
        if isinstance(phrase, dict):
            bits.append(phrase.get(attrs[key], f"{key.replace('_', ' ')} {value}"))
        elif isinstance(phrase, str):
            bits.append(phrase.format(v=value))
        else:
            bits.append(f"{key.replace('_', ' ')} {value}")
    if "window_count" in attrs:
        n = attrs["window_count"]
        bits.append(f"{n} window" + ("" if n == 1 else "s"))
    if attrs.get("appliances"):
        bits.append("visible appliances: " + ", ".join(attrs["appliances"]))
    if not bits:
        return f"The photo shows a {room}."
    return f"The photo shows a {room} with " + ", ".join(bits) + "."


# ==========================================================================
# Graph state
# ==========================================================================
# Designed for the JUDGE, not just for the runtime. The LangChain callback handler
# logs each node's observation input as the state at node entry, so this TypedDict
# IS the text a managed judge reads when it matches a node by name — which is why
# every field is short, readable and free of base64 (see the module docstring).

class AuditState(TypedDict, total=False):
    # inputs
    listing_id: str
    marketing_copy: str
    claims: List[str]
    # vision_extract
    extracted_attributes: Dict[str, Any]
    attribute_text: str
    extraction_confidence: float
    extraction_repairs: List[str]
    # retrieve_listing
    listing_facts: Dict[str, Any]
    listing_found: bool
    cited_listing_id: Optional[str]
    # audit_claims / compose_answer / request_better_photo
    claim_results: List[Dict[str, str]]
    audit_repairs: List[str]
    verdict: str
    corrected_copy: str
    branch: str


# ==========================================================================
# The graph
# ==========================================================================

def build_photo_audit_graph(*, photo_data_uri: str, model: str, lf,
                            refs: Optional[Dict[str, Any]] = None):
    """Compile the audit graph with one photo bound into its nodes.

    Built per call rather than once at import, so the image never has to travel in
    the graph state (module docstring, point 5). Exposed publicly because a
    diagram/CLI may want the compiled graph; `run_photo_audit` is the entry point
    everything else should use.

    `refs`, if given, collects the ids of the manual observations we create, for
    callers that need to attach observation-level scores afterwards.
    """
    # Imported lazily so seeders and code evaluators can import this module for its
    # constants without LangGraph installed — and so the failure, when the deps are
    # genuinely missing, names the non-obvious one.
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as e:  # pragma: no cover - dependency guidance
        raise RuntimeError(
            "the photo audit needs langgraph — `pip install -r requirements.txt` "
            "(and note `langchain`, the meta-package, is required too: "
            "langfuse.langchain imports it directly)") from e

    refs = refs if refs is not None else {}

    # ---------------- 1) vision_extract ----------------------------------
    def vision_extract(state: AuditState) -> Dict[str, Any]:
        """Photo -> closed-vocabulary attributes + a self-reported confidence.

        The node's OUTPUT carries the attribute text, because that text is the
        entire input to the text-only proxy judge (layer B) and to any evaluator
        asking "did the extractor write the deciding attribute down?".
        """
        text_prompt = ("Extract the visible attributes of this property photo as JSON, "
                       "following the schema and closed vocabulary exactly.")
        with lf.start_as_current_observation(
            as_type="generation", name=OBS_VISION_CALL, model=model
        ) as gen:
            # The data URI goes in the observation input as a plain string: that is
            # what makes Langfuse extract it into stored media, render the photo
            # inline in the trace, and leave a `@@@langfuseMedia:...@@@` token
            # behind — the token layer D's anti-pattern judge is pointed at.
            gen.update(input={"photo": photo_data_uri, "instruction": text_prompt})
            res = call_llm(model, VISION_SYSTEM,
                           [_vision_user_message(model, photo_data_uri, text_prompt)],
                           max_tokens=700)
            parsed = _extract_json(res["text"])
            attrs, repairs = _sanitize_attributes(parsed.get("attributes"))
            confidence = _coerce_confidence(parsed.get("extraction_confidence"))
            if confidence is None:
                # No confidence means we do not know whether we read the photo. The
                # safe reading is "we did not" — which routes to
                # request_better_photo instead of auditing copy against guesses.
                repairs.append("extraction_confidence missing or unparseable — "
                               "treated as 0.0, which routes to request_better_photo")
                confidence = 0.0
            if not parsed:
                repairs.append("model reply was not JSON — no attributes extracted")

            attribute_text = _attribute_text(attrs, confidence)
            defects = _defects(repairs)
            gen.update(
                output={"attributes": attrs, "attribute_text": attribute_text,
                        "extraction_confidence": confidence},
                usage_details=res["usage"],
                # The raw reply stays on the generation so a failed extraction is
                # debuggable without a re-run, and the repair list makes "we
                # dropped something" visible instead of implicit.
                metadata={"raw_model_json": parsed, "repairs": repairs,
                          "stop_reason": res.get("stop_reason")},
                level="WARNING" if defects else None,
                status_message="; ".join(defects)[:500] if defects else None,
                **({"cost_details": res["cost_details"]} if res.get("cost_details") else {}),
            )
            refs["vision_observation_id"] = gen.id
        return {"extracted_attributes": attrs, "attribute_text": attribute_text,
                "extraction_confidence": confidence, "extraction_repairs": repairs}

    # ---------------- conditional edge -----------------------------------
    def route_after_extract(state: AuditState) -> str:
        """Below the contract's floor we ask for a better photo instead of auditing.

        The self-correct branch is the point: an agent that audits copy against a
        photo it could not read produces a confident, worthless verdict — the same
        failure mode as the anti-pattern judge, one layer up.
        """
        if float(state.get("extraction_confidence") or 0.0) < photo_contract.CONFIDENCE_FLOOR:
            return photo_contract.NODE_REQUEST_BETTER_PHOTO
        return photo_contract.NODE_RETRIEVE_LISTING

    # ---------------- 2) retrieve_listing --------------------------------
    def retrieve_listing(state: AuditState) -> Dict[str, Any]:
        """Catalog lookup, as a tool-style span inside the node."""
        listing_id = state["listing_id"]
        with lf.start_as_current_observation(
            as_type="tool", name=OBS_LISTING_LOOKUP
        ) as tool:
            tool.update(input={"listing_id": listing_id})
            listing = get_listing(listing_id)
            facts = _listing_facts(listing)
            tool.update(output={"found": bool(listing), "listing": facts},
                        level=None if listing else "WARNING",
                        status_message=None if listing else f"{listing_id} not in catalog")
        # `cited_listing_id` echoes the id we actually audited, so `listing-cited`
        # is a traceability check (did the audit say what it audited?), not a
        # grounding one. The grounding signal is `listing_found`, which is on the
        # tool observation above and in the state.
        return {"listing_facts": facts, "listing_found": bool(listing),
                "cited_listing_id": listing_id}

    # ---------------- 3) audit_claims ------------------------------------
    def audit_claims(state: AuditState) -> Dict[str, Any]:
        """Adjudicate every claim to supported / contradicted / unverifiable.

        Claims are numbered and adjudications come back BY INDEX, so a paraphrased
        claim in the reply cannot be mismatched to the wrong input claim — the
        output always carries the claim text verbatim as it was given to us.
        """
        claims: List[str] = list(state.get("claims") or [])
        numbered = "\n".join(f"[{i}] {c}" for i, c in enumerate(claims))
        context = (
            f"Listing record (context only, NOT photo evidence):\n"
            f"{json.dumps(state.get('listing_facts') or {}, ensure_ascii=False)}\n\n"
            f"Photo evidence extracted from the image:\n{state.get('attribute_text', '')}\n\n"
            f"Marketing copy under audit:\n{state.get('marketing_copy', '')}\n\n"
            f"Claims to adjudicate:\n{numbered}"
        )

        repairs: List[str] = []
        with lf.start_as_current_observation(
            as_type="generation", name=OBS_AUDIT_CALL, model=model
        ) as gen:
            gen.update(input={"claims": claims,
                              "marketing_copy": state.get("marketing_copy", ""),
                              "attribute_text": state.get("attribute_text", ""),
                              "listing_facts": state.get("listing_facts") or {},
                              "sees_pixels": AUDIT_CLAIMS_SEES_PIXELS})
            if AUDIT_CLAIMS_SEES_PIXELS:
                messages = [_vision_user_message(model, photo_data_uri, context)]
            else:
                messages = [{"role": "user", "content": context}]
            res = call_llm(model, AUDIT_SYSTEM, messages, max_tokens=1200)
            parsed = _extract_json(res["text"])
            if not parsed:
                repairs.append("model reply was not JSON — every claim abstained")

            by_index: Dict[int, Dict[str, Any]] = {}
            for entry in (parsed.get("adjudications") or []):
                if not isinstance(entry, dict):
                    continue
                idx = _coerce_int(entry.get("claim_index"))
                if idx is None or not (0 <= idx < len(claims)):
                    # Fall back to matching on the claim text before discarding it.
                    text = str(entry.get("claim") or "").strip().lower()
                    idx = next((i for i, c in enumerate(claims)
                                if c.strip().lower() == text), None)
                if idx is None:
                    repairs.append(f"discarded adjudication with unusable index: {entry!r}"[:200])
                    continue
                if idx in by_index:
                    repairs.append(f"duplicate adjudication for claim [{idx}] — kept the first")
                    continue
                by_index[idx] = entry

            claim_results: List[Dict[str, str]] = []
            for i, claim in enumerate(claims):
                entry = by_index.get(i)
                if entry is None:
                    # Fill rather than drop, but mark it detectably: see
                    # UNADJUDICATED_EVIDENCE.
                    repairs.append(f"claim [{i}] was not adjudicated — filled as unverifiable")
                    claim_results.append({"claim": claim, "verdict": "unverifiable",
                                          "evidence": UNADJUDICATED_EVIDENCE})
                    continue
                verdict, note = _normalize_verdict(entry.get("verdict"))
                if note:
                    # Keep the benign prefix leading, so _defects() still sees it.
                    repairs.append(f"{_BENIGN}claim [{i}]: {note[len(_BENIGN):]}"
                                   if note.startswith(_BENIGN) else f"claim [{i}]: {note}")
                evidence = str(entry.get("evidence") or "").strip()
                claim_results.append({"claim": claim, "verdict": verdict,
                                      "evidence": evidence[:_EVIDENCE_MAX_CHARS]})

            defects = _defects(repairs)
            gen.update(output={"claims": claim_results},
                       usage_details=res["usage"],
                       metadata={"repairs": repairs, "stop_reason": res.get("stop_reason")},
                       level="WARNING" if defects else None,
                       status_message="; ".join(defects)[:500] if defects else None,
                       **({"cost_details": res["cost_details"]} if res.get("cost_details") else {}))
            refs["audit_observation_id"] = gen.id

        # The node's own output is the adjudication and nothing else — this is the
        # observation the managed proxy judge targets by name.
        return {"claim_results": claim_results, "audit_repairs": repairs}

    # ---------------- 4) compose_answer ----------------------------------
    def compose_answer(state: AuditState) -> Dict[str, Any]:
        """Roll the per-claim verdicts up, and rebuild the copy the photo supports.

        No LLM call here, for two reasons. The verdict is a CONTRACT decision
        (`photo_contract.overall_verdict` — one contradiction dominates), not a
        judgement, and making that visible in the trace is itself a teaching point.
        And `corrected_copy` has no ground truth, so an LLM rewrite would buy
        nothing measurable while adding a fresh hallucination surface: it could
        re-assert the very claim the audit just contradicted.
        """
        claim_results = list(state.get("claim_results") or [])
        supported = [c["claim"] for c in claim_results if c.get("verdict") == "supported"]
        contradicted = [c["claim"] for c in claim_results
                        if c.get("verdict") == "contradicted"]
        corrected = photo_contract.marketing_copy(supported)
        summary = _photo_summary_sentence(state.get("extracted_attributes") or {},
                                          contradicted)
        return {"verdict": photo_contract.overall_verdict(claim_results),
                "corrected_copy": " ".join(p for p in (corrected, summary) if p),
                "branch": photo_contract.NODE_COMPOSE_ANSWER}

    # ---------------- 5) request_better_photo (terminal) -----------------
    def request_better_photo(state: AuditState) -> Dict[str, Any]:
        """Self-correct: refuse to audit copy against a photo we could not read.

        Every claim comes back `unverifiable` — truthfully, since an unreadable
        photo settles nothing — so `claim-coverage` still sees a complete audit and
        the abstention is explicit rather than an empty list.

        The overall verdict is set directly, NOT via `overall_verdict()`: that
        function maps all-unverifiable to "unverifiable", and this branch needs the
        contract's fourth value, `needs_better_photo`, which only the graph can
        produce and which is the whole reason the branch exists.
        """
        conf = float(state.get("extraction_confidence") or 0.0)
        reason = (f"photo unreadable — extraction confidence {conf:.2f} is below the "
                  f"{photo_contract.CONFIDENCE_FLOOR:.2f} floor")
        claims = list(state.get("claims") or [])
        return {
            "claim_results": [{"claim": c, "verdict": "unverifiable", "evidence": reason}
                              for c in claims],
            "verdict": "needs_better_photo",
            # Not copy, deliberately: nothing in this listing's copy can be
            # republished on this evidence, so the "corrected copy" is a hold.
            "corrected_copy": (
                f"Cannot verify this listing's copy from the photo supplied ({reason}). "
                "Please add a brighter, in-focus photo of the room before publishing."),
            "cited_listing_id": state.get("listing_id"),
            "branch": photo_contract.NODE_REQUEST_BETTER_PHOTO,
        }

    # ---------------- wiring ---------------------------------------------
    # Node KEYS are the contract's names: LangGraph names each node observation
    # after its key, and the seeded managed judges filter on those names. Renaming
    # one here would stop a judge firing with no error anywhere.
    graph = StateGraph(AuditState)
    graph.add_node(photo_contract.NODE_VISION_EXTRACT, vision_extract)
    graph.add_node(photo_contract.NODE_RETRIEVE_LISTING, retrieve_listing)
    graph.add_node(photo_contract.NODE_AUDIT_CLAIMS, audit_claims)
    graph.add_node(photo_contract.NODE_COMPOSE_ANSWER, compose_answer)
    graph.add_node(photo_contract.NODE_REQUEST_BETTER_PHOTO, request_better_photo)

    graph.add_edge(START, photo_contract.NODE_VISION_EXTRACT)
    graph.add_conditional_edges(
        photo_contract.NODE_VISION_EXTRACT, route_after_extract,
        # An explicit path map (rather than bare returned names) so both branches
        # show up when the graph is drawn — the untaken one is absent from a trace
        # by design, and a picture is how you explain that on a call.
        {photo_contract.NODE_REQUEST_BETTER_PHOTO: photo_contract.NODE_REQUEST_BETTER_PHOTO,
         photo_contract.NODE_RETRIEVE_LISTING: photo_contract.NODE_RETRIEVE_LISTING},
    )
    graph.add_edge(photo_contract.NODE_RETRIEVE_LISTING, photo_contract.NODE_AUDIT_CLAIMS)
    graph.add_edge(photo_contract.NODE_AUDIT_CLAIMS, photo_contract.NODE_COMPOSE_ANSWER)
    graph.add_edge(photo_contract.NODE_COMPOSE_ANSWER, END)
    graph.add_edge(photo_contract.NODE_REQUEST_BETTER_PHOTO, END)
    return graph.compile()


def _audit_summary(state: AuditState) -> Dict[str, Any]:
    """The graph's final state as EXACTLY the photo_contract §4 schema.

    Exactly, in the contract's key order: every evaluator across the four layers
    consumes this dict, and the root observation's output is this dict, so an extra
    or renamed key here is a broken evaluator somewhere else.
    """
    claim_results = list(state.get("claim_results") or [])
    summary = {
        "listing_id": state.get("listing_id"),
        "extracted_attributes": state.get("extracted_attributes") or {},
        "extraction_confidence": float(state.get("extraction_confidence") or 0.0),
        "claims": claim_results,
        # Defensive recompute: both terminal nodes set a verdict, and if neither
        # did, the contract's roll-up is the only right answer to fall back on.
        "verdict": state.get("verdict") or photo_contract.overall_verdict(claim_results),
        "corrected_copy": state.get("corrected_copy") or "",
        "cited_listing_id": state.get("cited_listing_id"),
    }
    assert tuple(summary) == photo_contract.AUDIT_KEYS, "audit summary drifted from the contract"
    return summary


def run_photo_audit(
    *,
    listing_id: str,
    marketing_copy: str,
    claims: List[str],
    photo_data_uri: Any,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    extra_tags: Optional[List[str]] = None,
    is_experiment: bool = False,
    model: Optional[str] = None,
    include_trace_refs: Optional[bool] = None,
) -> Dict[str, Any]:
    """Audit one listing photo against one piece of marketing copy.

    One call = one trace, rooted at the `audit-listing-photo` wrapper span, exactly
    as `concierge.run_turn` is one turn = one trace. Returns the
    `photo_contract` §4 audit schema and nothing else.

    `photo_data_uri` is a base64 data URI. In an experiment it comes from
    `item.input["photo"].fetch_data_uri()`; a `LangfuseMediaReference` is also
    accepted and resolved here. Either way it is resolved PER CALL — Langfuse media
    URLs are signed and expire, so a data URI cached across a long run can 403
    halfway through.

    `is_experiment=True` skips trace-level attribute setting and the flush: the
    experiment owns the trace, same contract as `run_turn`.

    `include_trace_refs` adds `trace_id`, the ids of the manual observations, and
    `requested_claims` to the returned dict — for a caller that wants to attach
    observation-level scores, or to score `claim-coverage` on live traffic. It
    defaults to `not is_experiment`, which is the split that matters:

      * live path — extras included, exactly as `run_turn` returns `trace_id` and
        `final_generation_id`. Nothing evaluator-facing reads this dict.
      * experiment path — refs OMITTED, so what the runner records as the item's
        output is EXACTLY the contract §4 schema, which is what every evaluator
        across the four layers consumes.

    Either way the ROOT OBSERVATION's output is the contract schema and nothing
    else — that is the part judges and annotation queues read, and it never varies.
    """
    lf = get_langfuse()
    model = model or AGENT_MODEL
    claims = list(claims or [])
    photo_data_uri = _resolve_data_uri(photo_data_uri)

    # Trace-level attributes. `propagate_attributes` wraps the ROOT SPAN's creation
    # here (concierge enters it just inside its root instead — both work, but this
    # ordering is the one measured on the LangGraph path in
    # scripts/verify_multimodal.py, where every node observation came back carrying
    # the propagated userId/sessionId/tags). Without that fan-out, observation-level
    # evaluators filter on attributes the observations do not have and match
    # nothing, silently.
    if is_experiment:
        ctx: Any = nullcontext()
    else:
        ctx = propagate_attributes(
            # Stable and low-cardinality, and identical to the root span's name so
            # newer Langfuse UIs render them as one node. The listing and the
            # claims live in the trace INPUT, never in the name.
            trace_name=photo_contract.ROOT_SPAN,
            session_id=session_id,
            user_id=user_id,
            tags=photo_contract.BASE_TAGS + list(extra_tags or []),
            # Short scalars only — propagated metadata is stringified and capped at
            # 200 chars. `agent_model` is here because observation-level rules
            # filter on it.
            metadata={"agent_model": model, "provider": provider_of(model)},
        )

    refs: Dict[str, Any] = {}
    with ctx:
        with lf.start_as_current_observation(
            as_type="span", name=photo_contract.ROOT_SPAN
        ) as root:
            trace_id = lf.get_current_trace_id()
            refs["trace_id"] = trace_id
            refs["root_observation_id"] = root.id
            # The root observation's input/output IS the trace's input/output in v4
            # (observations-first — no `set_current_trace_io`). Input mirrors the
            # dataset-item shape and output mirrors the §4 audit schema, so a trace,
            # a dataset item and an experiment result all read the same way in the
            # UI, in an annotation queue, and to a judge filtered on
            # "Is Root Observation" — which sees ONLY this observation.
            #
            # The photo rides along on the root input so it renders inline on the
            # trace (the first beat of the demo) and so a human working the
            # annotation queue can actually see what they are correcting. Langfuse
            # dedupes media by content hash, so the same bytes on the generation
            # observation cost one stored object, not two.
            root.update(
                input={"listing_id": listing_id, "marketing_copy": marketing_copy,
                       "claims": claims, "photo": photo_data_uri},
                metadata={"agent_model": model, "provider": provider_of(model),
                          "confidence_floor": photo_contract.CONFIDENCE_FLOOR,
                          "audit_claims_sees_pixels": AUDIT_CLAIMS_SEES_PIXELS},
            )

            graph = build_photo_audit_graph(photo_data_uri=photo_data_uri, model=model,
                                            lf=lf, refs=refs)
            # The CallbackHandler is what turns nodes into observations; it is built
            # per call because it keeps per-run state. `run_name` names LangGraph's
            # own CHAIN root — without it the trace shows a generic `LangGraph`
            # node, and the contract fixes the name so a trace is recognisable.
            from langfuse.langchain import CallbackHandler

            final_state = graph.invoke(
                {"listing_id": listing_id, "marketing_copy": marketing_copy,
                 "claims": claims},
                config={"callbacks": [CallbackHandler()],
                        "run_name": photo_contract.GRAPH_RUN_NAME},
            )

            audit = _audit_summary(final_state)
            root.update(output=audit,
                        # Filterable outcome on the root, so "show me the traces
                        # that routed to request_better_photo" is one query.
                        metadata={"verdict": audit["verdict"],
                                  "branch": final_state.get("branch"),
                                  # Defects, not every repair note: "traces where
                                  # the extractor emitted something invalid" is a
                                  # question worth being able to ask.
                                  "extraction_defects": len(_defects(
                                      final_state.get("extraction_repairs") or [])),
                                  "audit_defects": len(_defects(
                                      final_state.get("audit_repairs") or []))})

    if not is_experiment:
        flush_langfuse(lf)

    if include_trace_refs is None:
        include_trace_refs = not is_experiment
    if include_trace_refs:
        # `requested_claims` echoes the input claim list, which the §4 schema does
        # not carry. `agent/photo_scoring.py::_requested_claims` reads exactly this
        # key so `claim-coverage` has a denominator on LIVE traffic, where there is
        # no dataset item to supply one — without it that evaluator abstains rather
        # than inventing a pass. It rides with the trace refs, not inside the audit:
        # the root observation's output, which judges and annotation queues read,
        # stays exactly the contract schema.
        return {**audit, **refs, "requested_claims": claims}
    return audit


__all__ = [
    "AUDIT_CLAIMS_SEES_PIXELS",
    "OBS_AUDIT_CALL",
    "OBS_LISTING_LOOKUP",
    "OBS_VISION_CALL",
    "UNADJUDICATED_EVIDENCE",
    "AuditState",
    "build_photo_audit_graph",
    "run_photo_audit",
]

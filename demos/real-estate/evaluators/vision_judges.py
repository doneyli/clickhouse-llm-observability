"""
Layer A of the photo-audit eval stack: SDK judges that actually SEE THE PIXELS.

Two item-level experiment evaluators, in the shape `dataset.run_experiment(...)`
calls — `(*, input, output, expected_output, metadata, **kwargs) -> Evaluation`:

  photo-copy-consistency   is each per-claim verdict right GIVEN THE PHOTO?
  extraction-fidelity      do the extracted attributes actually describe it?

WHY THIS LAYER EXISTS AT ALL
    Langfuse's *managed* evaluators are text-only: they interpolate the stored
    field, and for a media field the stored field is the bare string
    `@@@langfuseMedia:...@@@` (verified in `scripts/verify_multimodal.py`
    check 3). They also run sandboxed with no network egress, so a managed code
    evaluator could not fetch the image even if it wanted to. An SDK evaluator
    runs in OUR process, so it may do exactly what these two do: resolve a
    `LangfuseMediaReference` over the network and put real bytes in front of a
    vision model. That asymmetry is the demo's whole pedagogical payload
    (MULTIMODAL_EVAL_SPEC.md §1, §3.3).

ONE PRINCIPLE, APPLIED THROUGHOUT: NEVER SCORE 0.0 FOR OUR OWN BREAKAGE
    A 0.0 means "the agent did badly". If the evaluator got no image, or the
    media URL had expired, or the vision model returned prose instead of JSON,
    the agent did nothing at all — and a 0.0 there is a false accusation that
    reads, in the Runs tab, exactly like a quality regression. Every such case
    returns the `NOT_SCORED` sentinel below, tagged with which of the four kinds
    of not-measurable it is (see `_not_scored`) and a comment naming the cause.

    ⚠️ The sentinel is a CATEGORICAL string, not `value=None`, and that is
    deliberate — `langfuse.api.ScoreBody.value` is typed `Union[float, str]` and
    REJECTS None (verified against SDK 4.14.4), so a None-valued Evaluation is
    swallowed by `create_score`'s except-and-log and never reaches Langfuse. The
    comment would then exist only in the local run printout: i.e. the
    misconfiguration would be invisible in the UI, which is the opposite of the
    point. A categorical value lands, carries its comment, and is skipped by the
    numeric run-level means (they only average int/float/bool), so it cannot
    quietly drag `avg-photo-copy-consistency` down either. What it DOES do is
    shrink the denominator — the mean's own comment reports "over N items", so a
    run where half the items lost their media reads as `over 10 items` and
    invites the question.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from langfuse import Evaluation
from langfuse.media import LangfuseMediaReference

from agent import photo_contract as pc
from agent import photo_scoring

# Reuse — not re-implement — the concierge demo's run-level mean aggregator, so
# `avg-*` means one thing across the whole project. Reaching for a private name
# is deliberate: the day photo-audit needs different aggregation semantics is the
# day to copy it, and until then a fix there should apply here.
from evaluators.experiment_evaluators import _mean_evaluator

# The sentinel value for "this evaluator could not run". See the module docstring.
NOT_SCORED = "NOT_SCORED"
_MEDIA_TOKEN_PREFIX = "@@@langfuseMedia:"

_JUDGE_SYSTEM = (
    "You are a meticulous property-photo auditor. You are shown a photograph and "
    "asked about it. You answer ONLY from what is visible in the image — never "
    "from what the accompanying text asserts — and you return ONLY compact JSON."
)


# --------------------------------------------------------------- plumbing ----
def _d(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}


def _not_scored(name: str, reason: str, kind: str = "misconfigured") -> Evaluation:
    """An explicitly unscored result, tagged with WHOSE problem it is.

    Four kinds, because "not scored" alone sends you looking in the wrong place:
      misconfigured   — media plumbing: no reference, wrong type, unresolvable.
      judge error     — the vision model itself misbehaved (prose, not JSON).
      not applicable  — nothing to measure; often the correct agent behaviour.
      dataset         — the item lacks the ground truth the check needs.
    """
    return Evaluation(name=name, value=NOT_SCORED, data_type="CATEGORICAL",
                      comment=f"NOT SCORED ({kind}) — {reason}")


def _lst(items, limit: int = 4) -> str:
    vals = [str(i) for i in items]
    return ", ".join(vals[:limit]) + (f" (+{len(vals) - limit} more)"
                                      if len(vals) > limit else "")


def _photo_reference(input: Any) -> Tuple[Optional[LangfuseMediaReference], Optional[str]]:
    """Resolve `input["photo"]`, or explain precisely what arrived instead.

    Each branch is a real failure mode with a different fix, which is why they
    do not collapse into one "no media" string:
      * missing field   — the seeder never attached a photo.
      * raw token       — the item was not read through the SDK (a REST read, a
                          hand-built dict), or langfuse < 4.10, which does not
                          hydrate media in dataset items at all.
      * plain string    — someone put a path or a URL where LangfuseMedia goes.
    """
    photo = _d(input).get("photo")
    if isinstance(photo, LangfuseMediaReference):
        return photo, None
    if photo is None:
        return None, ("the dataset item has no `photo` field, so this evaluator was "
                      "never shown an image. Nothing about the agent's output is "
                      "being measured here.")
    if isinstance(photo, str) and photo.startswith(_MEDIA_TOKEN_PREFIX):
        return None, (f"`photo` arrived as the RAW media token "
                      f"{photo[:56]}... instead of a hydrated LangfuseMediaReference. "
                      "The item was not read through the SDK's dataset API, or the "
                      "installed langfuse is < 4.10. This is exactly the string a "
                      "managed text-only judge is handed — see the layer-D "
                      f"anti-pattern exhibit, {pc.SCORE_ANTIPATTERN}.")
    if isinstance(photo, str):
        return None, (f"`photo` is a plain string ({photo[:56]}...), not media. A path "
                      "or URL has been put where a LangfuseMedia belongs; the image "
                      "was never uploaded.")
    return None, (f"`photo` is a {type(photo).__name__}, not a LangfuseMediaReference.")


def _split_data_uri(uri: str) -> Tuple[str, str]:
    """`data:image/png;base64,AAA` -> ("image/png", "AAA")."""
    if not isinstance(uri, str) or "," not in uri or not uri.startswith("data:"):
        raise ValueError(f"not a data URI: {str(uri)[:40]!r}")
    header, payload = uri.split(",", 1)
    mime = header[len("data:"):].split(";", 1)[0]
    return mime or "image/png", payload


def _vision_json(prompt: str, data_uri: str,
                 max_tokens: int = 900) -> Tuple[Dict[str, Any], str]:
    """One vision call. Returns (parsed_json, raw_text); parsed is {} if unusable.

    JUDGE_MODEL must be VISION-CAPABLE. Point it at a text-only model and these
    two judges fail loudly (an API error surfacing as NOT SCORED), which is the
    correct outcome — silently degrading to a text judge is the mistake this
    whole demo is about.

    Imported lazily, exactly as `agent.scoring._judge_call` does: `agent.config`
    reads LANGFUSE_* at import time, and importing this module must not require
    credentials — the pure code evaluators are unit-tested with none.
    """
    from agent.config import JUDGE_MODEL, get_anthropic

    mime, b64 = _split_data_uri(data_uri)
    resp = get_anthropic().messages.create(
        model=JUDGE_MODEL, max_tokens=max_tokens, system=_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image",
             "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": prompt}]}],
    )
    text = resp.content[0].text if resp.content else ""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}, text
    try:
        parsed = json.loads(match.group(0))
        return (parsed if isinstance(parsed, dict) else {}), text
    except (json.JSONDecodeError, ValueError):
        return {}, text


def _unit(raw: Any) -> Optional[float]:
    """Coerce a judge's number onto 0..1, tolerating 1-5 / 1-10 / 0-100 scales.

    Mirrors `agent.scoring._numeric_judge`'s tolerance so a judge that ignores
    the rubric's scale is rescued rather than scored 0.
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val > 1.0:
        val = val / 5.0 if val <= 5 else (val / 10.0 if val <= 10 else val / 100.0)
    return max(0.0, min(1.0, val))


def _expired(photo: LangfuseMediaReference) -> Any:
    """Best-effort "was the signed URL already stale?", for a failure comment.

    `is_url_expired` is a plain METHOD on SDK 4.14 (not the property its name
    suggests), so this both calls it and tolerates it becoming a property later.
    Anything that goes wrong here is swallowed: this only ever runs while
    building an error message, and an exception raised from that path would lose
    the diagnostic it exists to carry.
    """
    try:
        val = photo.is_url_expired
        return val() if callable(val) else val
    except Exception:  # noqa: BLE001
        return "unknown"


def _fetch_uri(name: str, photo: LangfuseMediaReference) -> Tuple[Optional[str], Optional[Evaluation]]:
    """Resolve pixels, or return the NOT_SCORED Evaluation to hand straight back.

    A media URL that has expired, or an S3/MinIO hiccup, is infrastructure —
    same rule as a missing reference: it must not read as a quality problem.
    """
    try:
        return photo.fetch_data_uri(), None
    except Exception as e:  # noqa: BLE001 — any transport failure is the same story
        return None, _not_scored(
            name, f"the media reference did not resolve ({type(e).__name__}: {e}). "
                  "The image never reached the judge, so this says nothing about the "
                  "agent. Check media storage and whether the signed URL expired "
                  f"(url_expired={_expired(photo)}).")


# =============================================================================
# JUDGE 1 — photo-copy-consistency
# =============================================================================
_CONSISTENCY_PROMPT = """\
This is the photograph a property listing was audited against.

=== THE LISTING COPY ===
{copy}

=== THE AUDITOR'S PER-CLAIM VERDICTS (what you are checking) ===
{verdicts}

For EACH claim, decide the verdict the PHOTOGRAPH supports, using exactly this \
vocabulary:
  "supported"    — the photo bears the claim out.
  "contradicted" — the photo positively refutes it.
  "unverifiable" — the photo cannot settle it either way. This is the CORRECT \
answer for anything a photograph of this room simply cannot show (street noise, \
service charges, what the neighbours are like). Abstaining is right there; \
guessing is wrong.

Judge only from the image. Do not be influenced by the auditor's verdict, and do \
not treat confident copy as evidence.

Reply with ONLY this JSON:
{{"claims": [{{"claim": "<claim text, verbatim>",
              "photo_verdict": "supported"|"contradicted"|"unverifiable",
              "observed": "<the specific thing in the image that decides it>"}}],
  "reasoning": "<two sentences on the overall audit quality>"}}"""


def judge_photo_copy_consistency(*, input=None, output=None, expected_output=None,
                                 metadata=None, **kwargs) -> Evaluation:
    """Was each claim adjudicated correctly GIVEN THE PHOTO?

    value = fraction of the auditor's per-claim verdicts the vision judge agrees
    with. Agreement is computed HERE from the judge's own verdict rather than
    asking it for a boolean: a model asked "do you agree?" agrees, and a model
    asked "what do you see?" answers independently.

    This is the score the metadata-proxy judge (layer B) is supposed to
    approximate, and the hypothesis in MULTIMODAL_EVAL_SPEC.md §3.4 is that it
    stops approximating it on `contradicted_subtle` — precisely where
    vision_extract failed to write the deciding attribute down.
    """
    name = pc.SCORE_PHOTO_COPY_CONSISTENCY
    photo, problem = _photo_reference(input)
    if problem:
        return _not_scored(name, problem)

    entries = [e for e in (_d(output).get("claims") or []) if isinstance(e, dict)]
    if not entries:
        verdict = _d(output).get("verdict")
        if verdict == "needs_better_photo":
            return _not_scored(name, "the auditor declined the photo and returned "
                                     "`needs_better_photo`, so there are no per-claim "
                                     "verdicts to check. Whether declining was RIGHT is "
                                     f"{pc.SCORE_VERDICT_EXACT}'s question.",
                               kind="not applicable")
        return _not_scored(name, "the audit output carries no per-claim verdicts, so "
                                 "there is nothing to check for consistency — "
                                 f"{pc.SCORE_CLAIM_COVERAGE} is the score that grades "
                                 "that absence.", kind="not applicable")

    data_uri, failure = _fetch_uri(name, photo)
    if failure is not None:
        return failure

    verdict_lines = "\n".join(
        f'- "{e.get("claim")}" -> auditor said {e.get("verdict")!r}'
        + (f' (evidence: {str(e.get("evidence"))[:160]})' if e.get("evidence") else "")
        for e in entries)
    copy_text = (_d(input).get("marketing_copy")
                 or pc.marketing_copy([str(e.get("claim")) for e in entries]))

    try:
        parsed, raw = _vision_json(_CONSISTENCY_PROMPT.format(copy=copy_text,
                                                              verdicts=verdict_lines),
                                   data_uri)
    except Exception as e:  # noqa: BLE001
        return _not_scored(name, f"the vision call failed ({type(e).__name__}: {e}). "
                                 "No judgement was made.", kind="judge error")

    judged = {}
    for row in parsed.get("claims") or []:
        if isinstance(row, dict) and row.get("claim"):
            judged[str(row["claim"])] = row

    if not judged:
        # Last resort: honour a bare {"score": ...} before giving up, then say so.
        fallback = _unit(parsed.get("score", parsed.get("rating")))
        if fallback is not None:
            return Evaluation(name=name, value=round(fallback, 2), data_type="NUMERIC",
                              comment="Judge returned only an aggregate score, no "
                                      "per-claim breakdown, so the per-claim detail "
                                      "below is unavailable: "
                                      f"{str(parsed.get('reasoning', ''))[:300]}")
        return _not_scored(name, "the vision model returned no parseable JSON. Raw "
                                 f"reply: {raw[:240]!r}", kind="judge error")

    agreed, disagreed, unjudged = [], [], []
    for entry in entries:
        claim = str(entry.get("claim"))
        row = judged.get(claim)
        if row is None:  # judge paraphrased or skipped it — cannot pair them up
            unjudged.append(claim)
            continue
        photo_verdict = str(row.get("photo_verdict", "")).strip().lower()
        if photo_verdict not in pc.VERDICTS:
            unjudged.append(f"{claim} (judge returned {photo_verdict!r})")
        elif photo_verdict == entry.get("verdict"):
            agreed.append(claim)
        else:
            disagreed.append(f"{claim!r}: auditor {entry.get('verdict')!r} vs photo "
                             f"{photo_verdict!r} ({str(row.get('observed', ''))[:120]})")

    checked = len(agreed) + len(disagreed)
    if not checked:
        return _not_scored(name, "the judge did not return a usable verdict for any "
                                 f"claim (unpaired: {_lst(unjudged)}).",
                           kind="judge error")

    value = len(agreed) / checked
    comment = f"Vision judge agrees with {len(agreed)}/{checked} verdict(s)."
    if disagreed:
        comment += f" Disagreements: {_lst(disagreed, 3)}."
    if unjudged:
        comment += (f" Excluded {len(unjudged)} claim(s) the judge did not pair up: "
                    f"{_lst(unjudged, 2)}.")
    reasoning = str(parsed.get("reasoning", "")).strip()
    if reasoning:
        comment += f" Judge: {reasoning[:280]}"
    return Evaluation(name=name, value=round(value, 2), data_type="NUMERIC",
                      comment=comment)


# =============================================================================
# JUDGE 2 — extraction-fidelity
# =============================================================================
# Rather than asking the judge to grade the agent's attribute dict (which invites
# it to agree with a confidently-worded one), we ask it to read the photo INTO
# THE SAME CLOSED VOCABULARY, then diff the two readings in code. Two payoffs:
#   * the comment can say "judge read window_count=1, agent said 3" — a number a
#     human can go and check against the image;
#   * the two INTERPRETIVE attributes are never put to a vote. `condition` and
#     `natural_light` are a deterministic function of the countable ones
#     (`photo_contract.derive_interpretive`), so we apply that function to the
#     judge's countable reading. A vision model asked whether a kitchen is
#     "renovated" answers with its own taste and disagrees with ground truth for
#     definitional rather than perceptual reasons; the contract says a
#     disagreement here should be a real finding, and this is how it stays one.
#
# The vocabulary block is GENERATED from the contract, not retyped. A prompt that
# drifts from CATEGORICAL_VOCAB would make the judge emit values that
# `validate_attributes` then rejects, and the rejected fields drop silently out of
# the denominator — a scoring bug that looks like a quiet score, not an error.
_VOCAB_BLOCK = "\n".join(
    f"  {key + ':':<15}{' | '.join(pc.CATEGORICAL_VOCAB[key])}"
    for key in pc.COUNTABLE_KEYS if key in pc.CATEGORICAL_VOCAB
) + (
    f"\n  {'window_count:':<15}integer "
    f"{pc.WINDOW_COUNT_RANGE[0]}-{pc.WINDOW_COUNT_RANGE[1]} (windows visible in frame)"
    f"\n  {'appliances:':<15}any of {', '.join(pc.APPLIANCES)} that are VISIBLE"
)

_FIDELITY_PROMPT = """\
Read this property photograph into a fixed vocabulary. Report ONLY what you can \
actually see; if a field is not visible or not applicable to this room, put its \
name in "unreadable" instead of guessing.

""" + _VOCAB_BLOCK + """

Reply with ONLY this JSON (omit nothing except unreadable fields):
{"room_type": "...", "cabinetry": "...", "countertop": "...", "flooring": "...",
 "clutter": "...", "window_count": 0, "appliances": ["..."],
 "unreadable": ["<field names you could not determine>"],
 "reasoning": "<one sentence on image quality and what dominates the frame>"}"""

# Which countable readings each interpretive attribute is derived FROM. If the
# judge could not read a dependency, the derived value would be a default rather
# than an observation, so that attribute is excluded from the comparison instead
# of being scored against a fiction.
_INTERPRETIVE_DEPS = {
    "natural_light": ("window_count",),
    "condition": ("cabinetry", "countertop", "clutter"),
}


def _judge_reading(parsed: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Keep the countable keys the judge read AND the contract accepts."""
    unreadable = {str(k) for k in (parsed.get("unreadable") or [])
                  if isinstance(k, (str, int))}
    reading, rejected = {}, []
    for key in pc.COUNTABLE_KEYS:
        if key not in parsed or key in unreadable:
            continue
        candidate = {key: parsed[key]}
        if pc.validate_attributes(candidate):
            # The judge broke its own vocabulary — do not grade the agent against
            # a value the contract would itself reject.
            rejected.append(f"{key}={parsed[key]!r}")
            continue
        reading[key] = parsed[key]
    return reading, rejected


def _same(key: str, a: Any, b: Any) -> bool:
    if key == "appliances":
        return set(a or []) == set(b or [])
    return a == b


def _is_empty(value: Any) -> bool:
    return value in (None, "", "none", [], ())


def judge_extraction_fidelity(*, input=None, output=None, expected_output=None,
                              metadata=None, **kwargs) -> Evaluation:
    """Do `output["extracted_attributes"]` actually describe the photograph?

    value = fraction of comparable attributes where the agent's extraction
    matches an independent vision reading of the same image. Wrong values and
    plainly-visible attributes the agent never mentioned both count against it;
    attributes the judge could not read do not.

    This is the score that notices what `attributes-schema-valid` cannot: an
    empty attribute dict is perfectly schema-valid and describes nothing.
    """
    name = pc.SCORE_EXTRACTION_FIDELITY
    photo, problem = _photo_reference(input)
    if problem:
        return _not_scored(name, problem)

    agent_attrs = _d(output).get("extracted_attributes")
    if not isinstance(agent_attrs, dict) or not agent_attrs:
        if _d(output).get("verdict") == "needs_better_photo":
            return _not_scored(name, "the auditor declined the photo and extracted "
                                     "nothing, which may well be correct — "
                                     f"{pc.SCORE_VERDICT_EXACT} grades that decision. "
                                     "There is no extraction to check fidelity against.",
                               kind="not applicable")
        return Evaluation(name=name, value=0.0, data_type="NUMERIC",
                          comment="No attributes were extracted at all, yet the auditor "
                                  f"still returned verdict {_d(output).get('verdict')!r}. "
                                  "An empty extraction describes no photograph — note "
                                  f"that {pc.SCORE_ATTRS_SCHEMA_VALID} passes this case "
                                  "vacuously, which is why this judge exists.")

    data_uri, failure = _fetch_uri(name, photo)
    if failure is not None:
        return failure
    try:
        parsed, raw = _vision_json(_FIDELITY_PROMPT, data_uri)
    except Exception as e:  # noqa: BLE001
        return _not_scored(name, f"the vision call failed ({type(e).__name__}: {e}). "
                                 "No judgement was made.", kind="judge error")
    if not parsed:
        return _not_scored(name, "the vision model returned no parseable JSON. Raw "
                                 f"reply: {raw[:240]!r}", kind="judge error")

    reading, rejected = _judge_reading(parsed)
    if not reading:
        return _not_scored(
            name, "the judge could not read a single attribute from this image"
                  + (f" (and returned {len(rejected)} out-of-vocabulary value(s): "
                     f"{_lst(rejected)})" if rejected else "")
                  + f". Judge note: {str(parsed.get('reasoning', ''))[:200]!r}. If this "
                    "is a low-quality photo, that is a finding about the photo, not the "
                    "agent.", kind="not applicable")

    # Extend the judge's reading with the two derived attributes, where derivable.
    checkable = dict(reading)
    for key, deps in _INTERPRETIVE_DEPS.items():
        if all(dep in reading for dep in deps):
            checkable[key] = pc.derive_interpretive(reading)[key]

    agreed, disagreed, missed, unchecked = [], [], [], []
    for key, judge_value in checkable.items():
        if key not in agent_attrs:
            if not _is_empty(judge_value):
                missed.append(f"{key}={judge_value!r}")
            continue
        if _same(key, agent_attrs[key], judge_value):
            agreed.append(key)
        else:
            disagreed.append(f"{key}: agent {agent_attrs[key]!r} vs photo {judge_value!r}")
    for key in agent_attrs:
        if key in pc.ATTRIBUTE_KEYS and key not in checkable:
            unchecked.append(key)

    denominator = len(agreed) + len(disagreed) + len(missed)
    if not denominator:
        return _not_scored(name, "no attribute could be compared: the judge's readable "
                                 "fields and the agent's extracted fields do not "
                                 f"overlap (judge read {_lst(sorted(checkable))}; agent "
                                 f"reported {_lst(sorted(agent_attrs))}).",
                           kind="not applicable")

    value = len(agreed) / denominator
    comment = (f"{len(agreed)}/{denominator} attribute(s) match an independent vision "
               f"reading of the photo.")
    if disagreed:
        comment += f" Wrong: {_lst(disagreed, 4)}."
    if missed:
        comment += f" Visible but not extracted: {_lst(missed, 3)}."
    if unchecked:
        comment += (f" Not comparable (no usable reading from the judge): "
                    f"{_lst(sorted(unchecked), 4)}.")
    if rejected:
        comment += (f" Judge itself broke the vocabulary on {_lst(rejected, 2)}; those "
                    "fields were dropped rather than graded.")
    invented = [k for k in agent_attrs if k not in pc.ATTRIBUTE_KEYS]
    if invented:
        comment += (f" Ignored {len(invented)} out-of-vocabulary key(s) {_lst(invented, 3)}"
                    f" — {pc.SCORE_CLOSED_VOCABULARY} is the score that fails for those.")
    return Evaluation(name=name, value=round(value, 2), data_type="NUMERIC",
                      comment=comment)


VISION_JUDGES = [judge_photo_copy_consistency, judge_extraction_fidelity]

assert {pc.SCORE_PHOTO_COPY_CONSISTENCY, pc.SCORE_EXTRACTION_FIDELITY} == set(
    pc.VISION_SCORE_NAMES), "vision_judges drifted from photo_contract.VISION_SCORE_NAMES"


# =============================================================================
# CODE EVALUATORS, WRAPPED FOR THE EXPERIMENT RUNNER
# =============================================================================
# Layer C's functions are pure and Langfuse-free (`agent/photo_scoring.py`);
# these adapters are the only thing that knows about `Evaluation`, mirroring
# `evaluators/experiment_evaluators.py`.
def _code_evaluator(fn):
    """Wrap one layer-C function as an experiment evaluator.

    No ground-truth overlay is needed (an earlier draft had one): the contract's
    `build_expected_output(..., unreadable=True)` now emits
    `verdict="needs_better_photo"` and `claim_verdicts_apply=False` for the
    `low_quality` class, so `expected_output` alone is complete and the
    evaluators never have to reach into `metadata["scene_class"]`.

    A `NotApplicable` becomes an explicit NOT SCORED rather than vanishing.
    Inside an experiment every item has ground truth, so a check that could not
    run is worth seeing — and its `kind` distinguishes "the dataset told us not
    to grade this" from "the dataset is missing something".
    """
    def evaluator(*, input=None, output=None, expected_output=None, metadata=None,
                  **kwargs):
        score = fn(output if isinstance(output, dict) else {}, expected_output)
        if isinstance(score, photo_scoring.NotApplicable):
            return _not_scored(score.score_name, score.reason, kind=score.kind)
        if score is None:  # defensive: an evaluator that forgot to return
            return _not_scored(fn.score_name, "the evaluator returned nothing.",
                               kind="judge error")
        return Evaluation(name=score.name, value=score.value,
                          data_type=score.data_type, comment=score.comment)
    evaluator.__name__ = fn.__name__
    return evaluator


PHOTO_CODE_ITEM_EVALUATORS = [_code_evaluator(fn)
                              for fn in photo_scoring.PHOTO_CODE_EVALUATORS]
PHOTO_VISION_EVALUATORS = list(VISION_JUDGES)
PHOTO_ALL_EVALUATORS = PHOTO_CODE_ITEM_EVALUATORS + PHOTO_VISION_EVALUATORS


# =============================================================================
# RUN-LEVEL MEANS
# =============================================================================
# One `avg-<score-name>` per averageable score, for the Runs-tab comparison and
# for the CI gate to read.
#
# Every score in this layer is NUMERIC or BOOLEAN, so all eight average. That is
# not a given: the concierge demo's `tone` judge is CATEGORICAL
# (poor/good/excellent), `_mean_evaluator` skips non-numeric values, and the
# consequence is recorded at length in `cicd/thresholds.json` — a persona
# regression cannot fail that gate because the only score covering persona
# cannot be averaged. If a categorical photo-audit score is ever added, it needs
# a numeric companion or it will be invisible to CI in exactly the same way.
#
# Two names are deliberately ABSENT from these means:
#   * proxy-photo-consistency (layer B) and the layer-D anti-pattern are MANAGED
#     evaluators. They run server-side, asynchronously, after ingestion — their
#     scores never appear in `item_results[].evaluations`, so `_mean_evaluator`
#     cannot see them however it is configured. Compare those two against these
#     in the Langfuse UI (or query the scores API); do not expect an
#     `avg-proxy-photo-consistency` in the Runs tab and read its absence as zero.
AVERAGEABLE_SCORE_NAMES = tuple(pc.CODE_SCORE_NAMES) + tuple(pc.VISION_SCORE_NAMES)

PHOTO_RUN_EVALUATORS = [_mean_evaluator(n) for n in AVERAGEABLE_SCORE_NAMES]

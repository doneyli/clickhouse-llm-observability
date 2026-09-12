"""
Layer C of the photo-audit eval stack: DETERMINISTIC code evaluators.

Pure functions over the audit-output dict defined in `agent/photo_contract.py`
§4, plus the dataset item's `expected_output` (§7) where ground truth is
required. They reuse `agent.scoring.Score`, so the photo-audit path emits the
same score objects, on the same two consumers (live traces and experiment runs),
as the concierge path already does.

WHY THESE EXIST ALONGSIDE THE VISION JUDGES
    `cicd/thresholds.json` makes the argument at length and with numbers: on this
    demo's own measurements a code evaluator's value is an exact function of the
    output — when it moves you can open the trace and see why — whereas
    re-running an UNCHANGED prompt moved judge means by up to 0.050, more than
    any prompt-to-prompt judge delta in the table. So the gate is hard on these
    and loose on judges, and every score below is written to be *inspectable*:
    an unexplained 0.0 is not a finding, it is a mystery. Hence the `comment` on
    every Score names the offending key, claim or id.

    `closed-vocabulary` is the sharpest illustration. An extractor that invents
    `"granite_quality": "premium"` produces attribute text that reads perfectly
    well, so a judge — text-only or vision — waves it through. A set-membership
    test cannot be talked round.

TWO CONVENTIONS WORTH KNOWING BEFORE READING ON
    1. `Score | NotApplicable` — an evaluator returns `NotApplicable` when the
       check cannot be RUN on the data it was handed (no ground-truth verdict, no
       claim list, or a scene the dataset says not to grade). It carries the
       reason and WHOSE problem it is, because the alternatives are both wrong:
       inventing 1.0 inflates the metric the CI gate leans on, and 0.0 accuses
       the agent of something it did not do. `run_photo_code_evaluators` drops
       them by default; the experiment adapter renders them as an explicit
       NOT SCORED so a dataset defect can never look like a clean pass.
       (The house convention of passing with a "nothing to check" comment, as
       `scoring.code_budget_adherence` does, is right for a constraint that
       legitimately may not apply — and it is used below for exactly that, e.g.
       an item with no unverifiable claims.)

    2. These are OUR-process evaluators, so they are free to import the contract
       and (in the vision-judge layer) do network I/O. Langfuse's own *managed*
       code evaluators run sandboxed with no network egress, which is why the
       four-layer comparison in MULTIMODAL_EVAL_SPEC.md §3.3 puts the
       deterministic checks here in the SDK rather than in the UI.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Union

from . import photo_contract as pc
from .scoring import LISTING_ID_RE, Score

# The categorical dimensions, straight from the contract's membership-safe table.
# NEVER use ATTRIBUTE_VOCAB for a membership test: its `window_count` and
# `appliances` entries are human-readable PROSE, so `value not in
# ATTRIBUTE_VOCAB[key]` degrades to a silent SUBSTRING match on those two and
# passes nonsense. The contract says so in a comment; this is that warning obeyed.
ENUM_ATTRIBUTE_KEYS = tuple(pc.CATEGORICAL_VOCAB)


@dataclass(frozen=True)
class NotApplicable:
    """A check that could not be RUN — as distinct from a check that FAILED.

    Deliberately not a `Score`: there is no value to report, and inventing one
    (0.0 reading as "the agent failed", 1.0 as "the agent passed") is the exact
    confusion this type exists to prevent. `kind` says who should act:

      "not applicable" — nothing to measure here, and often that is because the
                         agent did the right thing. Never a defect.
      "dataset"        — the item lacks ground truth the check needs. A defect,
                         but in the dataset, not the agent.
    """

    score_name: str
    reason: str
    kind: str = "dataset"

    @property
    def name(self) -> str:
        """Alias for `score_name`, matching `Score.name`.

        Callers hold a `Result` (either type) and routinely want to index by
        which check it refers to. Without this, `{r.name: r for r in results}`
        raises AttributeError on the NotApplicable entries — which it did, the
        first time this module was used from outside.
        """
        return self.score_name


Result = Union[Score, NotApplicable]


# ------------------------------------------------------------ helper access ---
def _d(obj: Any) -> Dict[str, Any]:
    return obj if isinstance(obj, dict) else {}


def _lst(items: Sequence[Any], limit: int = 4) -> str:
    """Render a list of offenders for a comment: informative, bounded."""
    vals = [str(i) for i in items]
    head = ", ".join(vals[:limit])
    return head + (f" (+{len(vals) - limit} more)" if len(vals) > limit else "")


def _claim_entries(result: Any) -> List[Dict[str, Any]]:
    return [e for e in (_d(result).get("claims") or []) if isinstance(e, dict)]


def _malformed_claim_entries(result: Any) -> List[Any]:
    """Entries in `claims` that are not objects — a schema break, not a verdict."""
    return [e for e in (_d(result).get("claims") or []) if not isinstance(e, dict)]


def _adjudicated_claims(result: Any) -> List[str]:
    """Claim texts the audit actually returned a verdict for, in order.

    Order and duplicates are preserved on purpose: `photo_contract.overall_verdict`
    folds over every entry, so the same claim adjudicated twice with conflicting
    verdicts changes the trace-level roll-up. `claim-coverage` flags that.
    """
    return [str(e.get("claim")) for e in _claim_entries(result) if e.get("claim")]


def _verdict_of(result: Any, claim: str) -> Optional[str]:
    for e in _claim_entries(result):
        if str(e.get("claim")) == claim:
            return e.get("verdict")
    return None


def _requested_claims(result: Any,
                      expected_output: Any = None) -> Optional[List[str]]:
    """The claims the input copy actually made — the denominator for coverage.

    Preference order, most authoritative first:
      1. `expected_output["claim_verdicts"]` — the dataset's own ground truth,
         built by `photo_contract.build_expected_output` from the claim list that
         produced the marketing copy. Dict order is insertion order in 3.7+, so
         this preserves the copy's claim order.
      2. `expected_output["claims"]` — a plain list, if a seeder carries one.
      3. `result["requested_claims"]` / `result["input_claims"]` — for the LIVE
         path, where there is no dataset item. The audit-output schema (§4) does
         not carry the input claim list, so a graph that wants coverage scored on
         production traffic has to echo it under one of these keys. If it does
         not, this check returns None rather than a fabricated pass.
    """
    exp = _d(expected_output)
    cv = exp.get("claim_verdicts")
    if isinstance(cv, dict) and cv:
        return [str(c) for c in cv]
    for key in ("claims",):
        val = exp.get(key)
        if isinstance(val, list) and val:
            return [str(c) for c in val]
    for key in ("requested_claims", "input_claims"):
        val = _d(result).get(key)
        if isinstance(val, list) and val:
            return [str(c) for c in val]
    return None


def _unverifiable_ground_truth(result: Any,
                               expected_output: Any = None) -> Optional[List[str]]:
    """Claims whose CORRECT verdict is `unverifiable`.

    From the dataset when available. Otherwise from the contract directly, which
    is EXACT rather than approximate for this particular question:
    `expected_verdict()` returns "unverifiable" if and only if the claim spec's
    `kind` is "unverifiable" or its predicate is None — neither of which depends
    on the scene. So "which claims can no photo settle" is knowable without
    ground truth, and `abstains-when-unverifiable` therefore still scores live
    traffic. (Correctness of the *other* verdicts is not: that needs
    `true_attributes`, which is why `verdict-exact-match` does not have a
    fallback.)
    """
    exp = _d(expected_output)
    cv = exp.get("claim_verdicts")
    if isinstance(cv, dict) and cv:
        return [str(c) for c, v in cv.items() if v == "unverifiable"]

    requested = _requested_claims(result, expected_output) or _adjudicated_claims(result)
    if not requested:
        return None
    known = [c for c in requested if c in pc.CLAIMS_BY_TEXT]
    if not known:
        return None
    return [c for c in known
            if pc.CLAIMS_BY_TEXT[c]["kind"] == "unverifiable"
            or pc.CLAIMS_BY_TEXT[c]["predicate"] is None]


# =============================================================================
# CODE EVALUATORS  (contract §6, CODE_SCORE_NAMES)
# =============================================================================
def code_attributes_schema_valid(result: Dict[str, Any],
                                 expected_output: Optional[Dict[str, Any]] = None) -> Score:
    """BOOLEAN — `validate_attributes()` returns no violations.

    The type-and-range contract: is it an object, is window_count an int in
    0..3, is appliances a duplicate-free list of known appliances, are the
    enumerated values in their tuples.

    Deliberately NOT a completeness check. `validate_attributes({})` returns no
    violations, so an extractor that surfaced nothing scores 1.0 here — see the
    comment this emits for that case, and `extraction-fidelity` (layer A) for the
    check that actually notices. Flagged in the build report: the contract
    argues countable attributes may be asserted HARD, but CODE_SCORE_NAMES has
    no attribute-accuracy score, so accuracy rests on a judge.
    """
    attrs = _d(result).get("extracted_attributes")
    problems = pc.validate_attributes(attrs)
    if problems:
        return Score(pc.SCORE_ATTRS_SCHEMA_VALID, False, "BOOLEAN", kind="code",
                     comment=f"{len(problems)} schema violation(s): {_lst(problems, 6)}.")
    if isinstance(attrs, dict) and not attrs:
        return Score(pc.SCORE_ATTRS_SCHEMA_VALID, True, "BOOLEAN", kind="code",
                     comment="Vacuously valid: extracted_attributes is EMPTY, so there "
                             "was nothing to violate. Nothing was extracted — read "
                             f"{pc.SCORE_EXTRACTION_FIDELITY} for whether that matters.")
    missing = [k for k in pc.COUNTABLE_KEYS if k not in attrs]
    comment = f"All {len(attrs)} extracted attribute(s) satisfy the schema."
    if missing:
        comment += (f" Not extracted: {_lst(missing, 7)} — absence is not a schema "
                    f"violation, so this passes; completeness is judged by "
                    f"{pc.SCORE_EXTRACTION_FIDELITY}.")
    return Score(pc.SCORE_ATTRS_SCHEMA_VALID, True, "BOOLEAN", kind="code",
                 comment=comment)


def code_closed_vocabulary(result: Dict[str, Any],
                           expected_output: Optional[Dict[str, Any]] = None) -> Score:
    """BOOLEAN — no attribute key or enumerated value outside the contract.

    THE check that catches an extractor inventing attributes. A judge reads
    `{"granite_quality": "premium"}` as a perfectly sensible observation about a
    kitchen; set membership does not.

    Scoped to the ENUMERATED dimensions (keys, the seven closed-tuple values,
    appliance names) on purpose. window_count's numeric range is a type/range
    concern and is reported by `attributes-schema-valid`, so the same defect does
    not appear twice under two names — a demo whose two scores always move
    together teaches that they are redundant.
    """
    attrs = _d(result).get("extracted_attributes")
    if not isinstance(attrs, dict):
        return Score(pc.SCORE_CLOSED_VOCABULARY, False, "BOOLEAN", kind="code",
                     comment=f"extracted_attributes is not an object (got "
                             f"{type(attrs).__name__}), so no vocabulary can hold.")

    unknown_keys = [k for k in attrs if k not in pc.ATTRIBUTE_KEYS]
    bad_values = [f"{k}={attrs[k]!r} (allowed: {'|'.join(pc.CATEGORICAL_VOCAB[k])})"
                  for k in ENUM_ATTRIBUTE_KEYS
                  if k in attrs and attrs[k] not in pc.CATEGORICAL_VOCAB[k]]
    appliances = attrs.get("appliances")
    bad_appliances = ([a for a in appliances if a not in pc.APPLIANCES]
                      if isinstance(appliances, list) else [])

    if unknown_keys or bad_values or bad_appliances:
        parts = []
        if unknown_keys:
            parts.append(f"invented attribute key(s) {_lst(unknown_keys)} — the closed "
                         f"key set is {_lst(pc.ATTRIBUTE_KEYS, len(pc.ATTRIBUTE_KEYS))}")
        if bad_values:
            parts.append(f"out-of-vocabulary value(s): {_lst(bad_values)}")
        if bad_appliances:
            parts.append(f"unknown appliance(s) {_lst(bad_appliances)} — allowed: "
                         f"{'|'.join(pc.APPLIANCES)}")
        return Score(pc.SCORE_CLOSED_VOCABULARY, False, "BOOLEAN", kind="code",
                     comment="; ".join(parts) + ".")
    return Score(pc.SCORE_CLOSED_VOCABULARY, True, "BOOLEAN", kind="code",
                 comment=f"All {len(attrs)} key(s) and every enumerated value are inside "
                         f"the closed vocabulary.")


def code_claim_coverage(result: Dict[str, Any],
                        expected_output: Optional[Dict[str, Any]] = None) -> Result:
    """NUMERIC — every claim the copy made was adjudicated, and none invented.

    value = adjudicated / requested, and 0.0 outright if the audit invented a
    claim, adjudicated one twice, or returned a malformed entry. A fabricated
    adjudication is not a fraction of a defect: it means the verdict list no
    longer describes the copy under audit, and `overall_verdict()` folds over
    whatever is in that list.

    Graded even on an UNREADABLE scene, unlike `abstains-when-unverifiable`. This
    asks "did you adjudicate every claim", not "were the verdicts right", and the
    graph's request_better_photo branch deliberately returns every claim as
    `unverifiable` rather than an empty list precisely so this stays meaningful.
    (Which is also why `overall_verdict([])` now returns "unverifiable": an empty
    claim list must not read as a clean pass anywhere.)
    """
    requested = _requested_claims(result, expected_output)
    if requested is None:
        return NotApplicable(
            pc.SCORE_CLAIM_COVERAGE,
            "the input claim list is not reachable from either side: no "
            "expected_output['claim_verdicts'] / ['claims'], and no "
            "result['requested_claims']. Coverage has no denominator.")

    adjudicated = _adjudicated_claims(result)
    malformed = _malformed_claim_entries(result)
    req_set, adj_set = set(requested), set(adjudicated)
    missing = [c for c in requested if c not in adj_set]
    invented = [c for c in dict.fromkeys(adjudicated) if c not in req_set]
    duplicated = [c for c in dict.fromkeys(adjudicated) if adjudicated.count(c) > 1]

    covered = len(requested) - len(missing)
    frac = covered / len(requested) if requested else 1.0

    parts = []
    if missing:
        parts.append(f"{len(missing)}/{len(requested)} claim(s) never adjudicated: "
                     f"{_lst(missing)}")
    if invented:
        parts.append(f"adjudicated {len(invented)} claim(s) the copy never made: "
                     f"{_lst(invented)}")
    if duplicated:
        parts.append(f"claim(s) adjudicated more than once, so the roll-up is "
                     f"ambiguous: {_lst(duplicated)}")
    if malformed:
        parts.append(f"{len(malformed)} entry(ies) in `claims` are not objects: "
                     f"{_lst(malformed)}")

    if invented or duplicated or malformed:
        frac = 0.0
    if not parts:
        return Score(pc.SCORE_CLAIM_COVERAGE, 1.0, "NUMERIC", kind="code",
                     comment=f"All {len(requested)} claim(s) in the copy adjudicated "
                             f"exactly once, none invented.")
    return Score(pc.SCORE_CLAIM_COVERAGE, round(frac, 2), "NUMERIC", kind="code",
                 comment="; ".join(parts) + ".")


def code_listing_cited(result: Dict[str, Any],
                       expected_output: Optional[Dict[str, Any]] = None) -> Result:
    """BOOLEAN — `cited_listing_id` is the listing that was actually audited.

    Cheap, and it catches a failure mode that is invisible in prose: a
    well-written audit attributed to the wrong listing is worse than no audit,
    because it will be filed against a property nobody looked at.
    """
    expected_id = _d(expected_output).get("listing_id") or _d(result).get("listing_id")
    cited = _d(result).get("cited_listing_id")
    if not expected_id:
        return NotApplicable(
            pc.SCORE_LISTING_CITED,
            "neither the dataset nor the audit output names the listing under "
            "audit, so there is nothing to compare `cited_listing_id` against.")
    if not cited:
        return Score(pc.SCORE_LISTING_CITED, False, "BOOLEAN", kind="code",
                     comment=f"No cited_listing_id returned; the audit was for "
                             f"{expected_id!r}, so the output is unattributable.")
    if str(cited) != str(expected_id):
        return Score(pc.SCORE_LISTING_CITED, False, "BOOLEAN", kind="code",
                     comment=f"Cited {cited!r} but audited {expected_id!r} — the verdict "
                             f"is attributed to the wrong listing.")
    comment = f"Cited {cited!r}, the listing under audit."
    if not LISTING_ID_RE.fullmatch(str(cited)):
        comment += (" (Note: does not match the catalog id grammar "
                    "[A-Z]{2,4}-NNN — self-consistent, but suspect.)")
    return Score(pc.SCORE_LISTING_CITED, True, "BOOLEAN", kind="code", comment=comment)


def code_abstains_when_unverifiable(result: Dict[str, Any],
                                    expected_output: Optional[Dict[str, Any]] = None) -> Result:
    """NUMERIC — fraction of unverifiable claims the audit actually abstained on.

    Guessing is the failure. "On a quiet street" cannot be settled by any
    interior photo; an agent that answers "supported" has produced a confident
    number out of nothing, which is precisely the behaviour a photo-audit product
    would be sued over.

    A claim whose truth is unverifiable and which was never adjudicated at all
    also counts against this score — it was not "returned as unverifiable". That
    overlaps with `claim-coverage` by design: the two scores answer different
    questions ("was it covered" vs "was the abstention right") and a shared
    denominator would make neither readable.

    HONOURS `expected_output["claim_verdicts_apply"]`. The contract sets it False
    for an unreadable (`low_quality`) scene, and grading per-claim verdicts there
    would punish the only correct behaviour: the graph's request_better_photo
    branch returns every claim as `unverifiable`, which for a genuinely
    unverifiable claim happens to be right and for the others is an honest
    "I cannot see". Scoring either way says nothing about quality, so this
    returns NotApplicable instead of a number.
    """
    exp = _d(expected_output)
    if exp.get("claim_verdicts_apply") is False:
        return NotApplicable(
            pc.SCORE_ABSTAINS_UNVERIFIABLE,
            "the dataset marks this scene UNREADABLE (claim_verdicts_apply=False). "
            "Its claims do have a truth value in the scene definition, but a photo "
            "too dark or blurred to read gives the agent no fair way to recover it, "
            "so per-claim verdicts are not graded here. The correct behaviour on "
            f"this item is to decline the photo, which {pc.SCORE_VERDICT_EXACT} "
            "grades directly.",
            kind="not applicable")

    unverifiable = _unverifiable_ground_truth(result, expected_output)
    if unverifiable is None:
        return NotApplicable(
            pc.SCORE_ABSTAINS_UNVERIFIABLE,
            "no claim list was reachable and none of the adjudicated claims are in "
            "the contract's CLAIM_SPECS, so which claims a photo cannot settle is "
            "unknown.")
    if not unverifiable:
        return Score(pc.SCORE_ABSTAINS_UNVERIFIABLE, 1.0, "NUMERIC", kind="code",
                     comment="No unverifiable claims in this item — nothing to abstain "
                             "on, so this passes vacuously.")

    guessed, unadjudicated = [], []
    for claim in unverifiable:
        got = _verdict_of(result, claim)
        if got is None:
            unadjudicated.append(claim)
        elif got != "unverifiable":
            guessed.append(f"{claim!r} -> {got!r}")
    ok = len(unverifiable) - len(guessed) - len(unadjudicated)
    frac = ok / len(unverifiable)

    if not guessed and not unadjudicated:
        return Score(pc.SCORE_ABSTAINS_UNVERIFIABLE, 1.0, "NUMERIC", kind="code",
                     comment=f"Abstained correctly on all {len(unverifiable)} claim(s) no "
                             f"photo can settle: {_lst(unverifiable)}.")
    parts = []
    if guessed:
        parts.append(f"guessed instead of abstaining on {len(guessed)}/"
                     f"{len(unverifiable)} unverifiable claim(s): {_lst(guessed)}")
    if unadjudicated:
        parts.append(f"never returned a verdict for {_lst(unadjudicated)}, so no "
                     f"abstention was recorded")
    return Score(pc.SCORE_ABSTAINS_UNVERIFIABLE, round(frac, 2), "NUMERIC", kind="code",
                 comment="; ".join(parts) + ".")


def code_verdict_exact_match(result: Dict[str, Any],
                             expected_output: Optional[Dict[str, Any]] = None) -> Result:
    """BOOLEAN — trace-level verdict equals `expected_output["verdict"]`.

    Needs ground truth, and has no contract-only fallback: the roll-up is exact
    given per-claim verdicts, but whether those verdicts are RIGHT depends on
    `true_attributes`. Returns NotApplicable without a dataset verdict rather
    than scoring self-consistency under a name that promises correctness.

    A straight comparison is now correct for EVERY scene class, including
    `low_quality`: `build_expected_output(..., unreadable=True)` sets the expected
    verdict to `needs_better_photo`, so an agent that correctly declines an
    unreadable photo scores 1.0 here. That is the one score that grades the
    self-correct branch, which is why `abstains-when-unverifiable` can stand down
    on those items without the demo losing coverage of them.
    """
    expected = _d(expected_output).get("verdict")
    if not expected:
        return NotApplicable(
            pc.SCORE_VERDICT_EXACT,
            "expected_output carries no `verdict`, so correctness cannot be "
            "checked. On live traffic that is expected — there is no ground "
            "truth. Inside an experiment it means the item was not built by "
            "photo_contract.build_expected_output().")
    got = _d(result).get("verdict")
    if got == expected:
        return Score(pc.SCORE_VERDICT_EXACT, True, "BOOLEAN", kind="code",
                     comment=f"Verdict {got!r} matches ground truth.")
    comment = f"Verdict {got!r} != expected {expected!r}."
    if got not in pc.OVERALL_VERDICTS:
        comment += (f" {got!r} is not in the contract's verdict vocabulary "
                    f"({'|'.join(pc.OVERALL_VERDICTS)}).")
    elif got == "needs_better_photo":
        comment += (" The agent declined a photo the dataset says was readable — it "
                    "asked for a better one instead of auditing. Read "
                    f"`extraction_confidence` ({_d(result).get('extraction_confidence')!r}) "
                    f"against the contract's {pc.CONFIDENCE_FLOOR} floor: the extractor "
                    "under-rated a usable image, so the self-correct branch fired when "
                    "it should not have.")
    elif expected == "needs_better_photo":
        comment += (" The dataset marks this photo UNREADABLE, so the agent should have "
                    "declined it and asked for a better one. Auditing it anyway means "
                    "the verdict rests on attributes it could not really see — the "
                    "over-confidence this scene class exists to catch.")
    else:
        claim_verdicts = (_d(expected_output).get("claim_verdicts")
                          if _d(expected_output).get("claim_verdicts_apply") is not False
                          else None)
        if isinstance(claim_verdicts, dict) and claim_verdicts:
            wrong = [f"{c!r}: got {_verdict_of(result, c)!r}, expected {v!r}"
                     for c, v in claim_verdicts.items()
                     if _verdict_of(result, c) != v]
            if wrong:
                comment += f" Per-claim disagreement(s): {_lst(wrong, 3)}."
            else:
                comment += (" Every per-claim verdict is correct, so the roll-up itself "
                            "is wrong — check overall_verdict() usage in compose_answer.")
    return Score(pc.SCORE_VERDICT_EXACT, False, "BOOLEAN", kind="code", comment=comment)


def code_attributes_exact_match(result: Dict[str, Any],
                                expected_output: Optional[Dict[str, Any]] = None
                                ) -> Result:
    """Did the vision extractor actually read the photo correctly?

    Fraction of COUNTABLE attributes matching the scene's own definition. Only
    countable keys are compared: `condition` and `natural_light` are DERIVED by
    `derive_interpretive()`, so scoring them would grade the extractor on a rule
    it does not own, and double-count the countable keys they are computed from.

    This is the deterministic counterpart to the `extraction-fidelity` judge, and
    the row worth gating CI on. Rendered fixtures make `true_attributes` exact,
    so extraction accuracy is a measurement, not an opinion — and per
    cicd/thresholds.json, gate hard on the measurement and treat the judge as a
    smoke alarm.
    """
    exp = _d(expected_output)
    truth = exp.get("true_attributes")
    if not isinstance(truth, dict) or not truth:
        return NotApplicable(
            pc.SCORE_ATTRS_EXACT_MATCH,
            "the item carries no `true_attributes`, so there is nothing to compare "
            "the extraction against. Unobtainable on live traffic by nature — no "
            "one labelled the photo.",
            kind="dataset")

    if exp.get("claim_verdicts_apply") is False:
        return NotApplicable(
            pc.SCORE_ATTRS_EXACT_MATCH,
            "the dataset marks this scene UNREADABLE (claim_verdicts_apply=False). "
            "The scene definition knows its attributes, but a photo too dark or "
            "blurred to read gives the agent no fair way to recover them, so "
            "grading extraction here would punish the correct behaviour (declining "
            f"the photo, which {pc.SCORE_VERDICT_EXACT} grades directly).",
            kind="not applicable")

    got = _d(result.get("extracted_attributes"))
    comparable = [k for k in pc.COUNTABLE_KEYS if k in truth]
    if not comparable:
        return NotApplicable(
            pc.SCORE_ATTRS_EXACT_MATCH,
            "`true_attributes` contains no countable keys to compare.",
            kind="dataset")

    def _norm(key, value):
        # appliances is a SET in meaning — order must not decide correctness
        return sorted(value) if key == "appliances" and isinstance(value, list) else value

    hits, misses = 0, []
    for key in comparable:
        want, have = _norm(key, truth[key]), _norm(key, got.get(key))
        if key not in got:
            misses.append(f"{key}: not extracted (expected {want!r})")
        elif want == have:
            hits += 1
        else:
            misses.append(f"{key}: got {have!r}, expected {want!r}")

    frac = hits / len(comparable)
    if not misses:
        return Score(pc.SCORE_ATTRS_EXACT_MATCH, 1.0, "NUMERIC", kind="code",
                     comment=f"All {len(comparable)} countable attributes match the "
                             "scene definition exactly.")
    return Score(pc.SCORE_ATTRS_EXACT_MATCH, round(frac, 2), "NUMERIC", kind="code",
                 comment=f"{hits}/{len(comparable)} countable attributes correct. "
                         f"Wrong: {_lst(misses)}")


# The order the demo reads them in: schema, then vocabulary, then adjudication.
PHOTO_CODE_EVALUATORS = [
    code_attributes_schema_valid,
    code_closed_vocabulary,
    code_attributes_exact_match,
    code_claim_coverage,
    code_listing_cited,
    code_abstains_when_unverifiable,
    code_verdict_exact_match,
]

# Tag each function with the score it emits, and assert the set matches the
# contract. Cheap insurance: if a name is renamed in photo_contract.py and not
# here, the four eval layers stop scoring the same vocabulary and the
# side-by-side comparison silently compares nothing.
_EXPECTED_NAMES = (
    pc.SCORE_ATTRS_SCHEMA_VALID, pc.SCORE_CLOSED_VOCABULARY,
    pc.SCORE_ATTRS_EXACT_MATCH, pc.SCORE_CLAIM_COVERAGE, pc.SCORE_LISTING_CITED,
    pc.SCORE_ABSTAINS_UNVERIFIABLE, pc.SCORE_VERDICT_EXACT)
# zip() truncates silently, which would let a missing evaluator slip through the
# assert below with the remaining names unbound. Check the lengths first.
assert len(PHOTO_CODE_EVALUATORS) == len(_EXPECTED_NAMES), (
    f"{len(PHOTO_CODE_EVALUATORS)} evaluators vs {len(_EXPECTED_NAMES)} names")
for _fn, _name in zip(PHOTO_CODE_EVALUATORS, _EXPECTED_NAMES):
    _fn.score_name = _name
del _fn, _name

assert {f.score_name for f in PHOTO_CODE_EVALUATORS} == set(pc.CODE_SCORE_NAMES), (
    "photo_scoring drifted from photo_contract.CODE_SCORE_NAMES: "
    f"{sorted({f.score_name for f in PHOTO_CODE_EVALUATORS})} vs "
    f"{sorted(pc.CODE_SCORE_NAMES)}")

# Scores that need something the audit output alone does not carry. Exposed so the
# live-traffic path can explain WHY a production trace has four or five scores
# rather than six, instead of a reader assuming the evaluator broke.
#
#   verdict-exact-match  needs a ground-truth verdict. Unobtainable live, full
#                        stop — this one is experiment-only by nature.
#   claim-coverage       needs the INPUT claim list. `photo_contract.AUDIT_KEYS`
#                        does not include it (and photo_audit_graph asserts its
#                        summary is exactly AUDIT_KEYS), so a live caller must
#                        supply it. Cheapest wiring, no graph change:
#                            run_photo_code_evaluators(
#                                summary, {"claims": claims})
#                        `_requested_claims` accepts `expected_output["claims"]`
#                        precisely so the live path can do that.
#   attributes-exact-match  needs the scene's own `true_attributes`. Also
#                        experiment-only by nature: nobody labelled a production
#                        photo, which is exactly why the fixtures are rendered.
PHOTO_CODE_EVALUATORS_NEEDING_GROUND_TRUTH = (pc.SCORE_VERDICT_EXACT,
                                              pc.SCORE_ATTRS_EXACT_MATCH)
PHOTO_CODE_EVALUATORS_NEEDING_INPUT_CLAIMS = (pc.SCORE_CLAIM_COVERAGE,)


def run_photo_code_evaluators(
        result: Dict[str, Any],
        expected_output: Optional[Dict[str, Any]] = None,
        *, include_not_applicable: bool = False) -> List[Result]:
    """Run every deterministic photo-audit check that is measurable on this data.

    Mirrors `scoring.run_code_evaluators`, with one difference: a check that
    cannot be run returns `NotApplicable` and is DROPPED by default, so a live
    trace carries the four or five scores it can honestly support rather than six
    with two of them invented.

    Pass `include_not_applicable=True` to get the NotApplicable markers back —
    what the experiment adapter does, because inside an experiment every item has
    ground truth and a missing check is a dataset defect worth surfacing.
    """
    out: List[Result] = []
    for fn in PHOTO_CODE_EVALUATORS:
        res = fn(result, expected_output)
        if isinstance(res, NotApplicable):
            if include_not_applicable:
                out.append(res)
            continue
        if res is not None:
            out.append(res)
    return out

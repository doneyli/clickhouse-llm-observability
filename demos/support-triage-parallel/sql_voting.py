"""
Stage 2 — Voting (self-consistency) over N SQL candidates.

The *same* data question is answered N times at high temperature (Wang et al.
2022 self-consistency), each sample validated with ``EXPLAIN`` against the
ClickHouse public playground; valid candidates are then executed and
**majority-voted on their result-set signature** — the database, not string
comparison, arbitrates semantic equivalence. A tie fires an Opus judge
(Universal Self-Consistency-style) only when the vote splits.

What lands in Langfuse:
- ``vote-candidate`` — N sibling generations, **identical name**, ``sample_index``
  in metadata (low cardinality so they stay filterable).
- ``validate-candidates`` — span with an ``sql_validity_rate`` score; each
  ``explain-candidate`` is a ``tool`` observation.
- ``tally-votes`` — aggregator span whose **input holds all candidates** and whose
  **metadata is the literal vote tally** (``votes``, ``invalid``, ``winner``,
  ``margin``, ``tie_break_used``). ``consensus_confidence`` is a trace-level score.

Three pluggable aggregation strategies (``VOTE_STRATEGY`` / experiment parameter)
share one skeleton and differ only in how a candidate's signature is computed:
- ``result-signature`` (default) — execute each candidate + hash sorted rows.
- ``majority-exact``   — sqlglot canonical-form string equality (no execution).
- ``judge-consensus``  — skip vote counting; an Opus judge picks among valids.
"""

import asyncio
import hashlib
import os
from collections import Counter
from typing import Dict, List, Optional

import ch_validator
import langfuse_config as lf
from llm import anthropic_call, anthropic_call_sync, strip_sql

VOTE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-opus-4-7")
VOTE_SAMPLES = int(os.getenv("VOTE_SAMPLES", "5"))
VOTE_TEMPERATURE = float(os.getenv("VOTE_TEMPERATURE", "0.9"))
VOTE_STRATEGY = os.getenv("VOTE_STRATEGY", "result-signature")

# Local fallbacks — MIRROR the managed prompts in scripts/seed_prompts.py.
SQL_VOTER_FALLBACK = (
    "You are a ClickHouse SQL expert. Write a SINGLE read-only ClickHouse SELECT "
    "that answers the question using the public demo datasets (nyc_taxi, github, "
    "hackernews, uk, stackoverflow). Always qualify database.table, prefer an "
    "explicit GROUP BY, and add a LIMIT. Reply with ONLY the SQL — no prose, no "
    "code fences.\n\nQuestion: {{question}}\n\nSQL:"
)
TIE_BREAK_FALLBACK = (
    "The following SQL candidates tied in a majority vote. Pick the ONE that is "
    "most correct and consistent for the question, using the result previews as "
    "evidence. Reply with ONLY the integer index of the best candidate.\n\n"
    "Question: {{question}}\n\nCandidates:\n{{candidates}}\n\n"
    "Result previews:\n{{result_previews}}\n\nBest candidate index:"
)


# --------------------------------------------------------------------------- #
# Pure vote math (no I/O) — the unit-test target for the aggregation logic.
# --------------------------------------------------------------------------- #
def compute_tally(candidates: List[dict]) -> dict:
    """Tally votes over candidates annotated with ``valid`` (bool) and
    ``signature`` (str). Pure function — no network, no LLM.

    Returns ``{votes, invalid, winner, top, margin, tie, valid_count, empty}``.
    ``winner`` is None when the vote ties or there are no valid candidates.
    """
    valid = [c for c in candidates if c.get("valid")]
    votes = Counter(c["signature"] for c in valid if c.get("signature"))
    invalid = len(candidates) - len(valid)
    if not votes:
        return {"votes": {}, "invalid": invalid, "winner": None, "top": 0,
                "margin": 0, "tie": False, "valid_count": len(valid), "empty": True}
    counts = sorted(votes.values(), reverse=True)
    top = counts[0]
    second = counts[1] if len(counts) > 1 else 0
    tie = list(votes.values()).count(top) > 1
    winner = None if tie else max(votes, key=lambda k: votes[k])
    return {"votes": dict(votes), "invalid": invalid, "winner": winner, "top": top,
            "margin": top - second, "tie": tie, "valid_count": len(valid), "empty": False}


def _result_signature(sql: str) -> Optional[str]:
    """Execute a candidate read-only and hash its sorted result set."""
    rows = ch_validator.execute_readonly(sql)
    if rows is None:
        return None
    payload = repr(sorted(tuple(str(cell) for cell in row) for row in rows))
    return "sig-" + hashlib.sha1(payload.encode()).hexdigest()[:8]


def _canonical_signature(sql: str) -> str:
    """Signature from the sqlglot canonical SQL string (no execution).

    Deliberately weaker than result-signature — it cannot see that
    ``GROUP BY 1`` is equivalent to ``GROUP BY borough`` — which is the point the
    experiment makes.
    """
    canonical = ch_validator.normalize(sql).lower()
    try:
        import sqlglot
        canonical = sqlglot.parse_one(sql, dialect="clickhouse").sql(
            dialect="clickhouse", normalize=True, pretty=False).lower()
    except Exception:
        canonical = " ".join(canonical.split())
    return "csql-" + hashlib.sha1(canonical.encode()).hexdigest()[:8]


def _assign_signatures(candidates: List[dict], strategy: str) -> None:
    """Populate ``signature`` (used for voting) and ``result_signature`` (the
    executed row-hash, strategy-agnostic for experiments) on each valid candidate."""
    for c in candidates:
        if not c.get("valid"):
            c["signature"] = None
            continue
        if strategy == "majority-exact":
            c["signature"] = _canonical_signature(c["sql"])
            c["result_signature"] = None  # executed lazily for the winner only
        else:  # result-signature and judge-consensus both execute + hash
            sig = _result_signature(c["sql"])
            c["signature"] = sig
            c["result_signature"] = sig


def _result_preview(sql: str, max_rows: int = 3) -> str:
    rows = ch_validator.execute_readonly(sql)
    if not rows:
        return "(no rows / execution failed)"
    return "\n".join(str(r) for r in rows[:max_rows])


async def sample_candidate(question: str, index: int) -> dict:
    """One voting attempt — a generation named ``vote-candidate`` (same on all N;
    the run index lives in metadata)."""
    text, prompt_obj = lf.render_prompt("support-triage-sql-voter",
                                        SQL_VOTER_FALLBACK, question=question)
    gen_kwargs = {"model": VOTE_MODEL}
    if prompt_obj is not None:
        gen_kwargs["prompt"] = prompt_obj
    with lf.observe("vote-candidate", as_type="generation", input=question,
                    metadata={"sample_index": index}, **gen_kwargs) as gen:
        raw = await anthropic_call(model=VOTE_MODEL, prompt=text,
                                   temperature=VOTE_TEMPERATURE, max_tokens=500, obs=gen)
        sql = strip_sql(raw)
        gen.update(output=sql)
    return {"sql": sql, "sample_index": index, "valid": False, "signature": None}


def tie_break_judge(question: str, valid: List[dict]) -> dict:
    """Opus USC-style judge — pick the most consistent/correct candidate given the
    candidates + their result previews. Deterministic fallback (first valid) when
    the judge is unavailable."""
    if not valid:
        return {}
    previews = "\n\n".join(f"[{i}] {c['sql']}\n-> {_result_preview(c['sql'])}"
                           for i, c in enumerate(valid))
    candidate_list = "\n".join(f"[{i}] {c['sql']}" for i, c in enumerate(valid))
    text, prompt_obj = lf.render_prompt(
        "support-triage-tie-break-judge", TIE_BREAK_FALLBACK,
        question=question, candidates=candidate_list, result_previews=previews)
    gen_kwargs = {"model": JUDGE_MODEL}
    if prompt_obj is not None:
        gen_kwargs["prompt"] = prompt_obj
    with lf.observe("tie-break-judge", as_type="generation",
                    input={"question": question, "candidates": candidate_list},
                    **gen_kwargs) as gen:
        try:
            reply = anthropic_call_sync(model=JUDGE_MODEL, prompt=text,
                                        temperature=0.0, max_tokens=50, obs=gen)
            digits = "".join(ch for ch in reply if ch.isdigit())
            idx = int(digits) if digits else 0
            chosen = valid[idx] if 0 <= idx < len(valid) else valid[0]
        except Exception as e:
            gen.update(level="WARNING", status_message=f"judge unavailable: {e}")
            chosen = valid[0]  # deterministic fallback
        gen.update(output={"chosen_index": valid.index(chosen), "sql": chosen["sql"]})
    return chosen


def tally_votes(question: str, candidates: List[dict], strategy: str = VOTE_STRATEGY) -> dict:
    """Aggregate candidates into a winner. Writes the full tally to the
    ``tally-votes`` observation metadata and ``consensus_confidence`` to the trace."""
    _assign_signatures(candidates, strategy)
    valid = [c for c in candidates if c.get("valid")]

    with lf.observe(
        "tally-votes",
        input={"candidates": [{k: c.get(k) for k in ("sql", "valid", "signature",
                                                      "sample_index")} for c in candidates]},
        metadata={"strategy": strategy},
    ) as agg:
        if not valid:
            meta = {"votes": {}, "invalid": len(candidates), "winner": None,
                    "margin": 0, "tie_break_used": False, "strategy": strategy}
            agg.update(metadata=meta, output={"error": "no valid SQL candidates"})
            lf.score_current_trace("consensus_confidence", 0.0,
                                   comment="no valid SQL candidates")
            return {"winning_sql": None, "result_signature": None, "tally": {},
                    "consensus_confidence": 0.0, "tie_break_used": False,
                    "winner": None, "margin": 0, "invalid": len(candidates)}

        tally = compute_tally(candidates)
        tie_break_used = False

        if strategy == "judge-consensus":
            winner_c = tie_break_judge(question, valid)  # judge decides, always
            tie_break_used = True
        elif tally["tie"]:
            winner_c = tie_break_judge(question, valid)  # break the split
            tie_break_used = True
        else:
            winner_sig = tally["winner"]
            winner_c = next(c for c in valid if c.get("signature") == winner_sig)

        winner_sig = winner_c.get("signature")
        winner_votes = tally["votes"].get(winner_sig, 1)
        confidence = winner_votes / max(len(valid), 1)

        # Winner's executed result signature (strategy-agnostic — experiments
        # compare THIS to the pinned expected signature).
        result_sig = winner_c.get("result_signature") or _result_signature(winner_c["sql"])

        meta = {"votes": tally["votes"], "invalid": tally["invalid"],
                "winner": winner_sig, "margin": tally["margin"],
                "tie_break_used": tie_break_used, "strategy": strategy}
        agg.update(metadata=meta,
                   output={"winning_sql": winner_c["sql"], "result_signature": result_sig})
        lf.score_current_trace(
            "consensus_confidence", confidence,
            comment=f"{winner_votes}/{len(valid)} valid samples agreed; strategy={strategy}")

        return {"winning_sql": winner_c["sql"], "result_signature": result_sig,
                "tally": tally["votes"], "consensus_confidence": confidence,
                "tie_break_used": tie_break_used, "winner": winner_sig,
                "margin": tally["margin"], "invalid": tally["invalid"]}


async def vote_sql(question: str, strategy: str = VOTE_STRATEGY,
                   n_samples: int = VOTE_SAMPLES) -> dict:
    """Voting fan-out parent: sample N candidates concurrently, validate via
    EXPLAIN, then tally."""
    with lf.observe("vote-sql",
                    metadata={"n_samples": n_samples, "vote_temperature": VOTE_TEMPERATURE,
                              "strategy": strategy}):
        candidates = await asyncio.gather(
            *(sample_candidate(question, i) for i in range(n_samples)))
        candidates = list(candidates)

        with lf.observe("validate-candidates"):
            for c in candidates:
                c["valid"] = ch_validator.explain_ok(c["sql"])
            rate = sum(1 for c in candidates if c["valid"]) / max(len(candidates), 1)
            lf.score_current_span("sql_validity_rate", rate,
                                  comment=f"{sum(1 for c in candidates if c['valid'])}/"
                                          f"{len(candidates)} candidates planned successfully")

        return tally_votes(question, candidates, strategy)

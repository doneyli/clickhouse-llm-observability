"""Unit tests for the evaluator-optimizer loop (LLM + ClickHouse mocked).

Covers the acceptance criteria that don't need a live stack: termination on
accept / max-iterations / oscillation, the anti-collusion hard rule, the
acceptance threshold, critique feedback fed into the next generation, and
critique-JSON parsing.
"""

import json

import sql_refine_loop as loop
from sql_evidence import Evidence
from conftest import RecordingAsk


# ---------------------------------------------------------------- fixtures
def ok_ev():
    return Evidence(
        checks={"read_only": True, "has_limit": True, "explain_ok": True,
                "exec_ok": True, "nonempty_result": True},
        rows_preview="count()\n42", row_count=1,
    )


def bad_ev():
    # explain failed -> a deterministic evidence check is False
    return Evidence(
        checks={"read_only": True, "has_limit": True, "explain_ok": False},
        error="UNKNOWN_IDENTIFIER 'price_gbp'",
    )


def crit_json(verdict="accept", score=0.95, feedback="looks good", cited="count()\n42"):
    return json.dumps({"verdict": verdict, "score": score,
                       "feedback": feedback, "cited_evidence": cited})


# ---------------------------------------------------------------- termination
def test_accept_on_first_iteration(monkeypatch):
    monkeypatch.setattr(loop, "gather_evidence", lambda sql, max_rows=20: ok_ev())
    ask = RecordingAsk(gen_sqls=["SELECT count() FROM uk.uk_price_paid LIMIT 1"],
                       critic_jsons=[crit_json(verdict="accept", score=0.96)])
    monkeypatch.setattr(loop, "_ask", ask)

    res = loop.run_refine_loop("How many sales?", "uk dataset")
    assert res.converged is True
    assert res.iterations == 1
    assert res.stop_reason == "accepted"
    assert res.critic_scores == [0.96]
    assert res.sql.startswith("SELECT count()")


def test_max_iterations_when_never_accepted(monkeypatch):
    monkeypatch.setattr(loop, "gather_evidence", lambda sql, max_rows=20: bad_ev())
    ask = RecordingAsk(
        gen_sqls=["SELECT a FROM t LIMIT 1", "SELECT b FROM t LIMIT 1", "SELECT c FROM t LIMIT 1"],
        critic_jsons=[crit_json(verdict="revise", score=0.3, feedback="fix it")] * 3,
    )
    monkeypatch.setattr(loop, "_ask", ask)

    res = loop.run_refine_loop("q", "analysis", max_iterations=3)
    assert res.converged is False
    assert res.iterations == 3
    assert res.stop_reason == "max_iterations"
    assert len(res.critic_scores) == 3


def test_oscillation_guard(monkeypatch):
    monkeypatch.setattr(loop, "gather_evidence", lambda sql, max_rows=20: bad_ev())
    # generator repeats the SAME candidate -> oscillation guard trips on iteration 2
    ask = RecordingAsk(gen_sqls=["SELECT x FROM t LIMIT 1", "SELECT x FROM t LIMIT 1"],
                       critic_jsons=[crit_json(verdict="revise", score=0.2)] * 2)
    monkeypatch.setattr(loop, "_ask", ask)

    res = loop.run_refine_loop("q", "analysis", max_iterations=3)
    assert res.stop_reason == "oscillation"
    assert res.converged is False
    assert res.iterations == 1  # second (duplicate) candidate not counted


# ------------------------------------------------------- anti-collusion / threshold
def test_evidence_overrides_sycophantic_critic(monkeypatch):
    # Critic tries to accept with a perfect score, but a deterministic check failed.
    monkeypatch.setattr(loop, "_ask", lambda p, temperature=0.0: crit_json(verdict="accept", score=1.0))
    crit = loop._critique("q", "SELECT price_gbp FROM uk.uk_price_paid LIMIT 1",
                          bad_ev(), iteration=1, history=[])
    assert crit.verdict == "revise"          # evidence overrides opinion
    assert crit.score == 1.0                 # raw critic score preserved for the delta


def test_accept_only_when_all_checks_pass_and_threshold_met(monkeypatch):
    monkeypatch.setattr(loop, "_ask", lambda p, temperature=0.0: crit_json(verdict="accept", score=0.95))
    crit = loop._critique("q", "SELECT count() FROM uk.uk_price_paid LIMIT 1",
                          ok_ev(), iteration=1, history=[])
    assert crit.verdict == "accept"


def test_below_threshold_forced_revise(monkeypatch):
    monkeypatch.setattr(loop, "_ask", lambda p, temperature=0.0: crit_json(verdict="accept", score=0.5))
    crit = loop._critique("q", "SELECT count() FROM uk.uk_price_paid LIMIT 1",
                          ok_ev(), iteration=1, history=[])
    assert crit.verdict == "revise"          # score below ACCEPT_THRESHOLD


def test_opinion_only_critic_allowed_to_collude(monkeypatch):
    # The collusion demo: opinion-only critic is NOT forced to revise on failing
    # evidence checks (that's the whole point of Experiment B).
    monkeypatch.setattr(loop, "_ask", lambda p, temperature=0.0: crit_json(verdict="accept", score=0.95))
    crit = loop._critique("q", "SELECT price_gbp FROM uk.uk_price_paid LIMIT 1",
                          bad_ev(), iteration=1, history=[], critic_label="opinion-only")
    assert crit.verdict == "accept"


# ------------------------------------------------------------ feedback fed back
def test_critique_feedback_fed_into_next_generation(monkeypatch):
    monkeypatch.setattr(loop, "gather_evidence", lambda sql, max_rows=20: bad_ev())
    feedback = "use column price not price_gbp"
    ask = RecordingAsk(
        gen_sqls=["SELECT price_gbp FROM uk.uk_price_paid LIMIT 1",
                  "SELECT price FROM uk.uk_price_paid LIMIT 1",
                  "SELECT price FROM uk.uk_price_paid GROUP BY town LIMIT 1"],
        critic_jsons=[crit_json(verdict="revise", score=0.3, feedback=feedback,
                                cited="UNKNOWN_IDENTIFIER 'price_gbp'")] * 3,
    )
    monkeypatch.setattr(loop, "_ask", ask)

    loop.run_refine_loop("q", "analysis", max_iterations=3)

    gen_prompts = [p for p in ask.prompts if "you write a single read-only" in p.lower()]
    assert len(gen_prompts) >= 2
    assert "None yet" in gen_prompts[0]          # iteration 1: no history
    assert feedback in gen_prompts[1]            # iteration 2: prior critique fed back
    assert "UNKNOWN_IDENTIFIER 'price_gbp'" in gen_prompts[1]  # cited evidence too


# ------------------------------------------------------------ JSON parsing
def test_parse_valid_json():
    ev = ok_ev()
    crit = loop._parse_critique_json(crit_json(verdict="revise", score=0.4, feedback="add LIMIT"), ev)
    assert crit is not None
    assert crit.verdict == "revise"
    assert crit.score == 0.4
    assert crit.feedback == "add LIMIT"
    assert crit.checks == ev.checks


def test_parse_json_embedded_in_prose():
    ev = ok_ev()
    raw = "Here is my critique:\n```json\n" + crit_json(score=0.8) + "\n```\nThanks."
    crit = loop._parse_critique_json(raw, ev)
    assert crit is not None
    assert crit.score == 0.8


def test_parse_malformed_returns_none():
    assert loop._parse_critique_json("not json at all", ok_ev()) is None


def test_malformed_json_survives_via_retry_then_safe_revise(monkeypatch):
    # Both attempts malformed -> safe "revise" fallback (hard failure, not a crash).
    monkeypatch.setattr(loop, "_ask", lambda p, temperature=0.0: "garbage not json")
    crit = loop._critique("q", "SELECT 1 LIMIT 1", ok_ev(), iteration=1, history=[])
    assert crit.verdict == "revise"
    assert crit.score == 0.0


# ------------------------------------------------------------ result shaping
def test_as_context_warns_when_not_converged():
    res = loop.RefineResult(sql="SELECT 1 LIMIT 1", rows_preview="x\n1", converged=False,
                            iterations=3, stop_reason="max_iterations", critic_scores=[0.2, 0.3, 0.3])
    ctx = res.as_context()
    assert "WARNING" in ctx
    assert "SELECT 1 LIMIT 1" in ctx


def test_as_context_clean_when_converged():
    res = loop.RefineResult(sql="SELECT count() LIMIT 1", rows_preview="c\n42", converged=True,
                            iterations=1, stop_reason="accepted", critic_scores=[0.95])
    ctx = res.as_context()
    assert "WARNING" not in ctx
    assert "Validated SQL" in ctx

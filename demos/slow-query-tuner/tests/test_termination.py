"""finish-claim verification accepts true claims and rejects false ones.

The controller RE-EXECUTES the agent's final SQL against a (mock) environment —
so a claimed speedup that doesn't verify, or a rewrite that changed the result
set, is bounced back. LLM-free; the env is a mock.
"""

import types

import agent_loop
import ch_env
import tools
from checkpoint import LoopState

BASELINE_SIG = "baseline00000000"


class FakeEnv:
    """Mock TuningLabEnv: run_query returns a canned measured Obs."""

    def __init__(self, elapsed_ms, signature, ok=True):
        self.elapsed_ms = elapsed_ms
        self.signature = signature
        self.ok = ok
        self.calls = 0
        self.ddl_applied = []

    def run_query(self, sql, settings=None):
        self.calls += 1
        if not self.ok:
            return ch_env.Obs.error_obs("boom", kind="query")
        return ch_env.Obs(ok=True, kind="query", elapsed_ms=self.elapsed_ms,
                          read_rows=10, read_bytes=100, rows_preview=[[1]],
                          signature=self.signature)

    def apply_ddl(self, ddl):
        self.ddl_applied.append(ddl)
        return ch_env.Obs(ok=True, kind="ddl", text="applied")


def make_state():
    st = LoopState.fresh(run_id="r", session_id="s",
                         goal={"sql": "SELECT 1", "target_ms": 800}, goal_prompt="g")
    st.baseline_ms = 4000.0
    st.baseline_signature = BASELINE_SIG
    return st


def finish_action(status="success", final_sql="SELECT 1", speedup=10.0):
    return tools.Action(name="finish",
                        args={"status": status, "final_sql": final_sql,
                              "claimed_speedup": speedup, "summary": "done"},
                        tool_use_id="tid")


def test_true_success_claim_accepted():
    env = FakeEnv(elapsed_ms=300.0, signature=BASELINE_SIG)   # equivalent + fast
    v = agent_loop.verify_finish_claim(finish_action(), env, make_state(), target_ms=800)
    assert v.ok
    assert v.measured_speedup > 5      # 4000 / 300 ≈ 13x
    assert env.calls == 3              # median-of-3 measurement


def test_false_claim_rejected_when_too_slow():
    env = FakeEnv(elapsed_ms=1500.0, signature=BASELINE_SIG)  # equivalent but slow
    v = agent_loop.verify_finish_claim(finish_action(speedup=99.0), env, make_state(), target_ms=800)
    assert not v.ok
    assert "not met" in v.reason


def test_false_claim_rejected_when_result_set_differs():
    env = FakeEnv(elapsed_ms=100.0, signature="different00000")  # fast but WRONG rows
    v = agent_loop.verify_finish_claim(finish_action(), env, make_state(), target_ms=800)
    assert not v.ok
    assert "differs" in v.reason


def test_gave_up_is_accepted():
    env = FakeEnv(elapsed_ms=2000.0, signature=BASELINE_SIG)
    v = agent_loop.verify_finish_claim(finish_action(status="gave_up"), env, make_state(), target_ms=50)
    assert v.ok                        # conceding is an honest, valid termination


def test_success_claim_with_bad_sql_rejected():
    env = FakeEnv(elapsed_ms=100.0, signature=BASELINE_SIG)
    v = agent_loop.verify_finish_claim(
        finish_action(final_sql="DROP TABLE web_events"), env, make_state(), target_ms=800)
    assert not v.ok


def test_dispatch_marks_candidate_equivalence():
    # A candidate whose signature matches the baseline is flagged equivalent;
    # a mismatching one is flagged non-equivalent — the per-turn semantics check.
    env = FakeEnv(elapsed_ms=300.0, signature=BASELINE_SIG)
    action = tools.Action(name="run_query", args={"sql": "SELECT 1"}, tool_use_id="t")
    obs = tools.dispatch(action, env, baseline_sql="SELECT 1", baseline_signature=BASELINE_SIG)
    assert obs.candidate_checked and obs.equivalent is True

    env2 = FakeEnv(elapsed_ms=300.0, signature="other00000000")
    obs2 = tools.dispatch(action, env2, baseline_sql="SELECT 1", baseline_signature=BASELINE_SIG)
    assert obs2.candidate_checked and obs2.equivalent is False


def test_hitl_auto_modes():
    a = tools.Action(name="propose_ddl", args={"ddl": "ALTER ...", "rationale": "x"}, tool_use_id="t")
    assert agent_loop.hitl_approve(a, "auto-approve") is True
    assert agent_loop.hitl_approve(a, "auto-deny") is False

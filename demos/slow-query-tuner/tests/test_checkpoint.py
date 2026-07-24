"""save -> load round-trip preserves history, cost, session_id; compaction and
progress bookkeeping behave. LLM/DB-free."""

import pytest

import ch_env
import checkpoint
from checkpoint import LoopState


@pytest.fixture(autouse=True)
def _tmp_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("TUNER_CHECKPOINT_DIR", str(tmp_path))
    return tmp_path


def _seeded_state():
    st = LoopState.fresh(run_id="run-abc123", session_id="qtune-deadbeef",
                         goal={"sql": "SELECT 1", "target_ms": 800,
                               "expected_turn_band": [2, 4]},
                         goal_prompt="Optimize this query...")
    st.baseline_ms = 4100.0
    st.baseline_signature = "sigbaseline00000"
    st.cost_usd = 0.1234
    st.best_speedup = 3.5
    st.best_sql = "SELECT 1 -- fast"
    st.plateau = 1
    # Two completed turn-pairs.
    st.append_pair({"role": "assistant", "content": [{"type": "tool_use", "id": "a1",
                    "name": "explain_query", "input": {"sql": "SELECT 1"}}]},
                   "a1", {"kind": "explain", "text": "plan"}, "turn 1: explain")
    st.append_pair({"role": "assistant", "content": [{"type": "tool_use", "id": "a2",
                    "name": "run_query", "input": {"sql": "SELECT 1"}}]},
                   "a2", {"kind": "query", "elapsed_ms": 1200}, "turn 2: run_query -> 1200 ms")
    st.turn = 3
    return st


def test_round_trip_preserves_everything():
    st = _seeded_state()
    checkpoint.save(st.run_id, st)
    assert checkpoint.exists(st.run_id)

    loaded = checkpoint.load(st.run_id)
    assert loaded.run_id == st.run_id
    assert loaded.session_id == "qtune-deadbeef"      # same session -> stitched trace
    assert loaded.goal == st.goal
    assert loaded.turn == 3
    assert loaded.cost_usd == pytest.approx(0.1234)
    assert loaded.best_speedup == pytest.approx(3.5)
    assert loaded.best_sql == "SELECT 1 -- fast"
    assert loaded.plateau == 1
    assert loaded.baseline_ms == pytest.approx(4100.0)
    assert loaded.baseline_signature == "sigbaseline00000"
    assert loaded.messages == st.messages             # full history preserved
    assert loaded.summaries == st.summaries


def test_messages_are_json_serialisable():
    # No SDK objects — plain dicts only, so json.dump never chokes on resume.
    st = _seeded_state()
    checkpoint.save(st.run_id, st)  # would raise if messages held non-serialisable objects


def test_atomic_save_leaves_no_tmp(tmp_path):
    st = _seeded_state()
    checkpoint.save(st.run_id, st)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_compaction_folds_old_turns():
    st = _seeded_state()
    # Only 2 pairs -> no compaction when compact_after=6.
    msgs = st.compacted_messages(compact_after=6)
    assert len(msgs) == 1 + len(st.messages)          # goal + all raw
    assert "compacted" not in str(msgs[0]["content"])

    # compact_after=1 -> the oldest pair folds into the goal message summary.
    compacted = st.compacted_messages(compact_after=1)
    assert "compacted" in str(compacted[0]["content"])
    assert "turn 1: explain" in str(compacted[0]["content"])
    # Recent pair kept verbatim (2 messages) + the goal head.
    assert len(compacted) == 1 + 2


def test_record_progress_updates_best_and_plateau():
    st = _seeded_state()
    st.best_speedup = 1.0
    st.plateau = 0

    faster = ch_env.Obs(ok=True, kind="query", elapsed_ms=1000.0,
                        candidate_checked=True, equivalent=True)
    delta = st.record_progress(faster, candidate_sql="SELECT fast")
    assert delta > 0
    assert st.best_speedup == pytest.approx(4.1)      # 4100 / 1000
    assert st.best_sql == "SELECT fast"
    assert st.plateau == 0

    # A non-improving candidate increments the plateau counter, no delta.
    slower = ch_env.Obs(ok=True, kind="query", elapsed_ms=3000.0,
                        candidate_checked=True, equivalent=True)
    assert st.record_progress(slower, candidate_sql="SELECT meh") == 0.0
    assert st.plateau == 1

    # A non-equivalent candidate also counts as a plateau turn.
    wrong = ch_env.Obs(ok=True, kind="query", elapsed_ms=10.0,
                       candidate_checked=True, equivalent=False)
    assert st.record_progress(wrong, candidate_sql="SELECT wrong") == 0.0
    assert st.plateau == 2

    # An info-gathering turn (explain) does not touch plateau.
    explain = ch_env.Obs(ok=True, kind="explain", text="plan")
    assert st.record_progress(explain) == 0.0
    assert st.plateau == 2

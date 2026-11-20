"""
Pins the measured linkage matrix (SPEC §6): three real processes, two A2A hops, stub agents,
spans captured from each process's Langfuse SDK. Needs no server and no LLM key (~25 s).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import linkage_matrix  # noqa: E402

EXPECTED = {
    #  mode            traces roots remote-with-session
    "none":          (3, 3, "0/5"),
    "traceparent":   (1, 3, "0/5"),   # one trace id, but every remote process marks an app root
    "baggage-claim": (1, 1, "0/5"),   # the SDK's trace claim suppresses the extra roots only
    "baggage":       (1, 1, "5/5"),   # the default: one root, session + user everywhere
    "session":       (3, 3, "5/5"),   # by design: separate traces in one session
}


@pytest.mark.parametrize("mode", list(EXPECTED))
def test_linkage_mode(mode):
    r = linkage_matrix.measure(mode)
    traces, roots, session = EXPECTED[mode]
    assert (r["traces"], r["logical_roots"], r["remote_session"]) == (traces, roots, session), r
    assert r["dangling_parents"] == 0, r
    if mode == "baggage":
        assert r["remote_user"] == "5/5" and r["remote_trace_name"] == "support-turn", r
    if mode == "session":
        assert r["sessions"] == "matrix-session", r

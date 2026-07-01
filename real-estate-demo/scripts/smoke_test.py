"""
Walking-skeleton smoke test.

Validates the three riskiest Langfuse-integration unknowns BEFORE we build the
full demo:
  1. Key isolation  — trace lands in the 'real-estate' project (not a shadow).
  2. Observation-level scores — a score attached to a *child* observation
     actually renders against that observation (not just the trace).
  3. Span nesting — manual start_as_current_observation spans nest correctly.

Run:
    ./.venv/bin/python scripts/smoke_test.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_langfuse, verify_project, langfuse_api, LANGFUSE_HOST


def main():
    verify_project()
    lf = get_langfuse()

    trace_id = None
    gen_id = None

    # 1 root span -> 2 nested generation (with fake token usage)
    with lf.start_as_current_observation(as_type="span", name="smoke-root") as root:
        trace_id = lf.get_current_trace_id()
        root.update(input={"q": "smoke test"})
        with lf.start_as_current_observation(
            as_type="generation", name="smoke-generation", model="claude-sonnet-4-6"
        ) as gen:
            gen_id = gen.id
            gen.update(
                input=[{"role": "user", "content": "hello"}],
                output="hi there",
                usage_details={"input_tokens": 10, "output_tokens": 5},
            )
            # OBSERVATION-level score (attached to this generation)
            gen.score(name="smoke-observation-score", value=0.87, data_type="NUMERIC",
                      comment="observation-level smoke score")
        # TRACE-level score
        root.score_trace(name="smoke-trace-score", value=1.0, data_type="NUMERIC",
                         comment="trace-level smoke score")

    lf.flush()
    print(f"  trace_id={trace_id}  generation_id={gen_id}")
    print("  flushed; waiting for ingestion...")

    # Poll the API to confirm the trace + observation-level score landed.
    ok = False
    for attempt in range(15):
        time.sleep(2)
        try:
            status, trace = langfuse_api("GET", f"/api/public/traces/{trace_id}")
            if status != 200:
                continue
        except Exception:
            continue
        scores = trace.get("scores", [])
        obs_scores = [s for s in scores if s.get("observationId")]
        trace_scores = [s for s in scores if not s.get("observationId")]
        n_obs = len(trace.get("observations", []))
        if scores and n_obs >= 2:
            print(f"\n✓ Trace found: {n_obs} observations, {len(scores)} scores")
            print(f"  observation-level scores: {[(s['name'], s['value'], s.get('observationId')==gen_id) for s in obs_scores]}")
            print(f"  trace-level scores:       {[(s['name'], s['value']) for s in trace_scores]}")
            ok = obs_scores and any(s.get("observationId") == gen_id for s in obs_scores)
            break
        print(f"  attempt {attempt+1}: obs={n_obs} scores={len(scores)} (waiting)")

    print()
    if ok:
        print("SMOKE TEST PASSED — observation-level score confirmed on the child generation.")
        print(f"View: {LANGFUSE_HOST}  (Tracing > trace {trace_id})")
    else:
        print("SMOKE TEST INCOMPLETE — check ingestion / API above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

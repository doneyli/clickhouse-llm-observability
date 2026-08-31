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

from agent.config import (get_langfuse, verify_project, LANGFUSE_HOST,
                          list_observations, list_scores, score_observation_id)


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
    # Reads go through the v2 observations / v3 scores endpoints — the v1
    # `GET /traces/{id}` shape (with nested `.observations` / `.scores`) is
    # deprecated, and v3 moved a score's target into its `subject` object.
    # Wait for the thing this test actually asserts — a score attached to the
    # child generation. Trace-level and observation-level scores are ingested
    # independently, so breaking as soon as *any* score appears reports a
    # spurious failure whenever the trace-level one lands first.
    ok = False
    for attempt in range(15):
        time.sleep(2)
        try:
            observations = list_observations(trace_id)
            scores = list_scores(trace_id)
        except Exception:
            continue
        obs_scores = [s for s in scores if score_observation_id(s)]
        trace_scores = [s for s in scores if not score_observation_id(s)]
        n_obs = len(observations)
        if n_obs >= 2 and any(score_observation_id(s) == gen_id for s in obs_scores):
            print(f"\n✓ Trace found: {n_obs} observations, {len(scores)} scores")
            print(f"  observation-level scores: {[(s['name'], s['value'], score_observation_id(s)==gen_id) for s in obs_scores]}")
            print(f"  trace-level scores:       {[(s['name'], s['value']) for s in trace_scores]}")
            ok = True
            break
        print(f"  attempt {attempt+1}: obs={n_obs} scores={len(scores)} "
              f"(obs-level={len(obs_scores)}) (waiting)")

    print()
    if ok:
        print("SMOKE TEST PASSED — observation-level score confirmed on the child generation.")
        print(f"View: {LANGFUSE_HOST}  (Tracing > trace {trace_id})")
    else:
        print("SMOKE TEST INCOMPLETE — check ingestion / API above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

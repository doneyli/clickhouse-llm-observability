#!/usr/bin/env python3
"""The SDK half of the gateway-vs-SDK comparison.

Same provider, same model, same Langfuse project as `client.py` — but the
instrumentation lives HERE, in the application process, instead of at the proxy:

    client.py   app --(HTTP)--> LiteLLM proxy --> Anthropic
                                      `--> Langfuse      (gateway instruments)

    sdk_client.py   app (litellm SDK) --> Anthropic
                     `--> Langfuse                        (app instruments)

Every trace is tagged `path:sdk` here and `path:proxy` there, so both show up in
one Langfuse trace list and can be filtered apart.

Run it inside the LiteLLM image so no local pip install is needed:
    ./demos/litellm-gateway/run_sdk_demo.sh
"""

import os
import sys
import time
import uuid

import litellm

# The whole integration: LiteLLM's OTEL callback reads LANGFUSE_* from the
# environment and exports to $LANGFUSE_OTEL_HOST/api/public/otel. Compare with
# config.yaml's `callbacks: ["langfuse_otel"]` — same callback, but there it is
# declared once at the gateway for every app behind it, and here it must be
# declared (and kept correct) in each application separately.
litellm.callbacks = ["langfuse_otel"]

DEFAULT_PROMPT = (
    "In two sentences, contrast instrumenting LLM calls at a gateway versus in "
    "each application."
)


# Seconds to let LiteLLM's asynchronous success callback run before flushing.
# Measured, not guessed: flushing immediately after completion() returns loses
# the trace every time (verified against Langfuse Cloud), while settling first
# lands it. See flush_langfuse_spans below.
SETTLE_SECONDS = float(os.environ.get("SDK_DEMO_SETTLE_SECONDS", "6"))


def flush_langfuse_spans() -> bool:
    """Settle, then force-flush LiteLLM's OTEL span processor.

    Returns True if a flush ran. This is load-bearing, not defensive — getting
    it wrong makes the demo print a perfect response while Langfuse stays empty.

    Two separate hazards, both of which have to be handled:

    1. LiteLLM records the span from an *asynchronous* success callback (the live
       logger shows up in `litellm._async_success_callback`, not in
       `litellm.callbacks`, which still holds the plain string "langfuse_otel").
       A sync script that exits as soon as completion() returns never gives that
       callback a chance to run, so there is no span to flush yet. Hence the
       settle wait — flushing first and exiting drops the trace 100% of the time.
    2. The span then sits in a BatchSpanProcessor on a ~5s timer, so it still
       needs an explicit force_flush before the process exits.

    There is no public flush API, and the instance's `OTEL_EXPORTER` attribute is
    just a string while the OTEL *global* provider is never set — flushing either
    is a no-op. The real handle is `_tracer_provider` on the live logger.
    """
    time.sleep(SETTLE_SECONDS)

    flushed = False
    seen = set()
    candidates = []
    for attr in ("_async_success_callback", "callbacks", "success_callback",
                 "_async_failure_callback", "failure_callback"):
        candidates.extend(getattr(litellm, attr, None) or [])

    for candidate in candidates:
        if isinstance(candidate, str) or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        provider = getattr(candidate, "_tracer_provider", None)
        force_flush = getattr(provider, "force_flush", None)
        if callable(force_flush):
            try:
                force_flush()
                flushed = True
            except Exception as exc:
                print(f"(flush failed on {type(candidate).__name__}: {exc})",
                      file=sys.stderr)
    return flushed


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROMPT
    model = os.environ.get("LITELLM_UPSTREAM_MODEL", "anthropic/claude-sonnet-4-6")
    session_id = f"litellm-sdk-{uuid.uuid4().hex[:8]}"

    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        if not os.environ.get(key):
            print(f"Error: {key} is not set.", file=sys.stderr)
            return 1

    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": "Be concise and answer in two sentences."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=160,
        # Identical metadata contract to the proxy path: LiteLLM's langfuse_otel
        # callback maps trace_user_id (NOT user_id) onto the Langfuse trace user
        # and exports every other key as a langfuse.* span attribute.
        metadata={
            "generation_name": "litellm-sdk-completion",
            "trace_name": "litellm-sdk-demo",
            "trace_user_id": "sdk-demo-user",
            "session_id": session_id,
            "tags": ["litellm", "sdk", "path:sdk", "demo"],
        },
    )

    print(f"Model response: {response.choices[0].message.content}")
    print(f"Model:          {model}")
    print(f"Session ID:     {session_id}")
    print(f"Tokens:         {response.usage.total_tokens}")

    if not flush_langfuse_spans():
        print(
            "Error: could not flush the OTEL span processor, so the trace was "
            "probably dropped. Do not trust this run.",
            file=sys.stderr,
        )
        return 1

    print(f"Langfuse UI:    {os.environ.get('LANGFUSE_OTEL_HOST', '')} "
          f"(filter by session {session_id} or tag path:sdk)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

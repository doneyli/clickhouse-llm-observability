"""
Verify the multi-modal eval mechanisms this demo depends on.

Langfuse's *built-in* evaluators are text-only; its *SDK experiment runner* is
multi-modal. Every multi-modal claim in MULTIMODAL_EVAL_SPEC.md rests on the
handful of behaviours asserted here, so this script exists to catch it the day
one of them changes rather than mid-demo.

Checks:
  1. SDK >= 4.10        — below that, media dataset items do not hydrate at all.
  2. Media hydration    — LangfuseMedia in a dataset item comes back as a
                          LangfuseMediaReference whose bytes round-trip.
  3. Raw token          — the STORED field is a bare @@@langfuseMedia token.
                          This is the precondition for the anti-pattern exhibit:
                          it is what a managed (text-only) judge interpolates.
  4. Trace -> dataset   — a token promoted out of a production observation still
                          hydrates, so annotation -> dataset needs no re-upload.
  5. Vision judge       — an SDK evaluator can fetch pixels and land a score.
                          Costs 2 vision calls; skip with --skip-vision.
  6. LangGraph attrs    — propagate_attributes reaches node observations, so
                          observation-level evaluators can filter on them.
                          Skipped automatically if langgraph is not installed.

Run:
    ./.venv/bin/python scripts/verify_multimodal.py
    ./.venv/bin/python scripts/verify_multimodal.py --skip-vision
    ./.venv/bin/python scripts/verify_multimodal.py --cleanup

Exits non-zero if any check fails, so it is usable as a CI gate.
"""

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import (  # noqa: E402
    get_langfuse,
    langfuse_api,
    list_observations,
    list_scores,
    observation_io,
    verify_project,
    LANGFUSE_HOST,
    AGENT_MODEL,
)

DATASET = "spike/multimodal-probe"
TAG = "verify:multimodal"
MIN_SDK = (4, 10)

_results: list[tuple[bool, str, str]] = []


def check(passed: bool, label: str, detail: str = "") -> bool:
    _results.append((passed, label, detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return passed


def info(msg: str) -> None:
    print(f"        {msg}")


# --------------------------------------------------------------------------
# fixture: a deterministic scene of exactly 2 red circles + 1 blue square.
# The claim under test asserts "three red circles and no square", so a judge
# that truly received pixels must contradict it and be able to say why. No
# licensing question, and no dependency on any committed photo.
# --------------------------------------------------------------------------
def make_fixture() -> bytes:
    """Encode the scene as a PNG using only the stdlib (zlib + struct).

    Deliberately dependency-free: this script must run in CI and in a bare venv,
    and pulling in Pillow just to draw three shapes is not worth it.
    """
    import struct
    import zlib

    w, h = 320, 220
    px = bytearray(b"\xff" * (w * h * 3))          # white canvas

    def dot(cx, cy, r, rgb):
        for y in range(max(0, cy - r), min(h, cy + r + 1)):
            for x in range(max(0, cx - r), min(w, cx + r + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    px[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(rgb)

    def rect(x0, y0, x1, y1, rgb):
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(rgb)

    dot(60, 90, 40, (200, 30, 30))                 # red circle 1
    dot(160, 90, 40, (200, 30, 30))                # red circle 2
    rect(240, 50, 310, 130, (30, 60, 200))         # blue square

    rows = b"".join(b"\x00" + bytes(px[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit truecolour RGB
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(rows, 9))
            + chunk(b"IEND", b""))


CLAIM = "The image shows three red circles and no square."


def check_sdk_version() -> bool:
    import importlib.metadata as md

    raw = md.version("langfuse")
    parts = tuple(int(x) for x in re.findall(r"\d+", raw)[:2])
    return check(parts >= MIN_SDK, f"langfuse SDK >= {'.'.join(map(str, MIN_SDK))}",
                 f"installed {raw}")


def check_hydration(lf, raw: bytes) -> object:
    from langfuse.media import LangfuseMedia, LangfuseMediaReference

    sha = hashlib.sha256(raw).hexdigest()
    lf.create_dataset(name=DATASET,
                      description="verify_multimodal.py probe — safe to delete")
    lf.create_dataset_item(
        dataset_name=DATASET,
        id="probe-sdk-media",
        input={"question": "Does the photo support the claim?",
               "claim": CLAIM,
               "photo": LangfuseMedia(content_bytes=raw, content_type="image/png")},
        expected_output={"verdict": "contradicted",
                         "why": "two red circles and one blue square"},
        metadata={"probe": "hydration"},
    )
    lf.flush()
    time.sleep(6)

    item = next((i for i in lf.get_dataset(DATASET).items
                 if i.id == "probe-sdk-media"), None)
    if item is None:
        check(False, "media dataset item retrievable")
        return None
    photo = item.input.get("photo")
    if not check(isinstance(photo, LangfuseMediaReference),
                 "dataset item media hydrates to LangfuseMediaReference",
                 type(photo).__name__):
        return None
    try:
        got = photo.fetch_bytes()
        check(hashlib.sha256(got).hexdigest() == sha,
              "fetch_bytes() round-trips", f"{len(got)} B")
        uri = photo.fetch_data_uri()
        check(uri.startswith("data:image/png;base64,"),
              "fetch_data_uri() returns a data URI", f"{len(uri)} chars")
    except Exception as e:
        check(False, "media fetch", f"{type(e).__name__}: {e}")
        return None
    return photo


def check_raw_token() -> None:
    """The stored value is what a managed, text-only judge is handed."""
    status, body = langfuse_api(
        "GET", f"/api/public/dataset-items?datasetName={urllib.parse.quote(DATASET, safe='')}")
    if status != 200:
        check(False, "dataset-items API", f"HTTP {status}")
        return
    item = next((i for i in body.get("data", []) if i["id"] == "probe-sdk-media"), None)
    if item is None:
        check(False, "probe item present in API response")
        return
    stored = (item.get("input") or {}).get("photo")
    check(isinstance(stored, str) and stored.startswith("@@@langfuseMedia:"),
          "stored field is a bare @@@langfuseMedia token "
          "(what a text-only judge sees)",
          str(stored)[:64] + "…")
    info(f"mapped input field stringifies to: {json.dumps(item['input'])[:150]}…")


def check_trace_promotion(lf, raw: bytes) -> None:
    """annotation / production-trace -> dataset, the way the UI action does it."""
    from langfuse import propagate_attributes
    from langfuse.media import LangfuseMedia, LangfuseMediaReference

    sha = hashlib.sha256(raw).hexdigest()
    with propagate_attributes(trace_name="verify-multimodal-trace",
                              session_id="verify-multimodal",
                              tags=[TAG]):
        with lf.start_as_current_observation(as_type="span",
                                            name="verify-multimodal-trace") as span:
            span.update(
                input={"claim": CLAIM,
                       "photo": LangfuseMedia(content_bytes=raw,
                                              content_type="image/png")},
                output={"verdict": "contradicted"},
            )
            trace_id, obs_id = span.trace_id, span.id
    lf.flush()
    time.sleep(12)

    # v2 has no by-id path, so read the trace's observations and pick ours out.
    # `input` arrives as a RAW JSON STRING here (v2 rejects parseIoAsJson), so it
    # has to be parsed before the media token is visible — reading obs["input"]
    # directly would hand back a string and the isinstance check below would
    # pass on the wrong thing.
    try:
        observations = list_observations(trace_id, fields="core,basic,io")
    except RuntimeError as e:
        check(False, "multimodal observation readable", str(e))
        return
    obs = next((o for o in observations if o.get("id") == obs_id), None)
    if obs is None:
        check(False, "multimodal observation readable", f"{obs_id} not in trace")
        return
    stored = (observation_io(obs, "input") or {}).get("photo")
    if not check(isinstance(stored, str) and stored.startswith("@@@langfuseMedia:"),
                 "trace observation stores a media token"):
        return

    # promote it verbatim — exactly what "+ Add to dataset" copies
    lf.create_dataset_item(
        dataset_name=DATASET,
        id="probe-from-trace",
        input={"claim": CLAIM, "photo": stored},
        expected_output={"verdict": "contradicted"},
        metadata={"probe": "promoted-from-trace"},
        source_trace_id=trace_id,
        source_observation_id=obs_id,
    )
    lf.flush()
    time.sleep(6)

    item = next((i for i in lf.get_dataset(DATASET).items
                 if i.id == "probe-from-trace"), None)
    if item is None:
        check(False, "promoted item retrievable")
        return
    photo = item.input.get("photo")
    if check(isinstance(photo, LangfuseMediaReference),
             "promoted token hydrates (annotation -> dataset needs no re-upload)",
             type(photo).__name__):
        try:
            check(hashlib.sha256(photo.fetch_bytes()).hexdigest() == sha,
                  "promoted item bytes round-trip")
        except Exception as e:
            check(False, "promoted item fetch", f"{type(e).__name__}: {e}")


def check_vision_judge(lf) -> None:
    """An SDK evaluator CAN see pixels — the capability the managed judge lacks."""
    from anthropic import Anthropic
    from langfuse import Evaluation
    from langfuse.media import LangfuseMediaReference

    anth = Anthropic()

    def vision(prompt: str, data_uri: str) -> dict:
        b64 = data_uri.split(",", 1)[1]
        mime = data_uri.split(";", 1)[0].removeprefix("data:")
        msg = anth.messages.create(
            model=AGENT_MODEL, max_tokens=400,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": mime, "data": b64}},
                {"type": "text", "text": prompt}]}])
        m = re.search(r"\{.*\}", msg.content[0].text, re.S)
        return json.loads(m.group(0)) if m else {}

    def task(*, item, **kwargs):
        photo = item.input["photo"]
        return vision(
            f'Claim about this image: "{item.input["claim"]}"\n'
            'Reply JSON only: {"verdict": "supported"|"contradicted", '
            '"observed": "<what you see>"}',
            photo.fetch_data_uri())

    def judge(*, input, output, **kwargs):
        photo = input["photo"]
        if not isinstance(photo, LangfuseMediaReference):
            return Evaluation(name="probe-vision-judge", value=0.0,
                              comment="evaluator received no media reference")
        res = vision(
            f'Count the shapes in this image. Reply JSON only: '
            '{"n_circles": <int>, "n_squares": <int>}',
            photo.fetch_data_uri())
        # the fixture is unambiguous: 2 circles, 1 square
        correct = res.get("n_circles") == 2 and res.get("n_squares") == 1
        return Evaluation(name="probe-vision-judge", value=1.0 if correct else 0.0,
                          comment=f"counted circles={res.get('n_circles')} "
                                  f"squares={res.get('n_squares')} (expected 2/1)")

    run_name = "verify-multimodal-vision"
    ds = lf.get_dataset(DATASET)
    result = ds.run_experiment(
        name=run_name,
        run_name=run_name,   # pin it: without this the SDK appends a timestamp
                             # and looking the run up by name 404s
        description="probe: SDK evaluator sees pixels",
        task=task,
        evaluators=[judge],
    )
    lf.flush()

    item_results = [r for r in result.item_results if r.output]
    verdicts = [(r.output or {}).get("verdict") for r in item_results]
    check(any(v == "contradicted" for v in verdicts),
          "vision task read the image (verdict contradicts a false claim)",
          str(verdicts))
    evals = [e for r in item_results for e in r.evaluations
             if e.name == "probe-vision-judge"]
    check(bool(evals) and any(e.value == 1.0 for e in evals),
          "vision judge counted the shapes correctly (pixels reached the evaluator)",
          "; ".join(e.comment for e in evals)[:160])

    # and confirm the score is queryable server-side, not just in-process
    time.sleep(10)
    status, body = langfuse_api(
        "GET",
        f"/api/public/datasets/{urllib.parse.quote(DATASET, safe='')}"
        f"/runs/{urllib.parse.quote(run_name, safe='')}")
    if status != 200:
        check(False, "dataset run readable by pinned run_name", f"HTTP {status}")
        return
    names = set()
    for ri in body.get("datasetRunItems", []):
        if ri.get("traceId"):
            try:
                names |= {s["name"] for s in list_scores(ri["traceId"])}
            except RuntimeError:
                pass  # keep polling the remaining run items
    check("probe-vision-judge" in names, "vision score landed server-side",
          str(sorted(names)))


def check_langgraph_attrs(lf) -> None:
    try:
        from langgraph.graph import END, START, StateGraph  # noqa
    except ImportError:
        info("SKIP  LangGraph propagation — langgraph not installed "
             "(arrives in phase 2; also needs the `langchain` meta-package)")
        return
    from typing import TypedDict

    from langfuse import propagate_attributes
    from langfuse.langchain import CallbackHandler

    class S(TypedDict):
        n: int

    g = StateGraph(S)
    g.add_node("node_a", lambda s: {"n": s["n"] + 1})
    g.add_node("node_b", lambda s: {"n": s["n"] * 2})
    g.add_edge(START, "node_a")
    g.add_edge("node_a", "node_b")
    g.add_edge("node_b", END)
    graph = g.compile()

    user = "verify-multimodal-user"
    with propagate_attributes(trace_name="verify-multimodal-graph",
                              user_id=user, session_id="verify-multimodal",
                              tags=[TAG]):
        # Keep the manual wrapper: it — not LangGraph's own CHAIN root — is what
        # gets isRootObservation=True, which observation-level judges filter on.
        with lf.start_as_current_observation(as_type="span",
                                            name="verify-multimodal-graph") as root:
            out = graph.invoke({"n": 1},
                               config={"callbacks": [CallbackHandler()],
                                       "run_name": "verify-graph"})
            root.update(input={"n": 1}, output=out)
            trace_id = root.trace_id
    lf.flush()
    time.sleep(14)

    status, body = langfuse_api(
        "GET", f"/api/public/v2/observations?traceId={trace_id}&limit=100")
    if status != 200:
        check(False, "graph observations readable", f"HTTP {status}")
        return
    obs = body.get("data", [])
    names = {o["name"] for o in obs}
    check({"node_a", "node_b"} <= names, "graph nodes traced as observations",
          str(sorted(names)))
    with_user = [o for o in obs if o.get("userId") == user]
    check(len(with_user) == len(obs),
          "propagate_attributes reached EVERY node observation "
          "(else observation-level judges match nothing)",
          f"{len(with_user)}/{len(obs)}")
    roots = [o for o in obs if o.get("isRootObservation")]
    check(len(roots) == 1 and roots[0]["name"] == "verify-multimodal-graph",
          "exactly one logical root, and it is the manual wrapper span",
          str([o["name"] for o in roots]))


def cleanup() -> None:
    """Delete the probe ITEMS.

    Verified against the API: `DELETE /api/public/dataset-items/{id}` works, but
    `DELETE /api/public/datasets/{name}` returns 405 — the public API has no
    dataset-delete. So we remove the items and leave the (now empty) dataset
    shell, which has to go from the UI if you care.
    """
    print("\n=== cleanup ===")
    status, body = langfuse_api(
        "GET",
        f"/api/public/dataset-items?datasetName={urllib.parse.quote(DATASET, safe='')}")
    if status != 200:
        print(f"  could not list items: HTTP {status} {str(body)[:120]}")
        return
    items = body.get("data", [])
    if not items:
        print(f"  no items left on {DATASET}")
    for it in items:
        st, b = langfuse_api("DELETE", f"/api/public/dataset-items/{it['id']}")
        print(f"  {'deleted' if st == 200 else f'HTTP {st} on'} item {it['id']}"
              + ("" if st == 200 else f" — {str(b)[:90]}"))
    print(f"  the empty dataset `{DATASET}` remains — the public API has no "
          "dataset-delete (405); remove it from the UI if you want it gone")
    print(f"  probe traces are tagged `{TAG}` — filter and delete from the UI")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-vision", action="store_true",
                    help="Skip the vision-judge check (saves 2 vision calls)")
    ap.add_argument("--cleanup", action="store_true",
                    help="Delete the probe dataset and exit")
    args = ap.parse_args()

    verify_project()
    if args.cleanup:
        cleanup()
        return

    lf = get_langfuse()

    print("\n=== 1. SDK version ===")
    if not check_sdk_version():
        print("\nFAILED — media dataset items cannot hydrate below "
              f"{'.'.join(map(str, MIN_SDK))}. Upgrade and re-run.")
        sys.exit(1)

    raw = make_fixture()
    info(f"fixture: {len(raw)} B synthetic PNG (2 red circles + 1 blue square)")

    print("\n=== 2. media dataset item hydration ===")
    photo = check_hydration(lf, raw)

    print("\n=== 3. what a managed (text-only) judge is handed ===")
    check_raw_token()

    print("\n=== 4. production trace -> dataset item ===")
    check_trace_promotion(lf, raw)

    print("\n=== 5. SDK vision judge ===")
    if args.skip_vision:
        info("SKIP  --skip-vision")
    elif photo is None:
        info("SKIP  hydration failed, so a vision judge cannot run")
    else:
        check_vision_judge(lf)

    print("\n=== 6. LangGraph attribute propagation ===")
    check_langgraph_attrs(lf)

    failed = [label for ok, label, _ in _results if not ok]
    total = len(_results)
    print()
    if failed:
        print(f"VERIFY MULTIMODAL: FAILED — {len(failed)}/{total} checks failed")
        for label in failed:
            print(f"  - {label}")
        print("\nMULTIMODAL_EVAL_SPEC.md section 1a records the expected results.")
        sys.exit(1)
    print(f"VERIFY MULTIMODAL: PASSED — {total}/{total} checks")
    print(f"View: {LANGFUSE_HOST}  (Datasets > {DATASET})")
    print("Clean up with: ./.venv/bin/python scripts/verify_multimodal.py --cleanup")


if __name__ == "__main__":
    main()

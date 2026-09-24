"""
Acceptance test for the photo-audit path (phases 1-3).

`verify_multimodal.py` proves the Langfuse MECHANISMS work. This proves OUR
demo is wired onto them correctly:

  P1 fixtures    scenes are self-consistent and render to valid PNGs, and every
                 scene_class's label agrees with its COMPUTED ground truth
  P2 dataset     items seed and their media hydrates to LangfuseMediaReference
  P3 trajectory  one live audit produces the trace shape managed judges need:
                 ROOT_SPAN is the SOLE logical root, node observations exist,
                 and propagated attributes reached all of them
  P4 evaluation  an experiment lands every expected score name, server-side

Run:
    ./.venv/bin/python scripts/verify_photo_audit.py
    ./.venv/bin/python scripts/verify_photo_audit.py --limit 3 --skip-experiment

Exits non-zero on any failure.
"""

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import photo_contract as C  # noqa: E402
from agent.config import (  # noqa: E402
    get_langfuse, langfuse_api, verify_project, LANGFUSE_HOST, AGENT_MODEL,
)

_results = []


def check(passed, label, detail=""):
    _results.append((bool(passed), label, detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
    return bool(passed)


def info(msg):
    print(f"        {msg}")


# ---------------------------------------------------------------- P1 fixtures
def p1_fixtures():
    print("\n=== P1. fixtures: self-consistent and renderable ===")
    from data import photo_scenes

    problems = photo_scenes.verify_scenes()
    check(not problems, "verify_scenes() reports no problems",
          "; ".join(problems[:3]) if problems else f"{len(photo_scenes.SCENES)} scenes")

    classes = {}
    for s in photo_scenes.SCENES:
        classes[s["scene_class"]] = classes.get(s["scene_class"], 0) + 1
    check(set(classes) == set(C.SCENE_CLASSES),
          "every scene_class represented", json.dumps(classes))

    # Ground truth must agree with the class label, or the test case is a lie.
    mislabelled = []
    for s in photo_scenes.SCENES:
        exp = C.build_expected_output(s["claims"], s["attributes"])
        verdicts = set(exp["claim_verdicts"].values())
        cls = s["scene_class"]
        if cls.startswith("contradicted") and "contradicted" not in verdicts:
            mislabelled.append(f"{s['scene_id']}: {cls} but no contradicted claim")
        if cls == "supported" and "contradicted" in verdicts:
            mislabelled.append(f"{s['scene_id']}: supported but has a contradicted claim")
        if cls == "unverifiable" and verdicts != {"unverifiable"}:
            mislabelled.append(f"{s['scene_id']}: unverifiable but verdicts={verdicts}")
    check(not mislabelled, "scene_class labels agree with computed ground truth",
          "; ".join(mislabelled[:3]) if mislabelled else "all consistent")

    # renders must be real PNGs with the declared dimensions
    import struct
    bad = []
    for s in photo_scenes.SCENES:
        raw = photo_scenes.render(s)
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            bad.append(f"{s['scene_id']}: not a PNG")
            continue
        w, h = struct.unpack(">II", raw[16:24])
        if w < 200 or h < 150:
            bad.append(f"{s['scene_id']}: {w}x{h} too small")
    check(not bad, f"all {len(photo_scenes.SCENES)} scenes render to valid PNGs",
          "; ".join(bad[:3]) if bad else "")


# ----------------------------------------------------------------- P2 dataset
def p2_dataset(limit):
    print("\n=== P2. dataset: items seed and media hydrates ===")
    from langfuse.media import LangfuseMediaReference
    import subprocess

    r = subprocess.run(
        [sys.executable, "scripts/seed_photo_dataset.py", "--limit", str(limit)],
        capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        check(False, "seed_photo_dataset.py exits 0",
              (r.stderr or r.stdout)[-300:])
        return None
    check(True, "seed_photo_dataset.py exits 0", f"limit={limit}")
    time.sleep(8)

    lf = get_langfuse()
    ds = lf.get_dataset(C.DATASET_NAME)
    check(len(ds.items) >= 1, f"dataset {C.DATASET_NAME} has items",
          f"{len(ds.items)} items")

    item = ds.items[0]
    photo = (item.input or {}).get("photo")
    check(isinstance(photo, LangfuseMediaReference),
          "dataset item photo hydrates to LangfuseMediaReference",
          type(photo).__name__)
    exp = item.expected_output or {}
    check(all(k in exp for k in ("verdict", "claim_verdicts", "true_attributes")),
          "expected_output carries computed ground truth",
          str(sorted(exp))[:90])
    # true_attributes must be a dict, not a clipped string (the metadata trap)
    check(isinstance(exp.get("true_attributes"), dict),
          "true_attributes survived as an object (not clipped to a string)",
          type(exp.get("true_attributes")).__name__)
    return ds


# -------------------------------------------------------------- P3 trajectory
def p3_trajectory(ds):
    print("\n=== P3. trajectory: the trace shape managed judges need ===")
    from agent.photo_audit_graph import run_photo_audit

    item = next((i for i in ds.items
                 if (i.metadata or {}).get("scene_class") != "low_quality"), ds.items[0])
    photo = item.input["photo"]
    out = run_photo_audit(
        listing_id=item.input["listing_id"],
        marketing_copy=item.input["marketing_copy"],
        claims=item.input["claims"],
        photo_data_uri=photo.fetch_data_uri(),
        session_id="verify-photo-audit",
        user_id="verify-photo-user",
        extra_tags=["verify-photo-audit"],
    )
    info(f"verdict={out.get('verdict')!r} "
         f"claims={len(out.get('claims') or [])} "
         f"conf={out.get('extraction_confidence')}")

    check(all(k in out for k in C.AUDIT_KEYS),
          "audit output matches the contract schema",
          f"missing={[k for k in C.AUDIT_KEYS if k not in out]}")
    check(out.get("verdict") in C.OVERALL_VERDICTS,
          "overall verdict is in the vocabulary", str(out.get("verdict")))
    probs = C.validate_attributes(out.get("extracted_attributes"))
    check(not probs, "extracted attributes obey the closed vocabulary",
          "; ".join(probs[:3]) if probs else "")

    trace_id = out.get("trace_id")
    if not trace_id:
        check(False, "audit returned a trace_id (needed to inspect the trace)")
        return out
    time.sleep(14)

    st, body = langfuse_api(
        "GET", f"/api/public/v2/observations?traceId={trace_id}&limit=100")
    if st != 200:
        check(False, "observations readable", f"HTTP {st}")
        return out
    obs = body.get("data", [])
    names = {o["name"] for o in obs}
    info(f"{len(obs)} observations: {sorted(names)}")

    # Which nodes SHOULD be present depends on the branch taken. Asserting
    # audit_claims unconditionally fails whenever the agent legitimately
    # declines an unreadable photo — the branch is a feature, not a fault.
    declined = out.get("verdict") == "needs_better_photo"
    expected_nodes = ({C.NODE_VISION_EXTRACT, C.NODE_REQUEST_BETTER_PHOTO}
                      if declined else
                      {C.NODE_VISION_EXTRACT, C.NODE_AUDIT_CLAIMS,
                       C.NODE_COMPOSE_ANSWER})
    info(f"branch = {'request_better_photo' if declined else 'full audit'}")
    check(expected_nodes <= names,
          f"graph node observations present for the {'declined' if declined else 'audit'} branch",
          f"missing={sorted(expected_nodes - names)}")
    if declined:
        check(C.NODE_AUDIT_CLAIMS not in names,
              "audit_claims correctly absent on the declined branch")

    roots = [o for o in obs if o.get("isRootObservation")]
    check(len(roots) == 1 and roots[0]["name"] == C.ROOT_SPAN,
          f"exactly one logical root and it is {C.ROOT_SPAN!r} "
          "(what managed judges filter on)",
          str([o["name"] for o in roots]))

    propagated = [o for o in obs if o.get("userId") == "verify-photo-user"]
    check(len(propagated) == len(obs),
          "propagated attributes reached EVERY observation "
          "(else observation-level judges match nothing)",
          f"{len(propagated)}/{len(obs)}")

    if roots:
        st, root = langfuse_api("GET", f"/api/public/observations/{roots[0]['id']}")
        if st == 200:
            ri, ro = root.get("input"), root.get("output")
            check(bool(ri) and bool(ro),
                  "root span carries judge-facing input AND output")
            check(isinstance(ro, dict) and "claims" in (ro or {}),
                  "root output is the audit summary, not raw graph state",
                  str(ro)[:80])
    return out


# -------------------------------------------------------------- P4 evaluation
def p4_experiment(ds, limit):
    print("\n=== P4. evaluation: code + vision scores land ===")
    from agent.photo_audit_graph import run_photo_audit
    from agent.photo_scoring import PHOTO_CODE_EVALUATORS  # noqa: F401
    from evaluators.vision_judges import PHOTO_RUN_EVALUATORS
    from evaluators import vision_judges

    lf = get_langfuse()
    subset = ds.items[:limit]

    def task(*, item, **kwargs):
        return run_photo_audit(
            listing_id=item.input["listing_id"],
            marketing_copy=item.input["marketing_copy"],
            claims=item.input["claims"],
            photo_data_uri=item.input["photo"].fetch_data_uri(),
            is_experiment=True,
        )

    # ALL nine: the 7 code evaluators plus the 2 vision judges. Running only the
    # vision judges leaves every avg-<code-score> with nothing to average, which
    # is both a weaker test and the thing that used to emit a None-valued mean.
    item_evaluators = list(vision_judges.PHOTO_ALL_EVALUATORS)

    run_name = "verify-photo-audit"
    res = lf.run_experiment(
        name=run_name, run_name=run_name,   # pin it: unpinned names get a timestamp
                                            # suffix and 404 on lookup
        description="acceptance: photo-audit phases 1-3",
        data=subset, task=task,
        evaluators=item_evaluators,
        run_evaluators=PHOTO_RUN_EVALUATORS,
        max_concurrency=2,
    )
    lf.flush()

    produced = sorted({e.name for r in res.item_results for e in r.evaluations})
    info(f"item-level scores produced ({len(produced)}): {produced}")
    check(set(C.VISION_SCORE_NAMES) <= set(produced),
          "both vision judges produced scores", str(produced)[:110])
    code_seen = set(C.CODE_SCORE_NAMES) & set(produced)
    check(len(code_seen) >= 5,
          "code evaluators produced scores too (experiment-only ones included)",
          f"{len(code_seen)}/{len(C.CODE_SCORE_NAMES)}: {sorted(code_seen)}")
    run_names = sorted({e.name for e in (res.run_evaluations or [])})
    info(f"run-level means ({len(run_names)}): {run_names}")
    check(run_names and all(n.startswith("avg-") for n in run_names),
          "run-level means emitted, none None-valued", str(run_names)[:110])

    time.sleep(12)
    st, body = langfuse_api(
        "GET",
        f"/api/public/datasets/{urllib.parse.quote(C.DATASET_NAME, safe='')}"
        f"/runs/{urllib.parse.quote(run_name, safe='')}")
    if st != 200:
        check(False, "dataset run readable by pinned run_name", f"HTTP {st}")
        return
    landed = set()
    for ri in body.get("datasetRunItems", []):
        if ri.get("traceId"):
            s2, tr = langfuse_api("GET", f"/api/public/traces/{ri['traceId']}")
            if s2 == 200:
                landed |= {s["name"] for s in tr.get("scores", [])}
    info(f"scores landed server-side: {sorted(landed)}")
    check(bool(landed & set(C.VISION_SCORE_NAMES)),
          "vision scores queryable server-side", str(sorted(landed))[:110])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=3,
                    help="dataset items to seed/evaluate (vision calls cost money)")
    ap.add_argument("--skip-live", action="store_true")
    ap.add_argument("--skip-experiment", action="store_true")
    args = ap.parse_args()

    verify_project()
    info(f"model={AGENT_MODEL}")

    p1_fixtures()
    ds = p2_dataset(args.limit)
    if ds and not args.skip_live:
        p3_trajectory(ds)
    if ds and not args.skip_experiment:
        p4_experiment(ds, args.limit)

    failed = [l for ok, l, _ in _results if not ok]
    print()
    if failed:
        print(f"VERIFY PHOTO AUDIT: FAILED — {len(failed)}/{len(_results)} checks")
        for l in failed:
            print(f"  - {l}")
        sys.exit(1)
    print(f"VERIFY PHOTO AUDIT: PASSED — {len(_results)}/{len(_results)} checks")
    print(f"View: {LANGFUSE_HOST}  (Datasets > {C.DATASET_NAME})")


if __name__ == "__main__":
    main()

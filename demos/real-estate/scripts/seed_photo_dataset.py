"""
Create/refresh the multi-modal 'multimodal/property-photo-audit' dataset.

One item per scene in data/photo_scenes.py: a RENDERED property photo plus the
marketing copy whose claims that photo either bears out, refutes, or cannot
settle. The photo goes in as a `LangfuseMedia` object, which is the only way to
get pixels into a dataset item — CSV/JSON import is text-only
(MULTIMODAL_EVAL_SPEC.md §3.2).

Nothing here is hand-labelled. `expected_output` comes from
`agent.photo_contract.build_expected_output()`, computed from the same
attributes the renderer drew, so ground truth cannot drift away from the
fixture. If you find yourself wanting to correct an expected verdict, the scene
is wrong, not the label.

Run:
    ./.venv/bin/python scripts/seed_photo_dataset.py
    ./.venv/bin/python scripts/seed_photo_dataset.py --dry-run     # offline
    ./.venv/bin/python scripts/seed_photo_dataset.py --limit 3     # cheap probe
"""

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.photo_contract import (  # noqa: E402
    BASE_TAGS,
    DATASET_NAME,
    PROVENANCE_RENDERED,
    SCENE_CLASS_LOW_QUALITY,
    build_expected_output,
    marketing_copy,
)


def _expected_for(scene: dict) -> dict:
    """Ground truth for one scene, computed — never authored.

    `low_quality` scenes are passed `unreadable=True`, which makes their
    expected verdict `needs_better_photo` and sets `claim_verdicts_apply=False`.
    Without that, an agent behaving CORRECTLY on an unreadable photo (abstain
    and ask for a better one) would be graded against claim verdicts derived
    from attributes it had no fair way to see, and marked wrong.
    """
    return build_expected_output(
        scene["claims"],
        scene["attributes"],
        unreadable=scene["scene_class"] == SCENE_CLASS_LOW_QUALITY,
    )
from data.photo_scenes import (  # noqa: E402
    DATASET_DESCRIPTION,
    SCENES,
    render,
    verify_scenes,
)

# Langfuse uploads media on a BACKGROUND thread, so flush() returning does not
# mean the bytes are stored. Items read back too early hydrate to a reference
# whose fetch fails. 8s was comfortable for 21 small PNGs in the spike
# (verify_multimodal.py used 6s for one); raise it if a readback 404s.
MEDIA_UPLOAD_SETTLE_SECONDS = 8


def _describe(scene: dict, png: bytes) -> None:
    """One reviewable block per item — the whole point of --dry-run."""
    attrs = scene["attributes"]
    expected = _expected_for(scene)
    sha = hashlib.sha256(png).hexdigest()[:12]
    print(f"\n  id={scene['scene_id']}  [{scene['scene_class']}]  "
          f"{scene['listing_id']}")
    print(f'    marketing_copy: "{marketing_copy(scene["claims"])}"')
    print(f"    photo:          {len(png)} B image/png  sha256:{sha}…")
    print(f"    verdict:        {expected['verdict']}")
    for claim, verdict in expected["claim_verdicts"].items():
        print(f"      {verdict:<13} {claim}")
    print(f"    true_attributes: {expected['true_attributes']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Render and print the items; touch no network")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="Only seed the first N scenes (cheap probe)")
    args = ap.parse_args()

    # Refuse to seed a fixture set whose class labels disagree with the
    # verdicts computed from its own renders. Silently-broken items are worse
    # than no items: the experiment still runs, the comparison still produces
    # numbers, and the numbers mean nothing. strict=True raises with the full
    # list of problems rather than returning it — a seeder has no sensible way
    # to carry on past this.
    verify_scenes(strict=True)

    scenes = list(SCENES)[:args.limit] if args.limit else list(SCENES)
    mix: dict = {}
    for s in scenes:
        mix[s["scene_class"]] = mix.get(s["scene_class"], 0) + 1
    print(f"{len(scenes)} scene(s), class mix {mix}")

    if args.dry_run:
        # agent.config is imported LAZILY (below) rather than at module scope
        # precisely so this path works with no keys and no network — it reads
        # .env and hard-fails on a missing LANGFUSE_PUBLIC_KEY at import time.
        print(f"\nDRY RUN — would upsert into dataset '{DATASET_NAME}'")
        for scene in scenes:
            _describe(scene, render(scene))
        print(f"\n✓ dry run: {len(scenes)} item(s) rendered, nothing sent.")
        return 0

    from agent.config import get_langfuse, verify_project
    from langfuse.media import LangfuseMedia

    verify_project()
    lf = get_langfuse()

    try:
        lf.create_dataset(
            name=DATASET_NAME,               # the '/' puts it in a Langfuse
            description=DATASET_DESCRIPTION,  # dataset folder — free demo of
            metadata={"source": "demos/real-estate",   # folders while we're
                      "modality": "image+text",         # here
                      "photo_provenance": PROVENANCE_RENDERED,
                      "tags": BASE_TAGS})
        print(f"✓ Created dataset: {DATASET_NAME}")
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"• Dataset already exists: {DATASET_NAME} (refreshing items)")
        else:
            print(f"! create_dataset warning: {e}")

    created = 0
    for i, scene in enumerate(scenes, 1):
        try:
            png = render(scene)
            expected = _expected_for(scene)
            lf.create_dataset_item(
                # id = scene_id, so re-running UPSERTS in place instead of
                # duplicating. It also means an item's id says what the item
                # tests, which matters when you are staring at a failed run.
                # Media dedups on sha256, so an unchanged photo costs no
                # additional storage on a re-seed.
                id=scene["scene_id"],
                dataset_name=DATASET_NAME,
                input={
                    "listing_id": scene["listing_id"],
                    "marketing_copy": marketing_copy(scene["claims"]),
                    # The claim LIST as well as the prose: the audit graph
                    # adjudicates claim-by-claim, and re-parsing them out of
                    # the sentence would make claim-coverage a test of the
                    # parser instead of a test of the auditor.
                    "claims": scene["claims"],
                    "photo": LangfuseMedia(content_bytes=png,
                                           content_type="image/png"),
                },
                # Computed, never authored — see _expected_for() above.
                # `low_quality` items correctly expect "needs_better_photo" and
                # carry claim_verdicts_apply=False, so evaluators can grade them
                # straight off expected_output without special-casing.
                expected_output=expected,
                # Metadata values are coerced to strings and capped at 200
                # chars by Langfuse, so only short scalars go here — the
                # nested attribute dict lives in expected_output instead.
                metadata={"scene_class": scene["scene_class"],
                          "scene_id": scene["scene_id"],
                          "photo_provenance": PROVENANCE_RENDERED},
            )
            created += 1
            print(f"  [{i:2}] {scene['scene_id']:<38} "
                  f"{scene['scene_class']:<21} {len(png):>6} B  "
                  f"-> {expected['verdict']}")
        except Exception as e:
            print(f"  [{i:2}] ERROR on {scene['scene_id']}: "
                  f"{type(e).__name__}: {e}")

    lf.flush()
    # flush() drains the event queue, but media bytes upload on their own
    # background thread — an item fetched immediately after can come back with
    # a reference whose bytes are not there yet. Same class of trap as the
    # staged trace-consistency one: a 200 is not proof the payload landed.
    print(f"\nwaiting {MEDIA_UPLOAD_SETTLE_SECONDS}s for background media "
          f"upload to finish…")
    time.sleep(MEDIA_UPLOAD_SETTLE_SECONDS)

    print(f"✓ {created}/{len(scenes)} items in '{DATASET_NAME}'.")
    print(f"  View: Langfuse UI > Datasets > {DATASET_NAME}")
    return 0 if created == len(scenes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

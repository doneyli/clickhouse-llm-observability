"""
The 21 photo fixtures for the listing-photo-audit demo — and the ONLY module in
the repo that knows how pixels are made.

Two things live here, deliberately together:

  1. `SCENES` — the ground truth. Each scene names the TRUE countable
     attributes of a room and the claims the marketing copy makes about it.
  2. `render(scene)` — turns those attributes into a PNG using nothing but
     `zlib` + `struct`.

Because (2) is a pure function of (1), the dataset is self-labelling: nobody
hand-writes a verdict anywhere. `agent/photo_contract.build_expected_output()`
computes it from the attributes, and the attributes are what got drawn. A real
photo cannot offer that — it needs a human to say what is in it, and that
label, not the model, becomes the thing you are really measuring.

--------------------------------------------------------------------------
THESE ARE SCHEMATIC RENDERS, NOT PHOTOGRAPHS
--------------------------------------------------------------------------
Flat-colour elevations of a wall, a floor, a counter run and some appliance
silhouettes. Say so on stage — it is a documented trade, made for two reasons:

  * Licensing. This is a public repo. Scraped listing photos are not
    committable, and a generated photo-realistic set still needs per-file
    provenance (MULTIMODAL_EVAL_SPEC.md §3.2).
  * Exact ground truth. "3 windows" is a fact about this file, not an opinion
    about a JPEG, so a code evaluator may assert it HARD.

The cost is external validity: a vision model that reads schematic boxes
perfectly may still misread a real kitchen. So the demo's *mechanics* transfer;
its *absolute scores* do not. Swapping in real photography is a one-module
change by design — replace `render()` with a file lookup and give each file a
provenance entry. Do that before a customer engagement where the numbers matter.

Nothing outside the contract's closed vocabulary is drawn. No sofas, no plants,
no decoration — if it is in the pixels it is in the ground truth. That is why
the `living_room` scenes look sparse: a living room whose vocabulary is
"flooring, windows, clutter" IS a wall, a floor and some windows.

--------------------------------------------------------------------------
DETERMINISM IS LOAD-BEARING
--------------------------------------------------------------------------
Same scene → same bytes, every run, on every machine. Speckle and clutter
jitter come from a seeded LCG, never `random` and never `hash()` (str hashing
is salted per process). Langfuse dedups media on
`project + content_type + sha256`, so stable bytes mean re-seeding the dataset
re-uses the uploaded object instead of storing 21 more copies.

Run it to look at them:
    python3.11 data/photo_scenes.py        # renders data/photos_preview/*.png
"""

import struct
import sys
import zlib
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

# `python3.11 data/photo_scenes.py` puts data/ on sys.path, not the demo root,
# so `agent.photo_contract` would not resolve. Same two lines every script in
# scripts/ uses; kept here so the module is runnable AND importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.photo_contract import (  # noqa: E402
    APPLIANCES,
    CLAIMS_BY_TEXT,
    COUNTABLE_KEYS,
    SCENE_CLASSES,
    build_expected_output,
    derive_interpretive,
    expected_verdict,
    marketing_copy,
    validate_attributes,
)

DATASET_DESCRIPTION = (
    "Multi-modal listing-photo audit. Each item pairs a RENDERED property photo "
    "(schematic, stdlib-drawn — see data/photo_scenes.py) with marketing copy whose "
    "claims are true, false or unverifiable against that photo. Ground truth is "
    "computed from the render's own attributes, never hand-labelled. Composition is "
    "the experiment design: `contradicted_subtle` separates a real vision judge from "
    "a caption-then-judge proxy, and `unverifiable` catches agents that guess instead "
    "of abstaining."
)

# Where `__main__` writes previews. Scratch output for eyeballing, not an
# artifact anything imports — the seeder renders in memory.
PREVIEW_DIR = _ROOT / "data" / "photos_preview"


# ==========================================================================
# 1. THE SCENES
# ==========================================================================
# Proportions are from MULTIMODAL_EVAL_SPEC.md §3.2 and are pinned by
# `verify_scenes()`, because the mix IS the experiment: drop the two
# `contradicted_subtle` appliance items and the proxy-judge comparison — the
# entire point of the demo — quietly stops being able to fail.
INTENDED_MIX: Dict[str, int] = {
    "supported": 6,
    "contradicted_visible": 6,
    "contradicted_subtle": 4,
    "unverifiable": 3,
    "low_quality": 2,
}

# Claim texts, aliased so a scene's claim list reads as a sentence and a typo
# is an ImportError instead of a silently-unverifiable claim. Every string here
# must exist in the contract's CLAIM_SPECS.
_RENOVATED = "recently renovated"          # interpretive, visible
_LIGHT = "floods with natural light"       # interpretive, visible
_STONE = "stone countertops"               # countable,   visible
_EQUIPPED = "fully equipped kitchen"       # countable,   SUBTLE
_WOOD = "hardwood floors throughout"       # countable,   visible
_DISHWASHER = "dishwasher included"        # countable,   SUBTLE
_QUIET = "on a quiet street"               # unverifiable
_CHARGES = "low monthly service charges"   # unverifiable

_ALL_APPLIANCES = list(APPLIANCES)         # oven, fridge, dishwasher

# `listing_id`s are REAL ids out of agent/catalog.py, not invented ones, so the
# audit graph's retrieve_listing node has something to retrieve and the
# `listing-cited` code evaluator can check a citation against the same catalog
# the concierge uses. verify_scenes() asserts they still exist.
SCENES: Tuple[Dict[str, Any], ...] = (

    # ---------------------------------------------------------------- 6 ---
    # supported — the copy is accurate. The control class: an auditor that
    # flags a contradiction here is over-eager, which is a real production
    # failure (nobody ships an auditor that rewrites correct copy).
    {
        "scene_id": "sup-kitchen-all-true",
        "listing_id": "MAD-101",
        "scene_class": "supported",
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "wood",
                       "clutter": "clear", "window_count": 2,
                       "appliances": _ALL_APPLIANCES},
        # Every claim in the vocabulary that can be true, is. If an auditor
        # contradicts anything on this item, the problem is the auditor.
        "claims": [_RENOVATED, _LIGHT, _STONE, _WOOD, _EQUIPPED, _DISHWASHER],
    },
    {
        "scene_id": "sup-kitchen-stone-bright",
        "listing_id": "VLC-301",
        "scene_class": "supported",
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "tile",
                       "clutter": "clear", "window_count": 3,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_RENOVATED, _LIGHT, _STONE, _DISHWASHER],
    },
    {
        "scene_id": "sup-living-bright-wood",
        "listing_id": "BCN-201",
        "scene_class": "supported",
        "attributes": {"room_type": "living_room", "cabinetry": "none",
                       "countertop": "none", "flooring": "wood",
                       "clutter": "clear", "window_count": 3,
                       "appliances": []},
        "claims": [_LIGHT, _WOOD],
    },
    {
        "scene_id": "sup-kitchen-equipped-moderate-clutter",
        "listing_id": "LIS-101",
        "scene_class": "supported",
        # clutter=moderate still derives condition=renovated (only `cluttered`
        # blocks it), so "recently renovated" is genuinely supported here. A
        # tidy-but-lived-in room the auditor must not mark down.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "wood",
                       "clutter": "moderate", "window_count": 2,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_RENOVATED, _WOOD, _EQUIPPED, _DISHWASHER],
    },
    {
        "scene_id": "sup-bath-renovated-one-window",
        "listing_id": "PAR-101",
        "scene_class": "supported",
        # One window → natural_light=moderate, so the copy pointedly does NOT
        # claim "floods with natural light". Restraint in the copy is what
        # makes this a control rather than a contradiction.
        "attributes": {"room_type": "bathroom", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "tile",
                       "clutter": "clear", "window_count": 1,
                       "appliances": []},
        "claims": [_RENOVATED, _STONE],
    },
    {
        "scene_id": "sup-kitchen-modest-but-accurate",
        "listing_id": "BER-101",
        "scene_class": "supported",
        # The hardest control: dark dated cabinets and a laminate worktop are
        # right there in frame, but the copy never claims otherwise. An auditor
        # anchoring on "this room looks dated" instead of on the actual claims
        # will contradict something here — and be wrong.
        "attributes": {"room_type": "kitchen", "cabinetry": "dark_dated",
                       "countertop": "laminate", "flooring": "wood",
                       "clutter": "clear", "window_count": 2,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_WOOD, _LIGHT, _EQUIPPED, _DISHWASHER],
    },

    # ---------------------------------------------------------------- 6 ---
    # contradicted_visible — the contradiction is a whole material or a window
    # count, unmissable at a glance. Every eval layer should catch these; a
    # layer that misses one is broken, not merely weak.
    {
        "scene_id": "con-vis-kitchen-dated-dark",
        "listing_id": "BCN-202",
        "scene_class": "contradicted_visible",
        # The spec's headline example: "recently renovated, floods with natural
        # light" over a dark dated galley with one window and laminate tops.
        # Three visible contradictions in one line of copy.
        "attributes": {"room_type": "kitchen", "cabinetry": "dark_dated",
                       "countertop": "laminate", "flooring": "tile",
                       "clutter": "moderate", "window_count": 1,
                       "appliances": ["oven", "fridge"]},
        "claims": [_RENOVATED, _LIGHT, _STONE],
    },
    {
        "scene_id": "con-vis-living-windowless-carpet",
        "listing_id": "SVQ-401",
        "scene_class": "contradicted_visible",
        # clutter=moderate rather than clear, purely so the frame is not almost
        # featureless. A living room with no windows, no cabinetry and nothing
        # on the floor renders as two flat bands, and a near-empty frame invites
        # "I cannot tell" — which would route a plainly-contradicted item into
        # request_better_photo and look like a bug in the graph. Two objects
        # give the extractor something to anchor on. clutter does not enter any
        # of this item's claims, so ground truth is unchanged.
        "attributes": {"room_type": "living_room", "cabinetry": "none",
                       "countertop": "none", "flooring": "carpet",
                       "clutter": "moderate", "window_count": 0,
                       "appliances": []},
        "claims": [_LIGHT, _WOOD],
    },
    {
        "scene_id": "con-vis-kitchen-laminate-not-stone",
        "listing_id": "AMS-101",
        "scene_class": "contradicted_visible",
        # Mixed on purpose: four claims are true, two are false. Tests that the
        # auditor adjudicates claim-by-claim instead of ruling on the vibe of
        # the whole listing.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "laminate", "flooring": "wood",
                       "clutter": "clear", "window_count": 2,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_RENOVATED, _STONE, _LIGHT, _WOOD, _EQUIPPED, _DISHWASHER],
    },
    {
        "scene_id": "con-vis-bath-tile-not-wood",
        "listing_id": "ROM-101",
        "scene_class": "contradicted_visible",
        # dark_dated + cluttered is the only route to condition=needs_work, so
        # this is the one scene that exercises that branch of the contract.
        "attributes": {"room_type": "bathroom", "cabinetry": "dark_dated",
                       "countertop": "laminate", "flooring": "tile",
                       "clutter": "cluttered", "window_count": 1,
                       "appliances": []},
        "claims": [_WOOD, _RENOVATED],
    },
    {
        "scene_id": "con-vis-kitchen-no-windows",
        "listing_id": "ATH-101",
        "scene_class": "contradicted_visible",
        # A genuinely renovated kitchen with ZERO windows. The false claim is
        # the light, and only the light — so a judge that scores "does the copy
        # feel right" passes it and a judge that reads claims does not.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "tile",
                       "clutter": "clear", "window_count": 0,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_LIGHT, _RENOVATED, _STONE, _EQUIPPED],
    },
    {
        "scene_id": "con-vis-living-cluttered-tile",
        "listing_id": "DUB-101",
        "scene_class": "contradicted_visible",
        "attributes": {"room_type": "living_room", "cabinetry": "none",
                       "countertop": "none", "flooring": "tile",
                       "clutter": "cluttered", "window_count": 1,
                       "appliances": []},
        "claims": [_RENOVATED, _WOOD, _LIGHT],
    },

    # ---------------------------------------------------------------- 4 ---
    # contradicted_subtle — the ONLY false claims are appliance-inventory ones.
    # This is the class the demo is built to win or lose on: the contradiction
    # is not a wrong material, it is a missing item among several, which
    # survives being summarised. A caption that says "modern kitchen
    # appliances" is not wrong — it just cannot answer "is the dishwasher
    # there?", so the text-only proxy judge has nothing to reason over and
    # degrades exactly where the extractor was vague. Enforced by
    # verify_scenes(): every contradicted claim in this class must be a
    # `difficulty="subtle"` one.
    {
        "scene_id": "con-sub-kitchen-missing-dishwasher",
        "listing_id": "MAD-102",
        "scene_class": "contradicted_subtle",
        # Renovated, bright, stone, wood — all true. Two appliances, not three.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "wood",
                       "clutter": "clear", "window_count": 2,
                       "appliances": ["oven", "fridge"]},
        "claims": [_RENOVATED, _LIGHT, _STONE, _WOOD, _EQUIPPED, _DISHWASHER],
    },
    {
        "scene_id": "con-sub-kitchen-no-fridge",
        "listing_id": "BCN-203",
        "scene_class": "contradicted_subtle",
        # The sharpest pair in the set: "dishwasher included" is TRUE while
        # "fully equipped kitchen" is FALSE. Getting both right requires an
        # actual inventory, not a general impression of the room.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "tile",
                       "clutter": "clear", "window_count": 3,
                       "appliances": ["oven", "dishwasher"]},
        "claims": [_RENOVATED, _LIGHT, _STONE, _EQUIPPED, _DISHWASHER],
    },
    {
        "scene_id": "con-sub-kitchen-no-oven",
        "listing_id": "LIS-102",
        "scene_class": "contradicted_subtle",
        # Mirror image of the above: the absent appliance is the oven, the
        # loudest silhouette in the render. If the extractor lists appliances
        # by shape it catches this; if it lists them by expectation it does not.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "wood",
                       "clutter": "moderate", "window_count": 2,
                       "appliances": ["fridge", "dishwasher"]},
        "claims": [_RENOVATED, _WOOD, _EQUIPPED, _DISHWASHER],
    },
    {
        "scene_id": "con-sub-kitchen-dishwasher-absent",
        "listing_id": "VIE-101",
        "scene_class": "contradicted_subtle",
        # Deliberately short copy: exactly one true claim and one false one, so
        # a per-claim score on this item is unambiguous rather than averaged
        # into agreement.
        "attributes": {"room_type": "kitchen", "cabinetry": "dark_dated",
                       "countertop": "laminate", "flooring": "tile",
                       "clutter": "clear", "window_count": 2,
                       "appliances": ["oven", "fridge"]},
        "claims": [_LIGHT, _DISHWASHER],
    },

    # ---------------------------------------------------------------- 3 ---
    # unverifiable — no interior photo can settle these. The right answer is
    # abstention, and the trap is that the photos are perfectly nice: an agent
    # rewarded for being agreeable will say "supported, and what a lovely
    # kitchen". Only two such claim texts exist in the contract, so the three
    # items are the two singletons plus the pair.
    {
        "scene_id": "unv-kitchen-quiet-street",
        "listing_id": "AGP-501",
        "scene_class": "unverifiable",
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "wood",
                       "clutter": "clear", "window_count": 2,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_QUIET],
    },
    {
        "scene_id": "unv-living-service-charges",
        "listing_id": "BIO-601",
        "scene_class": "unverifiable",
        "attributes": {"room_type": "living_room", "cabinetry": "none",
                       "countertop": "none", "flooring": "carpet",
                       "clutter": "moderate", "window_count": 1,
                       "appliances": []},
        "claims": [_CHARGES],
    },
    {
        "scene_id": "unv-bath-street-and-charges",
        "listing_id": "VLC-302",
        "scene_class": "unverifiable",
        "attributes": {"room_type": "bathroom", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "tile",
                       "clutter": "clear", "window_count": 1,
                       "appliances": []},
        "claims": [_QUIET, _CHARGES],
    },

    # ---------------------------------------------------------------- 2 ---
    # low_quality — darkened and blurred until the attributes are not readable,
    # so extraction_confidence should fall below CONFIDENCE_FLOOR and the graph
    # should route to request_better_photo instead of auditing.
    #
    # ⚠️ The two items carry OPPOSITE ground truth (one all-supported, one
    # containing a contradiction) on purpose. If both were contradicted, an
    # agent that hallucinated "contradicted" on an unreadable photo would score
    # correct for the wrong reason, and the whole branch would look like it
    # worked. With the pair, guessing costs you one item either way.
    {
        "scene_id": "low-kitchen-dark-blurred",
        "listing_id": "AGP-502",
        "scene_class": "low_quality",
        # True copy over an unreadable photo. The photo cannot support it even
        # though reality does — "ask for a better photo" is the only honest
        # answer, and "contradicted" is the tempting wrong one.
        "attributes": {"room_type": "kitchen", "cabinetry": "light_modern",
                       "countertop": "stone", "flooring": "wood",
                       "clutter": "clear", "window_count": 2,
                       "appliances": _ALL_APPLIANCES},
        "claims": [_RENOVATED, _LIGHT, _STONE],
    },
    {
        "scene_id": "low-living-underexposed",
        "listing_id": "SVQ-402",
        "scene_class": "low_quality",
        "attributes": {"room_type": "living_room", "cabinetry": "none",
                       "countertop": "none", "flooring": "wood",
                       "clutter": "moderate", "window_count": 1,
                       "appliances": []},
        "claims": [_WOOD, _LIGHT],
    },
)

SCENES_BY_ID: Dict[str, Dict[str, Any]] = {s["scene_id"]: s for s in SCENES}

# Appliance-inventory claims. Only meaningful in a kitchen, so verify_scenes()
# refuses them elsewhere: a bathroom that "contradicts" a dishwasher claim
# would be a true-but-useless test case.
_APPLIANCE_CLAIMS = (_EQUIPPED, _DISHWASHER)


# ==========================================================================
# 2. RENDERER
# ==========================================================================
# stdlib only (zlib + struct), exactly like scripts/verify_multimodal.py's
# fixture: this has to work in CI and in a bare venv, and pulling Pillow in to
# draw rectangles is not a dependency worth carrying.
#
# 640x420, an elevation view with no perspective. Layout is fixed so a diff
# between two renders is always an attribute change:
#
#   y   0.. 34  ceiling band
#   y  34..300  wall            windows sit at y 44..152, x 30..330
#   y 226..240  countertop band (over the cabinet run)
#   y 240..300  cabinet run     x 20..344
#   y 208..300  appliances      x 356..636, three 80-wide slots
#   y 300..420  floor           clutter sits here
#
# Windows are confined to the left band and appliances to the right, so a
# counting model never has to separate a window from a fridge.

W, H = 640, 420
_FLOOR_Y = 300
_WALL_TOP = 34

_WALL_BY_LIGHT = {            # tracks window_count via derive_interpretive:
    "bright": (232, 230, 224),   # a bright wall with no windows would be a
    "moderate": (206, 203, 196),  # render that contradicts its own ground
    "dim": (176, 173, 166),       # truth, so brightness follows the count.
}
_GLASS = (252, 250, 238)      # near-white: reads as daylight, not as a poster
_FRAME = (92, 88, 82)
_BASEBOARD = (150, 147, 140)


def _rng(seed_text: str):
    """Tiny deterministic LCG.

    Not `random` (global state, and seeding it from a str is awkward) and
    emphatically not `hash()` — str hashing is salted per interpreter run, so
    `hash()` would give a scene different bytes on every invocation, changing
    its sha256 and defeating Langfuse's media dedup.
    """
    state = zlib.crc32(seed_text.encode()) & 0xFFFFFFFF

    def nxt(n: int) -> int:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state % n
    return nxt


class _Canvas:
    """A flat RGB byte buffer with just enough drawing to describe a room."""

    __slots__ = ("w", "h", "px")

    def __init__(self, w: int, h: int, rgb: Sequence[int]):
        self.w, self.h = w, h
        self.px = bytearray(bytes(rgb) * (w * h))

    def rect(self, x0: int, y0: int, x1: int, y1: int, rgb: Sequence[int]) -> None:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.w, x1), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(rgb) * (x1 - x0)
        stride = self.w * 3
        for y in range(y0, y1):
            o = y * stride + x0 * 3
            self.px[o:o + len(row)] = row

    def frame(self, x0: int, y0: int, x1: int, y1: int,
              rgb: Sequence[int], t: int = 3) -> None:
        self.rect(x0, y0, x1, y0 + t, rgb)
        self.rect(x0, y1 - t, x1, y1, rgb)
        self.rect(x0, y0, x0 + t, y1, rgb)
        self.rect(x1 - t, y0, x1, y1, rgb)

    def disc(self, cx: int, cy: int, r: int, rgb: Sequence[int]) -> None:
        b = bytes(rgb)
        for y in range(max(0, cy - r), min(self.h, cy + r + 1)):
            dy = y - cy
            span = int((r * r - dy * dy) ** 0.5)
            x0, x1 = max(0, cx - span), min(self.w, cx + span + 1)
            if x1 > x0:
                o = (y * self.w + x0) * 3
                self.px[o:o + (x1 - x0) * 3] = b * (x1 - x0)

    def darken(self, factor: float) -> None:
        """Global multiply via a 256-byte translate table — one C-level pass."""
        table = bytes(min(255, int(v * factor)) for v in range(256))
        self.px = self.px.translate(table)

    def blur(self, radius: int) -> None:
        """Separable box blur with running sums: O(w*h) per axis, not O(w*h*r²).

        The naive 2D version is ~(2r+1)² taps per pixel and takes tens of
        seconds in CPython at this size. Two of these passes approximate a
        Gaussian closely enough to read as an out-of-focus phone photo.
        """
        w, h, win = self.w, self.h, 2 * radius + 1
        stride = w * 3
        src, dst = self.px, bytearray(len(self.px))

        for y in range(h):                                   # horizontal
            base = y * stride
            for c in range(3):
                s = sum(src[base + min(max(i, 0), w - 1) * 3 + c]
                        for i in range(-radius, radius + 1))
                for x in range(w):
                    dst[base + x * 3 + c] = s // win
                    s += (src[base + min(x + radius + 1, w - 1) * 3 + c]
                          - src[base + max(x - radius, 0) * 3 + c])

        src, out = dst, bytearray(len(self.px))
        for x in range(w):                                   # vertical
            for c in range(3):
                off = x * 3 + c
                s = sum(src[min(max(i, 0), h - 1) * stride + off]
                        for i in range(-radius, radius + 1))
                for y in range(h):
                    out[y * stride + off] = s // win
                    s += (src[min(y + radius + 1, h - 1) * stride + off]
                          - src[max(y - radius, 0) * stride + off])
        self.px = out

    def png(self) -> bytes:
        """8-bit truecolour RGB, one IDAT. Same encoder as verify_multimodal.py."""
        stride = self.w * 3
        rows = b"".join(b"\x00" + bytes(self.px[y * stride:(y + 1) * stride])
                        for y in range(self.h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", zlib.compress(rows, 9))
                + chunk(b"IEND", b""))


# ------------------------------------------------------------------ parts ---

def _draw_windows(c: _Canvas, n: int) -> None:
    """`n` bright framed rectangles, evenly spaced with wide gaps.

    COUNTABLE means countable: the band is divided into n equal slots and each
    window is inset inside its slot, so even at n=3 there are ~24px of wall
    between frames. Nothing else in the scene is a bright rectangle.
    """
    if n <= 0:
        return
    x0, x1, top, bot = 30, 330, 44, 152
    slot = (x1 - x0) // n
    width = min(84, slot - 24)
    for i in range(n):
        cx = x0 + slot * i + slot // 2
        wx0, wx1 = cx - width // 2, cx + width // 2
        c.rect(wx0, top, wx1, bot, _GLASS)
        c.frame(wx0, top, wx1, bot, _FRAME, t=5)
        mid_y = (top + bot) // 2
        c.rect(wx0, mid_y - 2, wx1, mid_y + 2, _FRAME)          # transom
        c.rect(cx - 2, top, cx + 2, bot, _FRAME)                # mullion


def _draw_cabinets(c: _Canvas, kind: str) -> None:
    """light_modern = pale + handleless; dark_dated = dark brown + handles.

    The handles are the tell. "Handleless" is how modern cabinetry actually
    reads, and it gives a vision model a second, independent cue beyond colour
    — so a mis-extraction here is a real miss, not a lighting artefact.
    """
    if kind == "none":
        return
    x0, x1, y0, y1 = 20, 344, 240, _FLOOR_Y
    if kind == "light_modern":
        body, seam, handle = (228, 224, 216), (203, 198, 190), None
    else:                                    # dark_dated
        body, seam, handle = (84, 56, 36), (58, 38, 24), (206, 202, 194)
    c.rect(x0, y0, x1, y1, body)
    door = 54
    for i in range(1, (x1 - x0) // door + 1):
        c.rect(x0 + i * door - 1, y0, x0 + i * door + 1, y1, seam)
    if handle:
        for i in range((x1 - x0) // door):
            hx = x0 + i * door + door // 2
            c.rect(hx - 11, y0 + 10, hx + 11, y0 + 15, handle)
    c.rect(x0, y1 - 3, x1, y1, seam)                             # plinth shadow


def _draw_countertop(c: _Canvas, kind: str, cabinetry: str, rnd) -> None:
    """stone = speckled grey; laminate = one flat colour. That's the whole test.

    Speckle vs no speckle is the most compressible difference in the set — it
    is exactly the kind of detail that survives pixels and dies in a caption,
    which is why "stone countertops" is one of the demo's headline claims.
    """
    x0, x1, y0, y1 = 14, 350, 226, 240
    if kind == "none":
        if cabinetry != "none":
            c.rect(20, y1 - 4, 344, y1, (150, 146, 140))         # bare top edge
        return
    if kind == "stone":
        c.rect(x0, y0, x1, y1, (152, 152, 156))
        for _ in range((x1 - x0) * (y1 - y0) // 9):              # ~11% speckle
            sx, sy = x0 + rnd(x1 - x0), y0 + rnd(y1 - y0)
            c.rect(sx, sy, sx + 2, sy + 2,
                   (206, 206, 210) if rnd(2) else (108, 108, 114))
    else:                                                        # laminate
        c.rect(x0, y0, x1, y1, (198, 178, 150))
        c.rect(x0, y1 - 3, x1, y1, (168, 148, 122))              # front edge
    if cabinetry == "none":
        for lx in (30, 330):                                     # not floating
            c.rect(lx, y1, lx + 8, _FLOOR_Y, (170, 166, 160))


def _draw_floor(c: _Canvas, kind: str, rnd) -> None:
    """wood = plank lines; tile = a grid; carpet = flat, muted and mottled.

    Three textures that are unmistakable from each other at a glance, which is
    what makes "hardwood floors throughout" a *visible* claim. Carpet is the
    hard one: it is defined by the ABSENCE of planks and grout, so it gets a
    warm tone well away from every wall colour plus a fibrous mottle. A flat
    grey band would read as "some floor" and invite a low-confidence extraction
    on an item that is supposed to be plainly contradicted.
    """
    y0, y1 = _FLOOR_Y, H
    if kind == "wood":
        c.rect(0, y0, W, y1, (152, 112, 72))
        # Long horizontal planks. The end-joints are deliberately LOW contrast
        # and far apart (every 200px, staggered): draw them as boldly as the
        # plank joins and the floor reads as brickwork, which is a different
        # material and would make "hardwood floors throughout" ambiguous.
        for i, y in enumerate(range(y0, y1, 30)):
            c.rect(0, y, W, y + 2, (116, 80, 46))                # plank join
            c.rect(0, y + 2, W, y + 5, (163, 122, 80))           # grain sheen
            for x in range(-i * 70 % 200, W, 200):
                c.rect(x, y + 4, x + 1, min(y + 30, y1), (140, 102, 64))
    elif kind == "tile":
        c.rect(0, y0, W, y1, (202, 200, 194))
        for y in range(y0, y1, 24):
            c.rect(0, y, W, y + 2, (154, 152, 146))
        for x in range(0, W, 40):
            c.rect(x, y0, x + 2, y1, (154, 152, 146))
    else:                                                        # carpet
        base = (139, 122, 104)
        c.rect(0, y0, W, y1, base)
        for by in range(y0, y1, 4):                              # pile mottle
            for bx in range(0, W, 4):
                d = rnd(19) - 9
                c.rect(bx, by, bx + 4, by + 4,
                       tuple(max(0, min(255, v + d)) for v in base))


def _draw_appliance(c: _Canvas, kind: str, x: int) -> None:
    """One appliance per 80px slot, identified by SILHOUETTE, never by text.

    Deliberately no letters on the boxes: a rendered label would turn the
    vision task into OCR and the "did the model see a dishwasher" question into
    "did the model read a word". So:
        fridge      — tall, two doors, a seam and two vertical handles
        oven        — square, four hob circles on top, dark door window
        dishwasher  — square, full-width top handle bar, racks, NO circles
    """
    outline = (110, 112, 116)
    if kind == "fridge":
        y0, y1 = 110, _FLOOR_Y
        c.rect(x, y0, x + 80, y1, (204, 206, 210))
        c.frame(x, y0, x + 80, y1, outline, t=3)
        seam = y0 + 64
        c.rect(x, seam, x + 80, seam + 4, outline)               # freezer split
        c.rect(x + 62, y0 + 18, x + 67, seam - 8, outline)       # two handles
        c.rect(x + 62, seam + 12, x + 67, seam + 62, outline)
    elif kind == "oven":
        y0, y1 = 208, _FLOOR_Y
        c.rect(x, y0, x + 80, y1, (180, 182, 186))
        c.frame(x, y0, x + 80, y1, outline, t=3)
        for bx in (16, 34, 52, 70):                              # 4 hob rings
            c.disc(x + bx, y0 + 13, 6, (58, 58, 62))
        c.rect(x + 8, y0 + 32, x + 72, y1 - 10, (52, 52, 56))    # door glass
        c.rect(x + 8, y0 + 26, x + 72, y0 + 30, outline)         # door handle
    else:                                                        # dishwasher
        y0, y1 = 208, _FLOOR_Y
        c.rect(x, y0, x + 80, y1, (208, 210, 214))
        c.frame(x, y0, x + 80, y1, outline, t=3)
        c.rect(x + 4, y0 + 6, x + 76, y0 + 15, outline)          # bar handle
        c.rect(x + 56, y0 + 20, x + 74, y0 + 28, (58, 58, 62))   # display
        for ry in (44, 62):                                      # rack lines
            c.rect(x + 10, y0 + ry, x + 70, y0 + ry + 3, (168, 170, 174))


def _draw_clutter(c: _Canvas, level: str, rnd) -> None:
    """0 / 2 / 5 small objects for clear / moderate / cluttered.

    All on the open floor, never on the counter or over an appliance: clutter
    must be countable WITHOUT obscuring anything else that is ground truth.
    Otherwise a cluttered scene would also be a partially-occluded scene and
    two variables would move at once.
    """
    n = {"clear": 0, "moderate": 2, "cluttered": 5}[level]
    # Baselines stay >=12px off the bottom edge so nothing reads as clipped by
    # the frame — a half-cropped object is a judgement call about whether to
    # count it, and clutter is supposed to be countable.
    slots = [(80, 404), (210, 390), (335, 406), (466, 396), (588, 402)]
    tints = [(168, 72, 60), (66, 96, 148), (196, 168, 84),
             (108, 132, 96), (150, 96, 148)]
    for i in range(n):
        x, base = slots[i]
        x += rnd(9) - 4
        rgb = tints[i]
        # Darker outline on every object: on a pale tile or carpet floor a flat
        # fill can vanish into the background, and a clutter item you cannot
        # see is a clutter item the extractor cannot count.
        edge = tuple(max(0, v - 45) for v in rgb)
        if i % 3 == 0:                                            # box
            c.rect(x, base - 28, x + 26, base, rgb)
            c.frame(x, base - 28, x + 26, base, edge, t=2)
        elif i % 3 == 1:                                          # bottle
            c.rect(x + 5, base - 34, x + 19, base, rgb)
            c.frame(x + 5, base - 34, x + 19, base, edge, t=2)
            c.rect(x + 9, base - 42, x + 15, base - 34, edge)     # neck
        else:                                                     # bowl
            c.disc(x + 13, base - 11, 13, rgb)
            c.disc(x + 13, base - 13, 8, edge)


# ------------------------------------------------------------------ render ---

def render(scene: Dict[str, Any]) -> bytes:
    """Render one scene to PNG bytes. Deterministic: same scene, same bytes.

    Only `attributes` (plus the light level derived from window_count) drive
    the pixels — with the single exception of `scene_class == "low_quality"`,
    which applies the darken+blur pass. Degradation is keyed off the class
    rather than a separate flag so the two can never disagree.
    """
    attrs = scene["attributes"]
    rnd = _rng(scene["scene_id"])
    light = derive_interpretive(attrs)["natural_light"]

    c = _Canvas(W, H, _WALL_BY_LIGHT[light])
    ceiling = tuple(min(255, v + 12) for v in _WALL_BY_LIGHT[light])
    c.rect(0, 0, W, _WALL_TOP, ceiling)
    c.rect(0, _FLOOR_Y - 6, W, _FLOOR_Y, _BASEBOARD)

    _draw_floor(c, attrs["flooring"], rnd)
    _draw_windows(c, attrs["window_count"])
    _draw_cabinets(c, attrs["cabinetry"])
    _draw_countertop(c, attrs["countertop"], attrs["cabinetry"], rnd)

    # Canonical APPLIANCES order into fixed slots, so the same appliance always
    # lands in the same place and the only thing that varies is presence.
    slots = (356, 456, 556)
    for i, name in enumerate(a for a in APPLIANCES if a in attrs["appliances"]):
        _draw_appliance(c, name, slots[i])

    _draw_clutter(c, attrs["clutter"], rnd)

    if scene["scene_class"] == "low_quality":
        # Underexposed and out of focus, in that order — the same order a phone
        # does it. Tuned by eye against the previews so that the SHAPE of a
        # room survives (there is clearly a wall, a floor, some bright blobs)
        # while every attribute in the vocabulary does not: you cannot name the
        # cabinetry, read the worktop, identify the flooring, or count the
        # windows with any confidence. That is the state that should drive
        # extraction_confidence under CONFIDENCE_FLOOR — unreadable, not black.
        # A black frame would be too easy; a real agent has to decide that a
        # visible-but-illegible photo is still not good enough.
        c.darken(0.22)
        c.blur(11)
        c.blur(11)
    return c.png()


# ==========================================================================
# 3. VERIFICATION
# ==========================================================================

def verify_scenes(*, strict: bool = False) -> List[str]:
    """Check the fixture set against the contract. Returns problems; [] == sound.

    THE POINT of this function: a scene whose class label disagrees with its
    computed ground truth is a silently broken test case. Nothing downstream
    would ever complain — the seeder would happily upload a `contradicted_subtle`
    item with no contradiction in it, the experiment would run, the proxy-judge
    comparison would come out flat, and we would conclude something false about
    the method rather than noticing a bad fixture.

    Every problem is collected rather than raised at the first one: when a
    fixture set is wrong it is usually wrong in several places, and fixing them
    one exception at a time is miserable.

    Returns rather than raises by default — same contract as the contract's own
    `validate_attributes()`, so a verifier can print all the problems as one
    FAIL line. Callers that must not proceed on a broken set (the seeder) pass
    `strict=True` and get an AssertionError listing every problem.
    """
    problems: List[str] = []

    def bad(scene_id: str, msg: str) -> None:
        problems.append(f"{scene_id}: {msg}")

    # --- ids ---------------------------------------------------------------
    seen: Dict[str, int] = {}
    for s in SCENES:
        seen[s["scene_id"]] = seen.get(s["scene_id"], 0) + 1
    for sid, n in seen.items():
        if n > 1:
            problems.append(f"duplicate scene_id {sid!r} ({n} scenes) — dataset "
                            f"item ids are the scene_ids, so this would upsert "
                            f"one item over another")

    # Real catalog ids, not invented ones: retrieve_listing has to find them.
    # Soft-skipped if the catalog cannot be imported so this stays runnable in
    # a bare interpreter.
    try:
        from agent.catalog import LISTINGS
        known = {row["id"] for row in LISTINGS}
    except Exception as e:                                   # pragma: no cover
        known = set()
        problems.append(f"NOTE: could not import agent.catalog ({e}); "
                        f"listing_id existence not checked")

    for s in SCENES:
        sid = s["scene_id"]
        lid = s["listing_id"]
        if known and lid not in known:
            bad(sid, f"listing_id {lid!r} is not in agent/catalog.py")
        if s["scene_class"] not in SCENE_CLASSES:
            bad(sid, f"unknown scene_class {s['scene_class']!r}")

        # --- attributes ----------------------------------------------------
        attrs = s["attributes"]
        for problem in validate_attributes(attrs):
            bad(sid, f"attributes: {problem}")
        missing = [k for k in COUNTABLE_KEYS if k not in attrs]
        if missing:
            # Ground truth must be COMPLETE, not partial: extraction-fidelity
            # compares every countable key, and an absent key would read as an
            # extractor hallucination rather than a gap in the fixture.
            bad(sid, f"attributes missing countable keys {missing}")
        interpretive = [k for k in attrs if k in ("condition", "natural_light")]
        if interpretive:
            bad(sid, f"attributes hand-set interpretive keys {interpretive} — "
                     f"these are derived by the contract, never authored")

        # Structural sanity: a bathroom that "contradicts" a dishwasher claim
        # is a true-but-useless test case, and a living room with a worktop is
        # a render bug waiting to happen.
        room = attrs.get("room_type")
        if room != "kitchen" and attrs.get("appliances"):
            bad(sid, f"{room} scene lists appliances {attrs['appliances']}")
        if room == "living_room" and (attrs.get("cabinetry") != "none"
                                      or attrs.get("countertop") != "none"):
            bad(sid, "living_room scene has cabinetry/countertop")

        # --- claims --------------------------------------------------------
        claims = s["claims"]
        if not claims:
            bad(sid, "no claims — marketing_copy would be empty")
        if len(set(claims)) != len(claims):
            bad(sid, "duplicate claim text")
        for text in claims:
            if text not in CLAIMS_BY_TEXT:
                bad(sid, f"claim {text!r} is not in the contract's CLAIM_SPECS")
        if any(t not in CLAIMS_BY_TEXT for t in claims):
            continue                        # verdicts below would KeyError

        if room != "kitchen":
            for text in claims:
                if text in _APPLIANCE_CLAIMS:
                    bad(sid, f"appliance claim {text!r} on a {room} scene")

        # --- class vs COMPUTED ground truth --------------------------------
        full = {**attrs, **derive_interpretive(attrs)}
        verdicts = {t: expected_verdict(t, full) for t in claims}
        contradicted = [t for t, v in verdicts.items() if v == "contradicted"]
        unverifiable = [t for t, v in verdicts.items() if v == "unverifiable"]
        supported = [t for t, v in verdicts.items() if v == "supported"]
        overall = build_expected_output(claims, attrs)["verdict"]
        klass = s["scene_class"]

        if klass == "supported":
            if contradicted:
                bad(sid, f"class 'supported' but these claims compute as "
                         f"contradicted: {contradicted}")
            if unverifiable:
                bad(sid, f"class 'supported' but carries unverifiable claims "
                         f"{unverifiable} — keep the classes separable")
            if not supported:
                bad(sid, "class 'supported' with nothing supported — the copy "
                         "makes no true claim, so the control tests nothing")
            if overall != "supported":
                bad(sid, f"class 'supported' but overall verdict is {overall!r}")

        elif klass in ("contradicted_visible", "contradicted_subtle"):
            if not contradicted:
                bad(sid, f"class {klass!r} but NO claim computes as contradicted "
                         f"— this item cannot fail the way its class says")
            if overall != "contradicted":
                bad(sid, f"class {klass!r} but overall verdict is {overall!r}")
            difficulties = {CLAIMS_BY_TEXT[t]["difficulty"] for t in contradicted}
            if klass == "contradicted_visible" and "visible" not in difficulties:
                bad(sid, "class 'contradicted_visible' but every contradiction "
                         "is a subtle one")
            if klass == "contradicted_subtle" and difficulties - {"subtle"}:
                loud = [t for t in contradicted
                        if CLAIMS_BY_TEXT[t]["difficulty"] != "subtle"]
                bad(sid, f"class 'contradicted_subtle' but a contradiction is "
                         f"plainly visible: {loud} — this item would not "
                         f"separate the vision judge from the proxy judge")

        elif klass == "unverifiable":
            if len(unverifiable) != len(claims):
                bad(sid, f"class 'unverifiable' but these claims are decidable "
                         f"from the photo: {supported + contradicted}")
            if overall != "unverifiable":
                bad(sid, f"class 'unverifiable' but overall verdict is {overall!r}")

        elif klass == "low_quality":
            if not (supported or contradicted):
                bad(sid, "class 'low_quality' with only unverifiable claims — "
                         "then 'ask for a better photo' is indistinguishable "
                         "from plain abstention and the branch is untested")

    # --- the mix -----------------------------------------------------------
    counts = {k: 0 for k in SCENE_CLASSES}
    for s in SCENES:
        if s["scene_class"] in counts:
            counts[s["scene_class"]] += 1
    for klass, n in counts.items():
        if n == 0:
            problems.append(f"scene_class {klass!r} has no scenes")
    if counts != INTENDED_MIX:
        problems.append(f"class mix {counts} != INTENDED_MIX {INTENDED_MIX} — "
                        f"the mix is the experiment design; change both or "
                        f"neither")

    if problems and strict:
        raise AssertionError("photo_scenes is inconsistent:\n  - "
                             + "\n  - ".join(problems))
    return problems


# ==========================================================================
# 4. PREVIEW — so a human can actually look at the fixtures
# ==========================================================================

def _main() -> int:
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"rendering {len(SCENES)} scenes -> {PREVIEW_DIR}\n")

    widest = max(len(s["scene_id"]) for s in SCENES)
    for s in SCENES:
        png = render(s)
        (PREVIEW_DIR / f"{s['scene_id']}.png").write_bytes(png)
        a = s["attributes"]
        exp = build_expected_output(s["claims"], a)
        print(f"  {s['scene_id']:<{widest}}  {s['scene_class']:<21} "
              f"{len(png):>7} B  win={a['window_count']} "
              f"app={len(a['appliances'])} {a['cabinetry']}/{a['countertop']}/"
              f"{a['flooring']}/{a['clutter']} -> {exp['verdict']}")

    print("\n--- marketing copy + computed verdicts ---")
    for s in SCENES:
        exp = build_expected_output(s["claims"], s["attributes"])
        print(f"\n  {s['scene_id']}  [{s['scene_class']}]  {s['listing_id']}")
        print(f'    "{marketing_copy(s["claims"])}"')
        for claim, verdict in exp["claim_verdicts"].items():
            print(f"      {verdict:<13} {claim}")

    print("\n--- verify_scenes() ---")
    problems = verify_scenes()
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print(f"\n{len(problems)} problem(s). Fix the fixtures, not the checks.")
        return 1
    counts = {k: sum(1 for s in SCENES if s["scene_class"] == k)
              for k in SCENE_CLASSES}
    print(f"  PASS  {len(SCENES)} scenes, mix {counts}")
    print("  PASS  attributes valid, claims in CLAIM_SPECS, ids unique,")
    print("        every class represented, class labels agree with the")
    print("        verdicts computed from the renders' own attributes")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

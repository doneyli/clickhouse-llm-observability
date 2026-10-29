"""
SOP library and retriever.

Retrieval for compliance work is a metadata filter, not a similarity search: the
agent must see EVERY SOP tagged for the claim's product line and governing state
(company-wide SOPs tagged ALL included). Ranking by similarity would quietly drop
a rule that shares no words with the claim.

Release knob (SPEC.md F2): release `claims-1.4.0` reproduces a retriever bug — when a
state-specific SOP matches, it drops the all-state SOPs, so a New York claim never
sees the company contestability, suicide or beneficiary procedures. The trace shows
it: `retrieval-coverage` < 1, and the missed rule was never in the agent's context.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from claims import config

SOP_DIR = Path(__file__).resolve().parent.parent / "data" / "sops"


def _front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    meta: dict = {}
    if not m:
        return meta, text
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        meta[k.strip()] = [x.strip() for x in v[1:-1].split(",")] if v.startswith("[") else v
    return meta, text[m.end():]


@lru_cache(maxsize=1)
def library() -> list[dict]:
    out = []
    for p in sorted(SOP_DIR.glob("SOP-*.md")):
        meta, body = _front_matter(p.read_text())
        out.append({"sop_id": meta["sop_id"], "title": meta["title"], "version": int(meta["version"]),
                    "effective_from": meta["effective_from"], "lines": meta["lines"], "states": meta["states"],
                    "text": body.strip()})
    return out


def get(sop_id: str) -> dict | None:
    return next((s for s in library() if s["sop_id"] == sop_id), None)


def buggy(release: str | None = None) -> bool:
    return (release or config.RELEASE) == "claims-1.4.0"


def retrieve(line: str, state: str, release: str | None = None) -> list[dict]:
    hits = [s for s in library() if line in s["lines"] and ("ALL" in s["states"] or state in s["states"])]
    if buggy(release) and any(state in s["states"] for s in hits):
        hits = [s for s in hits if "ALL" not in s["states"]]  # the 1.4.0 bug
    return hits

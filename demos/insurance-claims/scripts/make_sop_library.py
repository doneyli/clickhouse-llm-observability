"""
Render the SOP library from claims/catalog.py:

  data/sops/SOP-*.md    prose SOPs (what the agent retrieves)
  data/sops/rules.json  structured rules (what the oracle and the evaluators use)

    .venv/bin/python scripts/make_sop_library.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import catalog  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "sops"
LINE_NAMES = {catalog.LIFE: "Individual life", catalog.GROUP: "Group life (employer plans)",
              catalog.ANNUITY: "Deferred annuity death benefits",
              catalog.SERVICE: "Deferred annuities — in-force servicing (withdrawals, surrenders, replacements)"}


def render(s: dict) -> str:
    states = "all states" if s["states"] == ["ALL"] else ", ".join(s["states"])
    lines = "; ".join(LINE_NAMES[x] for x in s["lines"])
    out = [
        "---",
        f"sop_id: {s['sop_id']}",
        f"title: {s['title']}",
        f"version: {s['version']}",
        f"effective_from: {s['effective_from']}",
        f"owner: {s['owner']}",
        f"lines: [{', '.join(s['lines'])}]",
        f"states: [{', '.join(s['states'])}]",
        "---",
        "",
        f"# {s['sop_id']} — {s['title']}",
        "",
        f"Northwind Life & Annuity · Standard Operating Procedure · Version {s['version']}, effective "
        f"{s['effective_from']} · Owner: {s['owner']}",
        "",
        "## 1. Scope and purpose",
        "",
        f"Products: {lines}. Jurisdictions: {states}.",
        "",
        s["purpose"],
        "",
    ]
    for sec in s["sections"]:
        out += [f"## {sec['section']}. {sec['heading']}", ""]
        if sec.get("intro"):
            out += [sec["intro"], ""]
        for r in sec["rules"]:
            if r["text"]:  # an older rule version lives in the revision history, not as its own paragraph
                out += [f"{r['text']} _(Rule version {r['version']}, effective {r['effective_from']}.)_", ""]
    out += ["## Revision history", "", s["history"], ""]
    return "\n".join(out)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("SOP-*.md"):
        old.unlink()
    for s in catalog.SOPS:
        (OUT / f"{s['sop_id']}.md").write_text(render(s))
    rules = [{k: v for k, v in r.items() if k != "text"} for r in catalog.all_rules()]
    (OUT / "rules.json").write_text(json.dumps({
        "note": "Structured rules codified by claims SMEs. Rendered from claims/catalog.py — do not edit. "
                "Never shown to the agent.", "rules": rules}, indent=1) + "\n")
    print(f"{len(catalog.SOPS)} SOPs, {len(rules)} rule versions → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

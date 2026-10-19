"""
Promote or roll back a prompt by moving LABELS, not code. The running agents pick the change up within
their 10 s prompt cache — no redeploy.

    .venv/bin/python scripts/prompt_label.py --show
    .venv/bin/python scripts/prompt_label.py --promote staging      # production → the `staging` version
    .venv/bin/python scripts/prompt_label.py --rollback             # production → previous-production
    .venv/bin/python scripts/prompt_label.py --name claims-adjudication --show

Langfuse's label API is ADDITIVE (a PATCH unions labels) and labels are unique per prompt (assigning one
moves it off the other version). So a promote sets previous-production on the outgoing version FIRST, then
production on the new one — never both on one version, or a later rollback would be a silent no-op.
On self-hosted Enterprise `production` is a protected label: only allowed roles can move it, and every
move lands in the audit log. Every label change also fires the prompt automation → the CI gate.
Exit 1 when nothing changed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claims import config  # noqa: E402


def versions(name: str) -> dict[int, list[str]]:
    out, page = {}, 1
    while True:
        d = config.api("GET", f"/api/public/v2/prompts", params={"name": name, "page": page, "limit": 50})
        for p in d.get("data") or []:
            if p["name"] == name:
                for v in p.get("versions") or []:
                    out.setdefault(v, [])
                for lab in p.get("labels") or []:
                    pass
        if page >= ((d.get("meta") or {}).get("totalPages") or 1):
            break
        page += 1
    # labels per version: read each version
    for v in sorted(out):
        p = config.api("GET", f"/api/public/v2/prompts/{quote(name, safe='')}", params={"version": v})
        out[v] = p.get("labels") or []
    return out


def with_label(vs: dict, label: str):
    return next((v for v, ls in vs.items() if label in ls), None)


def set_label(name: str, version: int, label: str) -> None:
    config.api("PATCH", f"/api/public/v2/prompts/{quote(name, safe='')}/versions/{version}", {"newLabels": [label]})


def show(name: str, vs: dict) -> None:
    print(f"{name}:")
    for v in sorted(vs):
        print(f"  v{v}: {', '.join(vs[v]) or '-'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="claims-sop-rules")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true")
    g.add_argument("--promote", metavar="LABEL")
    g.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    vs = versions(a.name)
    prod = with_label(vs, "production")
    if a.show:
        show(a.name, vs)
        prev = with_label(vs, "previous-production")
        if prev is not None and prev == prod:
            print("  ⚠ production and previous-production are on the same version — a rollback would do nothing")
        return 0
    if a.promote:
        target = with_label(vs, a.promote)
        if target is None or target == prod:
            print(f"nothing to do: label '{a.promote}' is {'missing' if target is None else 'already on production'}")
            return 1
        set_label(a.name, prod, "previous-production")
        set_label(a.name, target, "production")
        print(f"✓ {a.name}: production v{prod} → v{target} (previous-production = v{prod})")
    else:
        prev = with_label(vs, "previous-production")
        if prev is None or prev == prod:
            print("nothing to roll back to (previous-production missing or on the production version)")
            return 1
        # Same order as promote: previous-production first (it moves off `prev`, which we already hold),
        # then production — the end state is identical, and the automation's second event carries
        # `production`, so the gate evaluates the version now serving.
        set_label(a.name, prod, "previous-production")
        set_label(a.name, prev, "production")
        print(f"✓ {a.name}: rolled back production v{prod} → v{prev}")
    show(a.name, versions(a.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())

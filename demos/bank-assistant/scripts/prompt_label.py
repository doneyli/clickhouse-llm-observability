"""Promote or roll back the production prompt by moving LABELS, not code (EXP-04, EXP-05).

  --promote staging     production → the version labelled `staging`
                        (the old production version keeps `previous-production`)
  --rollback            production ↔ previous-production (instant, no redeploy)
  --set-production N    production → version N
  --show                print versions and labels

The running assistant picks the change up within its 10 s prompt cache TTL.
In the bank, `production` is a PROTECTED label: only Admin/Owner can move it,
and every move is recorded in the audit log.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, prompts  # noqa: E402

NAME = prompts.PROMPT_NAME


def versions() -> dict:
    meta = config.api("GET", "/api/public/v2/prompts", params={"name": NAME})["data"]
    out = {}
    for v in (meta[0]["versions"] if meta else []):
        p = config.api("GET", f"/api/public/v2/prompts/{NAME}", params={"version": v})
        out[v] = p.get("labels", [])
    return out


def version_with(label: str, vs: dict):
    return next((v for v, labels in vs.items() if label in labels), None)


def set_labels(version: int, labels: list[str]):
    config.api("PATCH", f"/api/public/v2/prompts/{NAME}/versions/{version}", body={"newLabels": labels})


def show(vs=None):
    vs = vs or versions()
    for v, labels in sorted(vs.items()):
        print(f"  v{v}: {', '.join(l for l in labels if l != 'latest') or '-'}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--promote", metavar="LABEL")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--set-production", type=int)
    g.add_argument("--show", action="store_true")
    a = ap.parse_args()
    vs = versions()
    print("Before:"); show(vs)
    if a.show:
        return
    current = version_with("production", vs)
    if a.promote:
        target = version_with(a.promote, vs)
    elif a.rollback:
        target = version_with("previous-production", vs)
    else:
        target = a.set_production
    if target is None:
        sys.exit("nothing to move: target version not found")
    if target == current:
        print(f"production is already v{current}"); return
    set_labels(target, ["production"])
    if current is not None:
        set_labels(current, ["previous-production"])
    print(f"\n✓ production: v{current} → v{target}   (assistant picks it up within ~10 s)")
    print("After:"); show()
    print(f"Prompt history: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/prompts/{NAME}")


if __name__ == "__main__":
    main()

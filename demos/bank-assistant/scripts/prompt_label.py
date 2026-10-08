"""Promote or roll back the production prompt by moving LABELS, not code (EXP-04, EXP-05).

  --promote staging     production → the version labelled `staging`
                        (the old production version keeps `previous-production`)
  --rollback            production ↔ previous-production (instant, no redeploy);
                        refuses (exit 1) when previous-production is missing or sits
                        on the production version — there is nothing to roll back to
  --set-production N    production → version N
  --set-previous N      previous-production → version N (the rollback target);
                        refuses version N when it is the production version
  --show                print versions and labels, and warn about label problems
                        (production + previous-production on one version, untitled versions)

Exit code 1 means "nothing was changed" (or the labels ended up inconsistent), so the
presenter console never shows a green success for a no-op.

The running assistant picks the change up within its 10 s prompt cache TTL.
In the bank, `production` is a PROTECTED label: only Admin/Owner can move it,
and every move is recorded in the audit log.
"""
import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from northwind import config, prompts  # noqa: E402

NAME = prompts.PROMPT_NAME


# ── Pure label logic (no network — unit-tested in tests/test_scripts_prompt_label.py) ──
# `vs` is {version: [labels]}; `prompts_meta` is {version: {"commitMessage": str | None}}.

def version_with(label: str, vs: dict):
    return next((v for v, labels in vs.items() if label in labels), None)


def rollback_target(vs: dict):
    """Version --rollback would put in production, or None when there is nothing to roll back to
    (no previous-production label, or it sits on the production version itself)."""
    current, previous = version_with("production", vs), version_with("previous-production", vs)
    return previous if previous is not None and previous != current else None


def is_untitled(labels: list, commit_message) -> bool:
    """A version nobody named or labelled — typically a stray 'new version' click in the UI."""
    return set(labels) <= {"latest"} and not (commit_message or "").strip()


def label_anomalies(vs: dict, prompts_meta: dict | None = None) -> list:
    """Human-readable problems with the label layout. Empty list = healthy.
    prompts_meta=None skips the untitled-version check (commit messages unknown)."""
    out = []
    prod, prev = version_with("production", vs), version_with("previous-production", vs)
    if prod is not None and prod == prev:
        out.append(f"production and previous-production are both on v{prod} — --rollback would do nothing; "
                   f"run --set-previous N (or --promote <label> first)")
    if prompts_meta is not None:
        for v, labels in sorted(vs.items()):
            if is_untitled(labels, (prompts_meta.get(v) or {}).get("commitMessage")):
                out.append(f"v{v}: untitled, unlabelled — delete it in the UI")
    return out


# ── Langfuse I/O ──

def fetch_label(label: str, timeout: int = 30):
    """The prompt version carrying `label` (full API payload), or None when no version has it."""
    try:
        return config.api("GET", f"/api/public/v2/prompts/{NAME}", params={"label": label}, timeout=timeout)
    except RuntimeError as e:
        if "404" in str(e):
            return None
        raise


def load():
    """({version: labels}, {version: {"commitMessage": ...}}) for every stored version.
    Versions are fetched in parallel — one round trip of wall time instead of one per version."""
    meta = config.api("GET", "/api/public/v2/prompts", params={"name": NAME})["data"]
    nums = sorted(meta[0]["versions"]) if meta else []

    def one(v):
        return v, config.api("GET", f"/api/public/v2/prompts/{NAME}", params={"version": v})

    with ThreadPoolExecutor(max_workers=6) as ex:
        full = dict(ex.map(one, nums))
    return ({v: p.get("labels", []) for v, p in full.items()},
            {v: {"commitMessage": p.get("commitMessage")} for v, p in full.items()})


def versions() -> dict:
    return load()[0]


def set_labels(version: int, labels: list[str]):
    # Labels are unique across versions: this MOVES each label here from wherever it was.
    config.api("PATCH", f"/api/public/v2/prompts/{NAME}/versions/{version}", body={"newLabels": labels})


def show(vs=None, prompts_meta=None):
    if vs is None:
        vs, prompts_meta = load()
    for v, labels in sorted(vs.items()):
        print(f"  v{v}: {', '.join(l for l in labels if l != 'latest') or '-'}")
    for warning in label_anomalies(vs, prompts_meta):
        print(f"  ⚠ {warning}")


def _v(version) -> str:
    return f"v{version}" if version is not None else "(none)"


def finish(expect_production: int):
    """Re-read the labels after a move, print the result and fail loudly if it is inconsistent."""
    vs, meta = load()
    print("After:"); show(vs, meta)
    prod, prev = version_with("production", vs), version_with("previous-production", vs)
    print(f"production: {_v(prod)} · previous-production: {_v(prev)}")
    if prod != expect_production or (prev is not None and prev == prod):
        print(f"✗ labels are inconsistent (expected production on v{expect_production}, "
              f"previous-production on a different version) — check Prompts → versions & labels")
        sys.exit(1)
    print(f"Prompt history: {config.LANGFUSE_BASE_URL}/project/{config.project_id()}/prompts/{NAME}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--promote", metavar="LABEL")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--set-production", type=int)
    g.add_argument("--set-previous", type=int, metavar="N")
    g.add_argument("--show", action="store_true")
    a = ap.parse_args()
    vs, meta = load()
    print("Before:"); show(vs, meta)
    if a.show:
        return
    current = version_with("production", vs)
    previous = version_with("previous-production", vs)

    if a.set_previous is not None:
        n = a.set_previous
        if n not in vs:
            sys.exit(f"nothing to move: v{n} does not exist")
        if n == current:
            print(f"refusing: v{n} is the production version — previous-production must be a different version")
            sys.exit(1)
        if n == previous:
            print(f"previous-production is already v{n} — nothing changed")
            sys.exit(1)
        set_labels(n, ["previous-production"])
        print(f"\n✓ previous-production: {_v(previous)} → v{n}   (--rollback will now go to v{n})")
        finish(expect_production=current)
        return

    if a.promote:
        target = version_with(a.promote, vs)
    elif a.rollback:
        target = rollback_target(vs)
        if target is None:
            why = (f"previous-production is on the production version (v{current})" if previous is not None
                   else "no version carries previous-production")
            print(f"nothing to roll back to: {why}. "
                  f"Run --promote <label> first, or --set-previous N.")
            sys.exit(1)
    else:
        target = a.set_production
    if target is None:
        sys.exit("nothing to move: target version not found")
    if target not in vs:
        sys.exit(f"nothing to move: v{target} does not exist")
    if target == current:
        print(f"production is already v{current}")
        print("(nothing changed)")
        sys.exit(1)
    set_labels(target, ["production"])
    if current is not None:
        set_labels(current, ["previous-production"])
    print(f"\n✓ production: v{current} → v{target}   (assistant picks it up within ~10 s)")
    finish(expect_production=target)


if __name__ == "__main__":
    main()

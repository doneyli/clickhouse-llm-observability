#!/usr/bin/env python3
"""Render the demo index and the per-demo cards from each demo's demo.yaml.

Writes:
  - the tables in demos/README.md between <!-- demo-index:start --> and
    <!-- demo-index:end --> (one table per profile; archived demos left out)
  - the card in each demo's README.md between <!-- demo-card:start --> and
    <!-- demo-card:end --> (READMEs without the markers are left alone; the
    checker reports them as DS-README-04)

Usage:
  python scripts/gen-demo-index.py            # write
  python scripts/gen-demo-index.py --check    # exit 1 if anything is out of date

Demos whose demo.yaml is missing or invalid are skipped with a warning; run
scripts/check-demo-structure.py for the reasons. See demos/CONTRIBUTING.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_standard as ds  # noqa: E402


def planned_writes(demos_dir: Path) -> tuple[dict[Path, str], list[str]]:
    """Map of file -> desired content for every generated block, plus warnings."""
    demos = ds.discover(demos_dir)
    writes: dict[Path, str] = {}
    warnings: list[str] = []

    for d in demos:
        if not d.valid:
            warnings.append(f"{d.name}: skipped ({'; '.join(d.manifest_errors[:2])})")
            continue
        readme = d.path / "README.md"
        if not readme.is_file():
            continue
        text = readme.read_text()
        new = ds.replace_block(text, ds.CARD_START, ds.CARD_END, ds.render_card(d))
        if new is not None and new != text:
            writes[readme] = new

    index = demos_dir / "README.md"
    if index.is_file():
        text = index.read_text()
        new = ds.replace_block(text, ds.INDEX_START, ds.INDEX_END, ds.render_index(demos))
        if new is None:
            warnings.append(f"{index}: no {ds.INDEX_START} / {ds.INDEX_END} markers")
        elif new != text:
            writes[index] = new
    else:
        warnings.append(f"{index}: missing, so no index was written")
    return writes, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if a generated block is out of date")
    ap.add_argument("--demos-dir", type=Path, default=ds.DEFAULT_DEMOS_DIR)
    args = ap.parse_args()

    writes, warnings = planned_writes(args.demos_dir.resolve())
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(ds.REPO_ROOT))
        except ValueError:
            return str(p)

    if args.check:
        for path in writes:
            print(f"out of date: {rel(path)}")
        if writes:
            print("Run: python scripts/gen-demo-index.py", file=sys.stderr)
            return 1
        print("demo index and cards are up to date")
        return 0

    for path, content in writes.items():
        path.write_text(content)
        print(f"wrote {rel(path)}")
    if not writes:
        print("demo index and cards already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())

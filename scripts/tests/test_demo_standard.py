"""Tests for the demo-standard tooling: manifest schema, checker rules, generator.

No network, no git remote, no services. Run:
  python -m unittest discover -s scripts/tests -v
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import demo_standard as ds  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up while the class is built
    spec.loader.exec_module(mod)
    return mod


checker = _load("check-demo-structure")
gen = _load("gen-demo-index")

MANIFEST = """\
name: good-demo
title: Good Demo
pitch: A fixture demo that follows every rule
profile: standalone
status: active
covers: [trace, evaluate]
stack: [python, langgraph]
langfuse:
  target: cloud
  project: good-demo
length:
  full_min: 10-12
  short_min: 5
commands:
  install: python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
  seed: .venv/bin/python scripts/seed_all.py
  run: .venv/bin/python main.py
  verify: .venv/bin/python scripts/verify_demo.py
  test: .venv/bin/python -m pytest -q
"""

README = """\
# Good Demo — A fixture demo that follows every rule

Fictional company, synthetic data.

**The question it answers:** does the checker pass a conforming demo?

<!-- demo-card:start -->
<!-- demo-card:end -->

- **Present it:** [DEMO_SCRIPT.md](DEMO_SCRIPT.md)
- **Design record:** [SPEC.md](SPEC.md)

## What it demonstrates
## Who it's for
## What runs where
## Set up from scratch

```bash
# Not a heading: comments inside fences are ignored
.venv/bin/python scripts/verify_demo.py
```

## Before a demo
## Run it
## Seeded faults — demo-specific sections are allowed here
## Evaluation
## Configuration
## Layout
## Tests
## Troubleshooting
## Known limits
"""

ACT = """\
## Act {n} · Step {n} ({m} min)

**Frame.** The problem.

**Show.** Click things. See `main.py:2`.

**Land.** The benefit.

**Ask.** A question.

> **Fallback:** do this instead.

---
"""

SCRIPT = """\
# Good Demo — Demo Script

What the audience sees.

- **App:** a CLI
- **Observability:** Cloud, project good-demo
- **Models:** a model
- **Run length:** 12 min; short path is Acts 1–2 (5 min)

> Setup lives in [README.md](README.md).

---

## How to run this script

---

## 0 · Pre-flight

```bash
unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL
.venv/bin/python scripts/verify_demo.py --links
```

---

## What each act proves

---

## Opening · Why (2 min)

---

{acts}
## Close · Wrap (1 min)

---

## Show me the code

**1 · Entry point — `main.py:2`**

---

## Talking points & objections

---

## Known limits

---

## Reset / re-run
"""


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.demos = self.tmp / "demos"
        d = self.demos / "good-demo"
        write(self.demos, "README.md", "# Demos\n\n<!-- demo-index:start -->\n<!-- demo-index:end -->\n")
        write(d, "demo.yaml", MANIFEST)
        write(d, "README.md", README)
        acts = "\n".join(ACT.format(n=n, m=4) for n in (1, 2))
        write(d, "DEMO_SCRIPT.md", SCRIPT.format(acts=acts))
        for f in ("SPEC.md", ".env.example", "main.py", "scripts/seed_all.py", "scripts/verify_demo.py"):
            write(d, f, "x = 1\ny = 2\n")
        write(d, "tests/test_logic.py", "def test_ok():\n    assert True\n")
        self.path = d
        self.regen()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)

    def regen(self) -> None:
        writes, _ = gen.planned_writes(self.demos)
        for p, content in writes.items():
            p.write_text(content)

    def findings(self, strict: bool = False, today: dt.date = dt.date(2026, 11, 27)):
        demo = next(d for d in ds.discover(self.demos) if d.name == "good-demo")
        raw = checker.DemoCheck(demo, self.tmp).run()
        return checker.apply_waivers(demo, raw, strict, today)

    def errors(self, **kw) -> set[str]:
        return {f.rule for f in self.findings(**kw) if f.level == "error"}

    def edit(self, rel: str, old: str, new: str) -> None:
        p = self.path / rel
        text = p.read_text()
        self.assertIn(old, text)
        p.write_text(text.replace(old, new))


class ConformingDemo(Fixture):
    def test_passes_with_no_findings(self):
        self.assertEqual([f for f in self.findings(strict=True) if f.level != "notice"], [])

    def test_index_and_card_are_generated(self):
        index = (self.demos / "README.md").read_text()
        self.assertIn("| **[Good Demo](good-demo/)** |", index)
        self.assertIn("cd demos/good-demo && .venv/bin/python main.py", index)
        card = (self.path / "README.md").read_text()
        self.assertIn("| **Length** | 10–12 min · short path 5 min |", card)
        self.assertIn("| **Stack** | Python · LangGraph |", card)
        self.assertEqual(gen.planned_writes(self.demos)[0], {})


class ManifestRules(Fixture):
    def test_unknown_key_and_bad_enum(self):
        self.edit("demo.yaml", "status: active", "status: live\ncolour: blue")
        errs = ds.validate_manifest(ds.yaml.safe_load((self.path / "demo.yaml").read_text()))
        self.assertTrue(any("unknown key 'colour'" in e for e in errs))
        self.assertTrue(any("'status' is 'live'" in e for e in errs))

    def test_pitch_rules(self):
        self.edit("demo.yaml", "every rule", "every rule.")
        self.assertIn("DS-MANIFEST-01", self.errors())

    def test_covers_order(self):
        self.edit("demo.yaml", "[trace, evaluate]", "[evaluate, trace]")
        self.assertIn("DS-MANIFEST-01", self.errors())

    def test_name_must_match_folder(self):
        self.edit("demo.yaml", "name: good-demo", "name: other")
        self.assertIn("DS-MANIFEST-02", self.errors())

    def test_missing_nested_key(self):
        self.edit("demo.yaml", "  verify: .venv/bin/python scripts/verify_demo.py\n", "")
        self.assertIn("DS-MANIFEST-01", self.errors())


class ReadmeRules(Fixture):
    def test_missing_section(self):
        self.edit("README.md", "## Troubleshooting\n", "")
        self.assertIn("DS-README-02", self.errors())

    def test_evaluation_only_required_when_covered(self):
        self.edit("README.md", "## Evaluation\n", "")
        self.assertIn("DS-README-02", self.errors())
        self.edit("demo.yaml", "[trace, evaluate]", "[trace]")
        self.regen()
        self.assertNotIn("DS-README-02", self.errors())

    def test_out_of_order(self):
        self.edit("README.md", "## Layout\n## Tests\n", "## Tests\n## Layout\n")
        self.assertIn("DS-README-03", self.errors())

    def test_demo_specific_section_outside_its_slot(self):
        self.edit("README.md", "## Known limits\n", "## Known limits\n## Extra notes\n")
        self.assertIn("DS-README-03", self.errors())

    def test_heading_suffix_allowed(self):
        self.edit("README.md", "## Known limits\n", "## Known limits (say them out loud)\n")
        self.assertEqual(self.errors(), set())

    def test_stale_card(self):
        self.edit("demo.yaml", "full_min: 10-12", "full_min: 30")
        self.assertIn("DS-README-04", self.errors())

    def test_missing_script_link_and_broken_link(self):
        self.edit("README.md", "[DEMO_SCRIPT.md](DEMO_SCRIPT.md)", "[nope](NOPE.md)")
        self.assertTrue({"DS-README-05", "DS-LINK-01"} <= self.errors())


class ScriptRules(Fixture):
    def test_run_length(self):
        self.edit("DEMO_SCRIPT.md", "**Run length:**", "**Duration:**")
        self.assertIn("DS-SCRIPT-02", self.errors())

    def test_needs_two_acts(self):
        text = (self.path / "DEMO_SCRIPT.md").read_text()
        cut = text.index("## Act 2")
        (self.path / "DEMO_SCRIPT.md").write_text(text[:cut] + text[text.index("## Close"):])
        self.assertIn("DS-SCRIPT-04", self.errors())

    def test_beats_in_order_and_qualified_labels(self):
        self.edit("DEMO_SCRIPT.md", "**Show.** Click things. See `main.py:2`.\n\n**Land.** The benefit.",
                  "**Land.** The benefit.\n\n**Show.** Click things.")
        self.assertIn("DS-SCRIPT-04", self.errors())

    def test_qualified_label_accepted(self):
        text = (self.path / "DEMO_SCRIPT.md").read_text().replace("**Show.**", "**Show (gate pass).**")
        (self.path / "DEMO_SCRIPT.md").write_text(text)
        self.assertEqual(self.errors(), set())

    def test_preflight_unset_and_verify(self):
        self.edit("DEMO_SCRIPT.md", "unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST LANGFUSE_BASE_URL\n", "")
        self.assertIn("DS-SCRIPT-05", self.errors())

    def test_section_order(self):
        self.edit("DEMO_SCRIPT.md", "## Known limits\n\n---\n\n## Reset / re-run",
                  "## Reset / re-run\n\n---\n\n## Known limits")
        self.assertIn("DS-SCRIPT-03", self.errors())

    def test_citation_past_end_is_warning_only(self):
        self.edit("DEMO_SCRIPT.md", "— `main.py:2`", "— `main.py:99`")
        found = self.findings(strict=True)
        self.assertTrue(any(f.rule == "DS-LINK-02" and f.level == "warning" for f in found))
        self.assertEqual(self.errors(strict=True), set())


class AssetRules(Fixture):
    def test_missing_assets(self):
        for f in (".env.example", "SPEC.md", "scripts/seed_all.py", "scripts/verify_demo.py", "tests/test_logic.py"):
            (self.path / f).unlink()
        self.assertTrue({"DS-ENV-01", "DS-SPEC-01", "DS-SEED-01", "DS-VERIFY-01", "DS-TEST-01"} <= self.errors())

    def test_container_seed_optional_and_paths_from_repo_root(self):
        self.edit("demo.yaml", "profile: standalone", "profile: container")
        self.edit("demo.yaml", "  seed: .venv/bin/python scripts/seed_all.py\n", "")
        self.edit("demo.yaml", "verify: .venv/bin/python scripts/verify_demo.py",
                  "verify: python demos/good-demo/scripts/verify_demo.py")
        self.regen()
        self.assertEqual(self.errors(), set())

    def test_addon_with_custom_files(self):
        self.edit("demo.yaml", "commands:", "addons:\n  - questions: QUESTIONS.md\n  - labs\ncommands:")
        self.assertIn("DS-ADDON-01", self.errors())
        write(self.path, "QUESTIONS.md", "q\n")
        write(self.path, "LABS.md", "l\n")
        self.assertNotIn("DS-ADDON-01", self.errors())

    def test_npm_script_resolution(self):
        self.edit("demo.yaml", "verify: .venv/bin/python scripts/verify_demo.py", "verify: npm run verify")
        write(self.path, "package.json", '{"scripts": {"test": "x"}}')
        self.assertIn("DS-VERIFY-01", self.errors())
        write(self.path, "package.json", '{"scripts": {"verify": "x"}}')
        self.assertNotIn("DS-VERIFY-01", self.errors())


class Waivers(Fixture):
    def setUp(self) -> None:
        super().setUp()
        (self.path / "SPEC.md").unlink()
        self.edit("README.md", "- **Design record:** [SPEC.md](SPEC.md)\n", "")

    def test_waiver_turns_failure_into_notice(self):
        self.edit("demo.yaml", "commands:", "waivers:\n  - rule: DS-SPEC-01\n    reason: later\ncommands:")
        self.assertEqual(self.errors(), set())
        self.assertIn("DS-SPEC-01", self.errors(strict=True))

    def test_expired_waiver_fails(self):
        self.edit("demo.yaml", "commands:",
                  "waivers:\n  - rule: DS-SPEC-01\n    reason: later\n    until: 2026-01-01\ncommands:")
        self.assertIn("DS-SPEC-01", self.errors())

    def test_unused_waiver_warns(self):
        self.edit("demo.yaml", "commands:",
                  "waivers:\n  - rule: DS-SPEC-01\n    reason: later\n  - rule: DS-ENV-01\n    reason: x\ncommands:")
        self.assertTrue(any(f.rule == "DS-ENV-01" and f.level == "warning" for f in self.findings()))

    def test_write_waivers_bootstraps(self):
        demo = next(d for d in ds.discover(self.demos) if d.name == "good-demo")
        added = checker.write_waivers(demo, checker.DemoCheck(demo, self.tmp).run())
        self.assertEqual(added, ["DS-SPEC-01"])
        self.assertEqual(self.errors(), set())
        self.assertIn(checker.BOOTSTRAP_REASON, (self.path / "demo.yaml").read_text())


class Index(Fixture):
    def test_archived_left_out_and_draft_tagged(self):
        self.edit("demo.yaml", "status: active", "status: draft")
        self.regen()
        self.assertIn("*(draft)*", (self.demos / "README.md").read_text())
        self.edit("demo.yaml", "status: draft", "status: archived")
        self.regen()
        self.assertNotIn("good-demo", (self.demos / "README.md").read_text())

    def test_check_index_detects_drift(self):
        self.edit("demo.yaml", "title: Good Demo", "title: Renamed Demo")
        self.assertTrue(checker.check_index(self.demos, ds.discover(self.demos), self.tmp))

    def test_underscore_folders_are_not_demos(self):
        write(self.demos, "_template/README.md", "x")
        self.assertNotIn("_template", [d.name for d in ds.discover(self.demos)])


if __name__ == "__main__":
    unittest.main()

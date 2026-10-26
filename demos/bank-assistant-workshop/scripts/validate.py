#!/usr/bin/env python3
"""Validate package content and checkpoint starting states without live credentials."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import checkpoint


def validate():
    manifest = json.loads((ROOT / "SOURCE_MANIFEST.json").read_text())
    for relative, expected in manifest["bank_source"]["original_file_sha256"].items():
        assert hashlib.sha256((ROOT / "original-demo" / relative).read_bytes()).hexdigest() == expected, relative
    print(f"Source snapshot: {len(manifest['bank_source']['original_file_sha256'])} hashes match.")
    for directory in [ROOT / "src", ROOT / "scripts", ROOT / "tests"]:
        for path in directory.rglob("*.py"):
            ast.parse(path.read_text(), filename=str(path))
    ast.parse((ROOT / "workshop.py").read_text())
    print("Python source syntax: passed.")
    for name in checkpoint.NAMES:
        for audience in ["learner", "instructor"]:
            path = ROOT / "docs" / audience / f"{name}.md"
            content = path.read_text()
            assert "## Review Before Sending" in content
            assert content.rstrip().endswith("the assessment block is labeled as such.")
    documents = [ROOT / "README.md", ROOT / "ASSESSMENT.md", ROOT / "VALIDATION.md"] + list((ROOT / "docs").rglob("*.md"))
    for path in documents:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if re.match(r"[a-zA-Z]+:", target) or target.startswith("#"):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
    print("Nine learner/instructor pairs and local documentation links: passed.")
    data = json.loads((ROOT / "data/seed-dataset.json").read_text())
    assert len(data) == len({item["id"] for item in data}) == 8
    from northwind import knowledge, agent
    for item in data:
        assert item["expected_output"] and item["metadata"]["synthetic"] is True
        for identifier in item["metadata"].get("expected_sources", []):
            assert knowledge.get(identifier) is not None, identifier
        if item["metadata"].get("expect_blocked"):
            assert agent.assess_input(item["input"]["question"], item["input"]["customer_id"])["blocked"]
    print("Eight synthetic dataset items, source IDs and blocked-request expectations: passed.")
    with tempfile.TemporaryDirectory(prefix="bank-workshop-checkpoints-") as tmp:
        folders = []
        for name in checkpoint.NAMES:
            folder = Path(tmp) / name
            checkpoint.build(name, folder)
            folders.append(folder)
            assert not (folder / ".env").exists()
            completed = subprocess.run([sys.executable, str(folder / "workshop.py"), "preflight", "--offline"],
                                       cwd=folder, capture_output=True, text=True)
            assert completed.returncode == 0, completed.stderr
        for module in ("tracing.py", "prompt_source.py", "monitoring.py", "experiment_hooks.py"):
            assert (folders[0]/"src/northwind"/module).read_bytes() == (folders[1]/"src/northwind"/module).read_bytes()
        assert "ENABLED = False" in (folders[2]/"src/northwind/tracing.py").read_text()
        assert "ENABLED = True" in (folders[3]/"src/northwind/tracing.py").read_text()
        assert "NotImplementedError" in (folders[6]/"src/northwind/experiment_hooks.py").read_text()
        assert "async def task" in (folders[7]/"src/northwind/experiment_hooks.py").read_text()
    print("All nine starting states: assembled and offline preflight passed.")
    print("No live authentication, model call, tracing export or Cloud mutation was performed.")


if __name__ == "__main__":
    validate()

#!/usr/bin/env python3
"""Create a credential-free exercise starting state. Existing destinations are refused."""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["00-setup", "01-base-app", "02-tracing", "03-prompt-management", "04-monitoring",
         "05-dataset", "06-experiments", "07-evaluation", "08-wrap-up"]
TRACING_START = '''"""Lesson 02 starter: add callback tracing and attribute propagation."""
from contextlib import contextmanager
ENABLED = False  # TODO 02: enable the Langfuse client.

def callbacks():
    # TODO 02: return [CallbackHandler()] from langfuse.langchain.
    return []

@contextmanager
def attributes(**kwargs):
    # TODO 02: wrap yield with langfuse.propagate_attributes(**kwargs).
    yield
'''
PROMPT_START = '''"""Lesson 03 starter: this app still serves its local prompt."""
def resolve(client, name, fallback, *, label, version=None):
    # TODO 03: call client.get_prompt; see the learner lesson.
    from northwind.config import LocalPrompt
    return LocalPrompt(fallback)
'''
MONITOR_START = '''"""Lesson 04 starter: no application signal scores yet."""
def record(client, *, trace_id, observation_id, check, history, message):
    # TODO 04: attach named BOOLEAN signals to this observation.
    pass
'''
EXPERIMENT_START = '''"""Lesson 06 starter: connect a dataset task to the real app."""
def make_task(*, prompt_version, model, prompt_label=None):
    # TODO 06: return an async task that invokes agent.run_turn for item.input.
    raise NotImplementedError("Complete lesson 06 make_task before running experiments.")

def evaluators(include_judge=True):
    # TODO 06: add deterministic and correctness callback evaluators.
    return []
'''


def build(name, output):
    if (ROOT / "CHECKPOINT.json").exists():
        raise SystemExit("Generate new checkpoints from the package's complete reference root, not an exercise folder.")
    if output.exists():
        raise SystemExit(f"Destination already exists: {output}. Choose a new folder; learner edits are never overwritten.")
    output.mkdir(parents=True)
    for relative in ["src", "scripts", "data", "docs", "tests"]:
        shutil.copytree(ROOT / relative, output / relative, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for relative in ["README.md", "ASSESSMENT.md", "VALIDATION.md", "SOURCE_MANIFEST.json", "NOTICE", "LICENSE",
                     "requirements.in", "requirements.txt", ".env.example", ".gitignore", "workshop.py"]:
        shutil.copy2(ROOT / relative, output / relative)
    readme = output / "README.md"
    readme.write_text(readme.read_text().replace("(original-demo/README.md)",
        "(https://github.com/doneyli/clickhouse-llm-observability/blob/6af4e7b0d7f87f888860e57f71614af207be62ac/demos/bank-assistant/README.md)"))
    index = NAMES.index(name)
    seam = output / "src/northwind"
    if index <= 2:
        (seam / "tracing.py").write_text(TRACING_START)
    if index <= 3:
        (seam / "prompt_source.py").write_text(PROMPT_START)
    if index <= 4:
        (seam / "monitoring.py").write_text(MONITOR_START)
    if index <= 6:
        (seam / "experiment_hooks.py").write_text(EXPERIMENT_START)
    (output / "CHECKPOINT.json").write_text(json.dumps({"checkpoint": name, "state": "before exercise",
        "tracing": index > 2, "managed_prompts": index > 3, "signals": index > 4, "experiments": index > 6}, indent=2)+"\n")
    print(f"Created {output}; restart both app processes after changing checkpoints.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", choices=NAMES, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build-all", action="store_true")
    args = parser.parse_args()
    if args.build_all:
        destination = args.output or ROOT / "checkpoints"
        for name in NAMES:
            build(name, destination / name)
    elif args.checkpoint:
        build(args.checkpoint, args.output or ROOT / "work" / args.checkpoint)
    else:
        print("\n".join(NAMES))


if __name__ == "__main__":
    main()

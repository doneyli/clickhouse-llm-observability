"""The root setup.sh's cluster-health step, run for real in bash with everything it calls
stubbed: `docker` and the judge seeder are fakes that log their arguments and return a
scripted exit code, and header/success/warn just print. No Docker, no setup, no network.

What it pins down: the container half runs `seed_all.py --skip-judges` (the judges cannot be
written from the container), the host half runs this demo's seed_evaluators.sh, both halves
run even when one fails, and any failure is a warning that leaves setup going, under the
`set -e` setup.sh runs with."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
ROOT = DEMO.parent.parent
sys.path.insert(0, str(DEMO / "scripts"))

import seed_all as sa  # noqa: E402

SETUP = ROOT / "setup.sh"
CONTAINER_SEED = ("compose --profile langfuse --profile demo run --rm cluster-health "
                  "python scripts/seed_all.py --skip-judges")


def setup_function_source() -> str:
    """ensure_cluster_health_seed() exactly as setup.sh defines it."""
    m = re.search(r"^ensure_cluster_health_seed\(\) \{\n.*?^\}\n", SETUP.read_text(), re.M | re.S)
    assert m, "ensure_cluster_health_seed() not found in setup.sh"
    return m.group(0)


def run_step(tmp_path: Path, docker_rc: int = 0, eval_rc: int = 0, with_demo: bool = True):
    """Run ensure_cluster_health_seed with SCRIPT_DIR pointed at a fake repo root."""
    log = tmp_path / "calls.log"
    root = tmp_path / "root"
    if with_demo:
        scripts = root / "demos" / "cluster-health-investigator" / "scripts"
        scripts.mkdir(parents=True)
        evaluators = scripts / "seed_evaluators.sh"
        evaluators.write_text(f'#!/usr/bin/env bash\necho "seed_evaluators $*" >> "{log}"\nexit {eval_rc}\n')
        evaluators.chmod(0o755)
    else:
        root.mkdir()
    script = "\n".join([
        "set -e",
        f'SCRIPT_DIR="{root}"',
        f'docker() {{ echo "docker $*" >> "{log}"; return {docker_rc}; }}',
        'header() { echo "HEADER $*"; }',
        'success() { echo "SUCCESS $*"; }',
        'warn() { echo "WARN $*"; }',
        setup_function_source(),
        "ensure_cluster_health_seed",
        'echo "SETUP CONTINUES"',
    ])
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30,
                         env={"PATH": os.environ.get("PATH", "")})
    calls = log.read_text().splitlines() if log.exists() else []
    return out, calls


def test_success_runs_the_container_seed_without_judges_then_the_host_judge_seeder(tmp_path):
    out, calls = run_step(tmp_path)
    assert out.returncode == 0, out.stderr
    assert calls == [f"docker {CONTAINER_SEED}", "seed_evaluators "]
    assert "SUCCESS Cluster-health prompts, datasets, and judges seeded" in out.stdout
    assert "WARN" not in out.stdout and "SETUP CONTINUES" in out.stdout


@pytest.mark.parametrize("docker_rc,eval_rc", [(1, 0), (0, 1), (1, 1)])
def test_a_failing_half_is_a_warning_and_setup_continues(tmp_path, docker_rc, eval_rc):
    out, calls = run_step(tmp_path, docker_rc=docker_rc, eval_rc=eval_rc)
    assert out.returncode == 0, out.stderr
    assert calls == [f"docker {CONTAINER_SEED}", "seed_evaluators "]  # both halves always run
    assert "SUCCESS" not in out.stdout
    assert "WARN Cluster-health seeding incomplete — run on the host: " \
           "python demos/cluster-health-investigator/scripts/seed_all.py" in out.stdout
    assert "SETUP CONTINUES" in out.stdout


def test_no_demo_folder_is_a_no_op(tmp_path):
    out, calls = run_step(tmp_path, with_demo=False)
    assert out.returncode == 0 and calls == [] and "HEADER" not in out.stdout
    assert "SETUP CONTINUES" in out.stdout


def test_setup_calls_the_step_and_what_it_calls_exists():
    text = SETUP.read_text()
    assert re.search(r"^\s+ensure_cluster_health_seed\s*$", text, re.M), "setup.sh never calls the step"
    evaluators = DEMO / "scripts" / "seed_evaluators.sh"
    assert evaluators.is_file() and os.access(evaluators, os.X_OK)
    assert (DEMO / "scripts" / "seed_all.py").is_file()


def test_the_container_half_seeds_everything_but_the_judges():
    """`seed_all.py --skip-judges` in the container plans prompts and datasets only, and
    seed_evaluators.sh, the step setup.sh runs on the host, is the judge step it skips."""
    steps = sa.plan_steps(container=True, with_traces=False, skip_judges=True)
    planned = [s for s in steps if s[1] is not None]
    assert [os.path.basename(s[1][-1]) for s in planned] == ["seed_prompts.py", "seed_datasets.py"]
    host = sa.plan_steps(container=False, with_traces=False, skip_judges=False)
    assert os.path.basename(host[-1][1][-1]) == "seed_evaluators.sh"

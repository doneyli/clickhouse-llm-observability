#!/usr/bin/env python3
"""The one seed entry point for langfuse-enterprise-operations. Idempotent; safe to re-run.

    python3 scripts/seed_all.py                  # prepare private/, then run the governance workflow
    python3 scripts/seed_all.py --governance-name "Governance Demo"

Most of this demo is created live, from the portal, in front of the audience. Seeding
prepares only what is slow or can be blocked by instance policy, so a failure shows up the
day before rather than mid-demo:

1. `private/` exists with mode 0700, and `private/org.env` exists with mode 0600. A missing
   `org.env` is copied from `org.env.example` and the run stops until you fill it in.
2. The organization key lists its organization's projects (HTTP 200).
3. `scripts/protected-labels.sh` runs once. It is the portal's own Protected labels
   workflow: a dedicated governance project, real Member and Admin sign-in personas, two
   versions of `governance-demo/release`, the protected `production` label, and the full
   Member-denied / Admin-promotes proof. On a re-run it reuses the recorded project and
   personas and repeats the proof. The Audit evidence act needs the same project.

It prints what was created, updated and left unchanged, and exits non-zero on failure.
Credentials are only ever passed as file paths (to the CLI and to Node), never read here.
Shell-exported LANGFUSE_* keys are stripped from the child process.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEMO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEMO_DIR))
sys.path.insert(0, str(DEMO_DIR / "scripts"))
import workflows as wf  # noqa: E402
from verify_demo import GOVERNANCE_PROMPT, child_env, cli  # noqa: E402

DEFAULT_GOVERNANCE_NAME = "Governance Demo"  # the portal form's default (public/index.html)
Runner = Callable[..., Any]


def governance_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    """What the governance workflow has recorded so far, for the created/unchanged report."""
    governance = state.get("governance") or {}
    project_id = governance.get("projectId")
    app = (state.get("applications") or {}).get(project_id or "") or {}
    return {
        "projectId": project_id,
        "projectName": app.get("name"),
        "personas": sorted(r for r, p in (governance.get("personas") or {}).items() if p.get("registered")),
        "versions": [v.get("version") for v in governance.get("versions") or []],
    }


def report(before: Dict[str, Any], after: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(created | unchanged | updated, what) for each seeded governance artifact."""
    out = []
    out.append(("unchanged" if before["projectId"] and before["projectId"] == after["projectId"] else "created",
                f"governance project {after['projectName']} ({after['projectId']})"))
    for role in after["personas"]:
        out.append(("unchanged" if role in before["personas"] else "created", f"native {role.title()} sign-in persona"))
    for version in after["versions"]:
        out.append(("unchanged" if version in before["versions"] else "created", f"prompt {GOVERNANCE_PROMPT} v{version}"))
    if after["versions"]:
        out.append(("updated", f"production label: reset to v{after['versions'][0]}, Member denied, Admin promoted "
                               f"v{after['versions'][-1]} (repeated on every run)"))
    return out


def governance_name(state: Dict[str, Any], requested: Optional[str]) -> str:
    """Reuse the recorded project's name: the workflow refuses a different one until Reset."""
    recorded = governance_snapshot(state)["projectName"]
    return requested or recorded or (state.get("governance") or {}).get("pendingName") or DEFAULT_GOVERNANCE_NAME


def event_line(event: Dict[str, Any]) -> Optional[str]:
    """One printable line per workflow event, or None for events not worth a line."""
    kind = event.get("type")
    if kind == "request":
        return f"    -> {event.get('label')}  [{event.get('method')} {event.get('path')}]"
    if kind == "response":
        return f"       {'ok' if event.get('ok') else 'FAILED'} HTTP {event.get('status')}"
    if kind == "note":
        return f"    note: {event.get('label')}"
    if kind == "governance":
        return "\n".join("    check: " + c for c in event.get("checks", []))
    if kind == "error":
        return f"    error: {event.get('message')}"
    return None


def ensure_private(state_dir: Path, org_env: Path) -> Tuple[List[Tuple[str, str]], bool]:
    """Create or tighten private/ and org.env. Returns (lines, org.env is usable)."""
    lines = []
    if state_dir.is_dir():
        if state_dir.stat().st_mode & 0o077:
            state_dir.chmod(0o700)
            lines.append(("updated", f"{state_dir} mode set to 0700"))
        else:
            lines.append(("unchanged", f"{state_dir} (mode 0700)"))
    else:
        state_dir.mkdir(mode=0o700, parents=True)
        lines.append(("created", f"{state_dir} (mode 0700)"))
    if not org_env.is_file():
        org_env.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(DEMO_DIR / "org.env.example", org_env)
        org_env.chmod(0o600)
        lines.append(("created", f"{org_env} from org.env.example: fill in an organization key, then re-run"))
        return lines, False
    if org_env.stat().st_mode & 0o077:
        org_env.chmod(0o600)
        lines.append(("updated", f"{org_env} mode set to 0600"))
    else:
        lines.append(("unchanged", f"{org_env} (mode 0600)"))
    return lines, True


def run_governance(state_dir: Path, org_env: Path, name: str, runner: Runner = subprocess.Popen) -> Tuple[bool, str]:
    """Run scripts/protected-labels.sh as the portal would, streaming its events. Returns (ok, message)."""
    run_id = "seed-" + uuid.uuid4().hex[:12]
    params = state_dir / "requests" / (run_id + ".json")
    wf.write_json(params, {"governanceName": name})
    command = [str(DEMO_DIR / "scripts" / "protected-labels.sh"), "--params-file", str(params),
               "--state-dir", str(state_dir), "--org-env", str(org_env), "--run-id", run_id]
    result, message = False, "the workflow ended without a result"
    process = runner(command, cwd=str(DEMO_DIR), stdout=subprocess.PIPE, text=True, env=child_env())
    for raw in process.stdout:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        line = event_line(event)
        if line:
            print(line, flush=True)
        if event.get("type") == "complete":
            result, message = True, event.get("result", {}).get("message", "complete")
        elif event.get("type") == "error":
            message = event.get("message") or "workflow failed"
            if "Resource temporarily unavailable" in message:
                message += " (another workflow holds private/workflow.lock: wait for the portal run to finish)"
    return process.wait() == 0 and result, message


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", default=str(DEMO_DIR / "private"), help="the portal's --state-dir (default: private/)")
    ap.add_argument("--org-env", help="the portal's --org-env (default: <state-dir>/org.env)")
    ap.add_argument("--governance-name", help=f"governance project name (default: the recorded one, else '{DEFAULT_GOVERNANCE_NAME}')")
    args = ap.parse_args(argv)
    os.umask(0o077)
    state_dir = Path(args.state_dir).resolve()
    org_env = Path(args.org_env).resolve() if args.org_env else state_dir / "org.env"

    print("langfuse-enterprise-operations seed")
    lines, usable = ensure_private(state_dir, org_env)
    for status, what in lines:
        print(f"  {status:<9} {what}")
    if not usable:
        return 1

    status, body = cli(org_env, "organizations", "list-projects")
    if status != 200:
        print(f"  FAILED    organization key check: {status} {wf.scrub(body) if status is None else ''}".rstrip())
        print("            401: wrong or revoked key; 403: a project key, use an organization key")
        return 1
    print(f"  unchanged organization key lists {len((body or {}).get('projects') or [])} project(s)")

    before = governance_snapshot(wf.load_state(state_dir))
    name = governance_name(wf.load_state(state_dir), args.governance_name)
    print(f"  running   Protected labels workflow for '{name}' (scripts/protected-labels.sh)")
    ok, message = run_governance(state_dir, org_env, name)
    if not ok:
        print(f"  FAILED    {message}")
        return 1
    for status, what in report(before, governance_snapshot(wf.load_state(state_dir))):
        print(f"  {status:<9} {what}")
    print(f"\nSeeded. {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

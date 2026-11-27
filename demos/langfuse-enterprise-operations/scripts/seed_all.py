#!/usr/bin/env python3
"""The one seed entry point for langfuse-enterprise-operations. Idempotent; safe to re-run.

    python3 scripts/seed_all.py              # prepare private/, reconcile the governance project
    python3 scripts/seed_all.py --reprove    # also repeat the Protected labels proof when nothing drifted

Most of this demo is created live, from the portal, in front of the audience. Seeding
prepares only what is slow or can be blocked by instance policy, so a failure shows up the
day before rather than mid-demo:

1. `private/` exists with mode 0700, and `private/org.env` exists with mode 0600 and sets
   both key names. A missing `org.env` is copied from `org.env.example` and the run stops
   until you fill it in.
2. The organization key lists its organization's projects (HTTP 200).
3. The governance prerequisites of Acts 4 and 5 are read back live, with the same checks
   `verify_demo.py` runs: the project and its ownership marker, both persona sign-ins and
   project roles, the protected-label policy, and both governed prompt versions against
   their definitions in `workflows.py`. When all of them pass, nothing is written. When any
   fails, `scripts/protected-labels.sh` (the portal's own Protected labels workflow) runs:
   it creates what is missing, replaces a prompt version whose definition drifted, and
   repeats the Member-denied / Admin-promotes proof. The checks then run again and must pass.

It prints what was created, updated and left unchanged, and exits non-zero on any failure,
including a partial workflow run. Credentials are only ever passed as file paths (to the
CLI and to Node), never read here. Shell-exported LANGFUSE_* keys are cleared first.
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
import verify_demo as v  # noqa: E402

DEFAULT_GOVERNANCE_NAME = "Governance Demo"  # the portal form's default (public/index.html)
Runner = Callable[..., Any]


def governance_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    """What the governance workflow has recorded, for the created/updated/unchanged report."""
    governance = state.get("governance") or {}
    project_id = governance.get("projectId")
    app = (state.get("applications") or {}).get(project_id or "") or {}
    return {
        "projectId": project_id,
        "projectName": app.get("name"),
        "personas": {r: p.get("email") for r, p in (governance.get("personas") or {}).items() if p.get("registered")},
        "versions": [(x.get("id"), x.get("version")) for x in governance.get("versions") or []],
    }


def report(before: Dict[str, Any], after: Dict[str, Any], proof_ran: bool) -> List[Tuple[str, str]]:
    """(created | updated | unchanged, what) per seeded governance artifact, by identity, not by count."""
    out: List[Tuple[str, str]] = []
    if after["projectId"]:
        out.append(("unchanged" if before["projectId"] == after["projectId"] else "created",
                    f"governance project {after['projectName']} ({after['projectId']})"))
    for role in ("ADMIN", "MEMBER"):
        email = after["personas"].get(role)
        if email:
            out.append(("unchanged" if before["personas"].get(role) == email else "created",
                        f"native {role.title()} sign-in persona"))
    for slot, (prompt_id, version) in enumerate(after["versions"], start=1):
        previous = before["versions"][slot - 1] if len(before["versions"]) >= slot else None
        if previous is None:
            out.append(("created", f"prompt {v.GOVERNANCE_PROMPT} slot {slot}: v{version}"))
        elif previous == (prompt_id, version):
            out.append(("unchanged", f"prompt {v.GOVERNANCE_PROMPT} slot {slot}: v{version}"))
        else:
            out.append(("updated", f"prompt {v.GOVERNANCE_PROMPT} slot {slot}: v{previous[1]} -> v{version} (definition drifted)"))
    if proof_ran and after["versions"]:
        out.append(("updated", f"production label: reset to v{after['versions'][0][1]}, Member denied, Admin promoted "
                               f"v{after['versions'][-1][1]} (the proof moves labels and adds audit records)"))
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
    """Create or tighten private/ and org.env. Returns (lines, org.env exists and can be checked)."""
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
    try:
        process = runner(command, cwd=str(DEMO_DIR), stdout=subprocess.PIPE, text=True, env=v.child_env())
    except OSError as error:
        return False, f"could not start scripts/protected-labels.sh: {error}"
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
            result, message = False, event.get("message") or "workflow failed"
            if "Resource temporarily unavailable" in message:
                message += " (another workflow holds private/workflow.lock: wait for the portal run to finish)"
    code = process.wait()
    if code != 0 and result:
        result, message = False, f"workflow exited {code} after reporting completion"
    return result, message


def failing(checks: List[v.Check]) -> List[str]:
    return [what for ok, what, required, _ in checks if required and not ok]


def main(argv: Optional[List[str]] = None, cli_runner: Runner = subprocess.run, popen: Runner = subprocess.Popen) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", default=str(DEMO_DIR / "private"), help="the portal's --state-dir (default: private/)")
    ap.add_argument("--org-env", help="the portal's --org-env (default: <state-dir>/org.env)")
    ap.add_argument("--governance-name", help=f"governance project name (default: the recorded one, else '{DEFAULT_GOVERNANCE_NAME}')")
    ap.add_argument("--reprove", action="store_true",
                    help="run the Protected labels proof even when nothing drifted (moves labels, adds audit records)")
    args = ap.parse_args(argv)
    os.umask(0o077)
    v.clear_shell_keys()
    state_dir = Path(args.state_dir).resolve()
    org_env = Path(args.org_env).resolve() if args.org_env else state_dir / "org.env"

    print("langfuse-enterprise-operations seed")
    lines, present = ensure_private(state_dir, org_env)
    for status, what in lines:
        print(f"  {status:<9} {what}")
    if not present:
        return 1
    ok, what, _, hint = v.env_check(str(org_env), ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"), "organization key", cli_runner)
    if not ok:
        print(f"  FAILED    {what}  ->  {hint}")
        return 1

    projects, detail = v.organization_projects(org_env, cli_runner)
    if projects is None:
        print(f"  FAILED    organization key check: {detail}")
        print("            401: wrong or revoked key; 403: a project key, use an organization key")
        return 1
    print(f"  unchanged organization key lists {len(projects)} project(s)")

    state = wf.load_state(state_dir)
    before = governance_snapshot(state)
    drift = failing(v.governance_checks(state, projects, org_env, cli_runner))
    if not drift and not args.reprove:
        for status, what in report(before, before, proof_ran=False):
            print(f"  {status:<9} {what}")
        print("\nSeeded. Governance read back unchanged; nothing written. --reprove repeats the live proof.")
        return 0
    for what in drift:
        print(f"  missing   {what}")

    name = governance_name(state, args.governance_name)
    print(f"  running   Protected labels workflow for '{name}' (scripts/protected-labels.sh)")
    ok, message = run_governance(state_dir, org_env, name, popen)
    after_state = wf.load_state(state_dir)
    for status, what in report(before, governance_snapshot(after_state), proof_ran=ok):
        print(f"  {status:<9} {what}")
    if not ok:
        print(f"  FAILED    {message}  (partial results above are kept for the next run)")
        return 1
    projects, detail = v.organization_projects(org_env, cli_runner)
    remaining = failing(v.governance_checks(after_state, projects or {}, org_env, cli_runner)) if projects is not None \
        else [f"organization inventory unreadable after the run ({detail})"]
    if remaining:
        for what in remaining:
            print(f"  FAILED    {what}")
        return 1
    print(f"\nSeeded. {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
